"""QuantTutorEnv — true gym environment for QuantTutorBench.

The agent controls the loop. The environment exposes:
    reset()        → Observation  (student opening + tools)
    call_tool()    → str          (tool result, no conversation advance)
    send_message() → Observation  (student response + done flag)
    evaluate()     → Scores       (post-hoc evaluation)
    close()        → None         (cleanup)

Usage::

    env = QuantTutorEnv(use_docker=True)
    obs = env.reset("S01_ma_crossover")

    while not obs.done:
        # Agent decides: call tools or reply to student
        result = env.call_tool("fetch_market_data", symbol="AAPL")
        obs = env.send_message("Here's what I found about AAPL...")

    scores = env.evaluate()
    env.close()
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Ensure bench/ is on sys.path for orchestrator imports
_BENCH_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_BENCH_ROOT))

from bench.gym.types import Observation, Scores

# Heavy imports (orchestrator, config) are deferred to method bodies
# so that `from bench.gym.types import Observation` works without deepeval.


class QuantTutorEnv:
    """Gym-style environment for QuantTutorBench.

    The agent is completely external. It interacts with this environment
    through reset/call_tool/send_message/evaluate. The environment owns:
    - Docker sandbox (container lifecycle, file system)
    - MCP tool proxy (tool registration, logging, distractor sampling)
    - Student simulator (LLM-driven, persona-aware)
    - TC checker (incremental termination criteria tracking)
    - Evaluation chain (QR + QP + Tutor 7D scoring)
    """

    def __init__(
        self,
        bench_root: Optional[str] = None,
        use_docker: bool = True,
        eval_model: Optional[str] = None,
        simulator_model: str = "openai/gpt-5.2",
        tc_checker_model: str = "anthropic/claude-sonnet-4-6",
    ):
        self.bench_root = Path(bench_root or _BENCH_ROOT)
        self.use_docker = use_docker
        self.eval_model = eval_model
        self.simulator_model = simulator_model
        self.tc_checker_model = tc_checker_model

        # State (populated by reset, cleared by close)
        self._task = None
        self._persona = None
        self._container = None
        self._proxy = None
        self._student = None
        self._tc_checker = None
        self._conversation: list[dict[str, str]] = []
        self._tool_logs_per_turn: dict[int, list] = {}
        self._turn: int = 0
        self._done: bool = False
        self._start_time: float = 0.0
        self._deadline: Optional[float] = None

        # Lazy-loaded heavy objects
        self._container_manager = None
        self._orchestrator = None  # For evaluation only

    # ── Public API ────────────────────────────────────────────────

    def reset(
        self,
        task_id: str,
        persona_id: Optional[str] = None,
        run_index: int = 0,
        max_turns: Optional[int] = None,
        timeout_minutes: Optional[int] = None,
    ) -> Observation:
        """Initialize environment for a task. Returns first student message.

        Args:
            task_id: Task identifier (e.g. "S01_ma_crossover").
            persona_id: Student persona. None = first defined in task.
            run_index: Trial index for reproducibility.
            max_turns: Override max turns. None = task default.
            timeout_minutes: Wall-clock timeout. None = task default.

        Returns:
            Observation with the student's opening message and available tools.
        """
        # Clean up any prior session
        self.close()

        self._start_time = time.time()

        # Load task + persona
        self._task = self._find_task(task_id)
        if self._task is None:
            raise ValueError(f"Task '{task_id}' not found.")

        if persona_id is None:
            persona_id = (
                self._task.persona_ids[0]
                if self._task.persona_ids
                else "intermediate_developer"
            )
        self._persona = self._load_persona(persona_id)

        effective_max_turns = max_turns or self._task.max_turns
        effective_timeout = timeout_minutes or self._task.timeout_minutes
        if effective_timeout and effective_timeout > 0:
            self._deadline = time.time() + effective_timeout * 60

        # Setup sandbox
        self._setup_sandbox(run_index)

        # Setup student simulator
        from config.prompt_config import build_scenario, build_user_description
        from bench.gym.tc_checker import parse_tc_items

        tc_items = parse_tc_items(
            self._task.ground_truth.termination_criteria if self._task.ground_truth else "",
            self._task.category.value,
            persona_id,
        )

        scenario = build_scenario(
            self._task, persona_id, has_incremental_tc=(tc_items is not None)
        )
        user_desc = build_user_description(
            self._persona, has_incremental_tc=(tc_items is not None)
        )

        from bench.gym.student_sim import StudentSimulator

        self._student = StudentSimulator(
            scenario=scenario,
            user_description=user_desc,
            model=self.simulator_model,
        )

        # Setup TC checker (if applicable)
        if tc_items is not None:
            from bench.gym.tc_checker import TCChecker

            self._tc_checker = TCChecker(
                tc_items=tc_items,
                model=self.tc_checker_model,
            )

        # Initialize conversation with student opening
        opening = self._task.student_openings.get(
            persona_id,
            "Hi, I'd like to learn about this topic.",
        )
        self._conversation = [{"role": "user", "content": opening}]
        self._turn = 1
        self._done = False

        return Observation(
            student_message=opening,
            available_tools=self._proxy.get_available_tools() if self._proxy else [],
            done=False,
            turn=self._turn,
            max_turns=effective_max_turns,
            info={"task_id": task_id, "persona_id": persona_id},
        )

    def call_tool(self, tool_name: str, **kwargs) -> str:
        """Execute a tool in the sandbox. Does NOT advance conversation.

        Args:
            tool_name: Name of the tool to call.
            **kwargs: Tool parameters.

        Returns:
            Tool result as a string (possibly truncated to 12K chars).

        Raises:
            RuntimeError: If env not reset or conversation already done.
        """
        self._assert_active()

        if self._proxy is None:
            raise RuntimeError("No proxy available. Did reset() succeed?")

        # Check deadline
        if self._deadline and time.time() > self._deadline:
            return "Error: Session timeout reached. No more tool calls allowed."

        # Set turn index for proxy logging
        assistant_turns = len(
            [m for m in self._conversation if m["role"] == "assistant"]
        )
        self._proxy.set_turn(assistant_turns)

        return self._proxy.call_tool(tool_name, **kwargs)

    def send_message(self, text: str) -> Observation:
        """Send agent's response to student. Advances conversation by one turn.

        1. Records agent message
        2. Checks termination criteria
        3. If not done, generates next student message via LLM
        4. Returns new observation

        Args:
            text: The agent's text response to the student.

        Returns:
            Observation with the student's next message (or done=True).
        """
        self._assert_active()

        # Record agent message
        self._conversation.append({"role": "assistant", "content": text})

        max_turns = self._task.max_turns if self._task else 30

        # Check wall-clock timeout
        if self._deadline and time.time() > self._deadline:
            self._done = True
            return Observation(
                student_message="",
                available_tools=self._proxy.get_available_tools() if self._proxy else [],
                done=True,
                turn=self._turn,
                max_turns=max_turns,
                info={"termination_reason": "timeout"},
            )

        # Check TC coverage
        if self._tc_checker is not None:
            try:
                all_covered = self._tc_checker.check(self._conversation)
                if all_covered:
                    # Generate closing message
                    closing = self._student.generate_closing(self._conversation)
                    if closing:
                        self._conversation.append({"role": "user", "content": closing})
                    self._done = True
                    return Observation(
                        student_message=closing or "",
                        available_tools=self._proxy.get_available_tools() if self._proxy else [],
                        done=True,
                        turn=self._turn,
                        max_turns=max_turns,
                        info={
                            "termination_reason": "tc_covered",
                            "tc_coverage": self._tc_checker.coverage_summary,
                        },
                    )
            except Exception as exc:
                logger.warning("TC check failed: %s", exc)

        # Check max turns
        self._turn += 1
        if self._turn > max_turns:
            self._done = True
            return Observation(
                student_message="",
                available_tools=self._proxy.get_available_tools() if self._proxy else [],
                done=True,
                turn=self._turn,
                max_turns=max_turns,
                info={"termination_reason": "max_turns"},
            )

        # Generate next student message
        try:
            student_msg = self._student.generate_message(self._conversation)
        except Exception as exc:
            logger.error("Student simulator failed: %s", exc)
            student_msg = "Can you tell me more about that?"

        self._conversation.append({"role": "user", "content": student_msg})

        return Observation(
            student_message=student_msg,
            available_tools=self._proxy.get_available_tools() if self._proxy else [],
            done=False,
            turn=self._turn,
            max_turns=max_turns,
            info={},
        )

    def evaluate(self) -> Scores:
        """Run full evaluation on the completed conversation.

        Must be called after the conversation is done (obs.done == True)
        or whenever the agent decides to stop.

        Returns:
            Scores with QR, QP, Tutor dimensions, and overall score.
        """
        if not self._conversation:
            return Scores(error="No conversation to evaluate.")

        start = time.time()

        try:
            # Lazy-create orchestrator for evaluation
            if self._orchestrator is None:
                from orchestrator.orchestrator import BenchmarkOrchestrator

                self._orchestrator = BenchmarkOrchestrator(
                    bench_root=str(self.bench_root),
                    use_docker=self.use_docker,
                    eval_model=self.eval_model,
                )

            conversation = list(self._conversation)
            workspace_path = self._container.workspace_path if self._container else ""

            eval_results = self._orchestrator._evaluate_task(
                self._task,
                self._persona,
                workspace_path,
                self._proxy,
                conversation,
            )

            from orchestrator.schemas import TaskResult
            from orchestrator.eval_helpers import populate_eval_results

            result = TaskResult(
                task_id=self._task.task_id,
                persona_id=self._persona.persona_id,
                category=self._task.category.value,
                requires_code=self._task.requires_code,
            )
            populate_eval_results(
                result,
                eval_results,
                category=self._task.category.value,
                requires_code=self._task.requires_code,
                eval_mode="full",
            )

            from evaluation.scoring import compute_task_score

            score_dict = compute_task_score(
                result.quant_result_score,
                result.quant_process_score,
                result.tutor_scores,
                category=result.category,
                requires_code=result.requires_code,
            )

            return Scores(
                overall=score_dict["overall_score"],
                quant_result=score_dict["quant_result_score"],
                quant_process=score_dict["quant_process_score"],
                quant_agent=score_dict["quant_agent_score"],
                tutor=score_dict["tutor_score"],
                tutor_dimensions=score_dict["tutor_dimension_scores"],
                process_metrics=result.process_metrics,
                cost_usd=result.cost_usd,
                duration_seconds=time.time() - start,
            )

        except Exception as exc:
            logger.error("Evaluation failed: %s", exc)
            return Scores(
                error=str(exc),
                duration_seconds=time.time() - start,
            )

    def get_conversation(self) -> list[dict[str, str]]:
        """Return the full conversation transcript so far."""
        return list(self._conversation)

    def get_tool_logs(self) -> list[dict]:
        """Return all tool call logs from the proxy."""
        if self._proxy is None:
            return []
        return [
            {
                "name": log.name,
                "args": log.args,
                "result": log.result,
                "success": log.success,
                "turn_index": log.turn_index,
                "duration_ms": log.duration_ms,
            }
            for log in self._proxy.get_logs()
        ]

    def get_workspace_files(self) -> list[str]:
        """List files in the workspace directory."""
        if self._container is None or not self._container.workspace_path:
            return []
        wp = self._container.workspace_path
        if not os.path.isdir(wp):
            return []
        return sorted(
            os.path.relpath(os.path.join(root, f), wp)
            for root, _, files in os.walk(wp)
            for f in files
        )

    def close(self):
        """Tear down sandbox and release resources."""
        if self._container is not None and self._container_manager is not None:
            try:
                self._container_manager.destroy_container(
                    self._container.container_id
                )
            except Exception as exc:
                logger.warning("Container destroy failed: %s", exc)
        self._container = None
        self._proxy = None
        self._student = None
        self._tc_checker = None
        self._conversation = []
        self._turn = 0
        self._done = False

    # Context manager support
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ── Private ───────────────────────────────────────────────────

    def _assert_active(self):
        if self._task is None:
            raise RuntimeError("Environment not initialized. Call reset() first.")
        if self._done:
            raise RuntimeError(
                "Conversation is done. Call evaluate() then close(), "
                "or reset() for a new task."
            )

    def _setup_sandbox(self, run_index: int):
        """Create container, register tools, configure proxy."""
        from orchestrator.container_manager import ContainerManager
        from mcp_servers.registry import create_proxy_for_task

        if self._container_manager is None:
            self._container_manager = ContainerManager(use_docker=self.use_docker)

        task = self._task

        # Download data
        from config.benchmark_config import DATASET_REVISION
        from scripts.data_manager import ensure_data

        sandbox_img = task.environment.sandbox_image if task.environment else ""
        if sandbox_img and "lean" in sandbox_img:
            paths = ensure_data(series="lean", revision=DATASET_REVISION)
        else:
            paths = ensure_data(series="normal", revision=DATASET_REVISION)

        # Stage data/docs directories
        data_files = task.environment.data_files if task.environment else []
        docs_available = task.environment.docs_available if task.environment else []
        from orchestrator.orchestrator import BenchmarkOrchestrator

        # Use orchestrator's staging helper
        orch = BenchmarkOrchestrator.__new__(BenchmarkOrchestrator)
        orch.bench_root = self.bench_root
        staged_data_dir, staged_docs_dir, self._staged_temp_dirs = (
            orch._create_staged_dirs(
                data_files,
                docs_available,
                data_search_dirs=paths.data_search_dirs,
                docs_dir=paths.docs,
            )
        )

        lean_data_dir = paths.lean_data
        student_code_dir = paths.student_code if task.category.value == "debug" else None

        # Create container
        self._container = self._container_manager.create_container(
            task_id=f"{task.task_id}_{self._persona.persona_id}_{run_index}",
            data_dir=staged_data_dir,
            docs_dir=staged_docs_dir,
            student_code_dir=student_code_dir,
            sandbox_image=(task.environment.sandbox_image if task.environment else None),
            network_enabled=(task.environment.network_enabled if task.environment else False),
            lean_data_dir=lean_data_dir,
        )

        # Start executor daemon in Docker mode
        max_bt = task.environment.max_backtest_trials if task.environment else 0
        if self._container_manager.use_docker:
            self._container_manager.start_executor(
                self._container.container_id,
                env_vars={
                    "QTB_MAX_BACKTEST_TRIALS": str(max_bt),
                    "LEAN_RUN_TIMEOUT": "300",
                },
            )

        # Set env vars for local mode
        os.environ["QTB_DATA_DIR"] = staged_data_dir
        os.environ["QTB_DOCS_DIR"] = staged_docs_dir
        os.environ["QTB_WORKSPACE_DIR"] = self._container.workspace_path
        os.environ["QTB_STUDENT_CODE_DIR"] = student_code_dir or ""
        os.environ["QTB_MAX_BACKTEST_TRIALS"] = str(max_bt)

        # Create MCP proxy with tools
        self._proxy = create_proxy_for_task(
            core_tool_names=task.environment.core_mcp_tools,
            convenient_tool_names=(
                task.ground_truth.convenient_tools if task.ground_truth else []
            ),
            seed=(
                task.seed
                if task.seed is not None
                else hash(f"{task.task_id}_{run_index}")
            ),
            container_manager=self._container_manager,
            container_id=self._container.container_id,
            workspace_path=self._container.workspace_path,
            use_docker=self._container_manager.use_docker,
        )

    def _find_task(self, task_id: str):
        from orchestrator.schemas import QuantTutorTask

        tasks_dir = self.bench_root / "tasks" / "layer2"
        for category_dir in tasks_dir.iterdir():
            if not category_dir.is_dir():
                continue
            for task_file in category_dir.glob("*.json"):
                if task_file.stem == task_id:
                    with open(task_file) as f:
                        return QuantTutorTask(**json.load(f))
        return None

    def _load_persona(self, persona_id: str):
        from orchestrator.schemas import StudentPersona

        path = self.bench_root / "personas" / f"{persona_id}.json"
        with open(path) as f:
            return StudentPersona(**json.load(f))
