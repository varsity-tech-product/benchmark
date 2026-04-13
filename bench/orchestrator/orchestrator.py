"""Main orchestrator for QuantTutorBench.

Manages the per-task lifecycle:
1. RESET - Create sandbox, configure tools
2. INTERACT - Run multi-turn conversation
3. CAPTURE - Collect traces
4. EVALUATE - Run scoring
5. TEARDOWN - Cleanup
"""

import json
import os
import shutil

# Use relative imports that work both as package and standalone
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.llm_config import SIMULATOR_DEFAULT_MODEL
from config.model_resolver import resolve_deepeval_model
from config.prompt_config import (
    build_oracle_context,
    build_scenario,
    build_tutor_context,
    build_user_description,
)
from evaluation.scoring import compute_benchmark_kpis, compute_task_score
from mcp_servers.registry import create_proxy_for_task, register_session_tools

from orchestrator.agent_adapters.base_adapter import BaseAgentAdapter
from orchestrator.container_manager import ContainerManager
from orchestrator.schemas import (
    BenchmarkReport,
    ConversationTurn,
    QuantTutorTask,
    StudentPersona,
    TaskResult,
)
from orchestrator.simulation import (
    run_agent_session,
    run_conversation_simulation,
)

try:
    from deepeval.test_case import ConversationalTestCase  # noqa: F401

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


class EvalAbortError(RuntimeError):
    """Raised when an evaluation call fails after exhausting retries.

    Per-task abort: this error terminates evaluation for the current task
    only.  Other parallel tasks are not affected.
    """

    pass


# ──────────────────────────────────────────────────────────────
# Tool-activity enrichment for Tutor 7D evaluation
# ──────────────────────────────────────────────────────────────

_SKIP_TOOLS_IN_SUMMARY = {"get_environment_info", "send_message"}
_MAX_RESULT_CHARS = 500  # per-tool result preview length (head + tail)
_MAX_SUMMARY_CHARS = 1500  # per-turn tool summary cap


def _brief_args(args: dict) -> str:
    """Compress tool args into a short representation."""
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 60:
            v_str = v_str[:57] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts[:3])


def _summarize_tool_calls(logs: list) -> str:
    """Generate a concise tool activity summary for one conversation turn."""
    lines = []
    for log in logs:
        if log.name in _SKIP_TOOLS_IN_SUMMARY:
            continue
        result_str = str(log.result or "")
        if len(result_str) <= _MAX_RESULT_CHARS:
            result_preview = result_str
        else:
            # Keep head + tail so key numerical results at the end are preserved
            half = _MAX_RESULT_CHARS // 2
            result_preview = result_str[:half] + " ... " + result_str[-half:]
        status = "OK" if log.success else "ERROR"
        lines.append(
            f"- {log.name}({_brief_args(log.args)}) -> [{status}] {result_preview}"
        )

    if not lines:
        return ""

    summary = "[Tool Activity]\n" + "\n".join(lines)
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS] + "\n... (truncated)"
    return summary


def _enrich_conversation_with_tools(
    conversation: list[dict],
    tool_logs: list,
) -> list[dict]:
    """Append tool-activity summaries to assistant turns for tutor evaluation.

    Uses ToolCallLog.turn_index to correlate tool calls to conversation turns.
    Only enriches assistant turns; user turns pass through unchanged.
    """
    if not tool_logs:
        return conversation

    from collections import defaultdict

    logs_by_turn = defaultdict(list)
    for log in tool_logs:
        logs_by_turn[log.turn_index].append(log)

    enriched = []
    assistant_idx = 0
    for turn in conversation:
        if turn["role"] != "assistant":
            enriched.append(turn)
            continue

        turn_logs = logs_by_turn.get(assistant_idx, [])
        summary = _summarize_tool_calls(turn_logs) if turn_logs else ""
        if summary:
            enriched.append(
                {
                    **turn,
                    "content": turn["content"] + "\n\n" + summary,
                }
            )
        else:
            enriched.append(turn)
        assistant_idx += 1

    return enriched


class BenchmarkOrchestrator:
    """Orchestrates the full benchmark evaluation lifecycle."""

    def __init__(
        self,
        bench_root: Optional[str] = None,
        use_docker: bool = False,
        max_concurrent: int = 1,
        eval_model: Optional[str] = None,
        simulator_model: Optional[str] = None,
    ):
        self.bench_root = Path(bench_root or Path(__file__).parent.parent)
        self.tasks_dir = self.bench_root / "tasks" / "layer2"
        self.personas_dir = self.bench_root / "personas"
        self.results_dir = self.bench_root / "results"
        self.container_manager = ContainerManager(use_docker=use_docker)
        # Lazy-loaded HF data paths (populated by _ensure_paths)
        self._paths_i = None
        self._paths_non_i = None
        self.max_concurrent = max_concurrent
        # Keep eval_model as raw (string/list/None) so each downstream
        # resolve_deepeval_model() call can randomly pick from the model list.
        self.eval_model = eval_model
        self.simulator_model = resolve_deepeval_model(
            simulator_model or SIMULATOR_DEFAULT_MODEL
        )

    def load_task(self, task_path: str) -> QuantTutorTask:
        """Load a task from JSON file."""
        with open(task_path) as f:
            return QuantTutorTask(**json.load(f))

    def load_persona(self, persona_id: str) -> StudentPersona:
        """Load a persona from JSON file."""
        persona_path = self.personas_dir / f"{persona_id}.json"
        with open(persona_path) as f:
            return StudentPersona(**json.load(f))

    def _ensure_paths(self, task):
        """Download HF data for the task's series if not cached. Returns DataPaths."""
        from config.benchmark_config import DATASET_REVISION

        from scripts.data_manager import ensure_data

        sandbox_img = task.environment.sandbox_image if task.environment else ""
        if sandbox_img and "lean" in sandbox_img:
            if self._paths_i is None:
                self._paths_i = ensure_data(series="lean", revision=DATASET_REVISION)
            return self._paths_i
        else:
            if self._paths_non_i is None:
                self._paths_non_i = ensure_data(
                    series="normal", revision=DATASET_REVISION
                )
            return self._paths_non_i

    def run_single_task(
        self,
        task: QuantTutorTask,
        persona: StudentPersona,
        agent: BaseAgentAdapter,
        run_index: int = 0,
        max_turns: Optional[int] = None,
        tools_enabled: bool = True,
        pre_teardown_hook: Optional[callable] = None,
        skip_eval: bool = False,
        prompt_mode: str = "tutor",
        timeout_minutes: Optional[int] = None,
        cancel_event=None,
        eval_mode: str = "full",
    ) -> TaskResult:
        """Run a single task with a specific persona and agent.

        This is the core benchmark loop implementing the 5-phase lifecycle.

        Args:
            tools_enabled: If False, no tools are passed to the agent (pure LLM conditions).
            prompt_mode: "tutor", "baseline", or "oracle". Controls which
                dynamic context builder is used (oracle adds learning goals
                and result-persistence directives).
        """
        start_time = time.time()
        max_turns = max_turns or task.max_turns
        agent.reset()
        result = TaskResult(
            task_id=task.task_id,
            persona_id=persona.persona_id,
            run_index=run_index,
            difficulty=task.difficulty.value,
            category=task.category.value,
            requires_code=task.requires_code,
        )

        staged_temp_dirs: list[str] = []
        container = None
        simulator_cost = None
        conversational_test_case = None

        try:
            # === PHASE 1: RESET ===
            # 1a. Download HF data and create staged directories
            paths = self._ensure_paths(task)
            data_files = task.environment.data_files if task.environment else []
            docs_available = task.environment.docs_available if task.environment else []
            staged_data_dir, staged_docs_dir, staged_temp_dirs = (
                self._create_staged_dirs(
                    data_files,
                    docs_available,
                    data_search_dirs=paths.data_search_dirs,
                    docs_dir=paths.docs,
                )
            )

            # LEAN metadata mount (symbol-properties, market-hours, universe sidecar)
            lean_data_dir = paths.lean_data

            # Student code for debug tasks (from current series' paths)
            student_code_dir = None
            if task.category.value == "debug":
                student_code_dir = paths.student_code

            # 1b. Create sandbox container (Docker or local fallback)
            # 12-col custom data mount (I-series / X-series LEAN tasks)
            custom_data_dir = getattr(paths, "custom_data", None)

            container = self.container_manager.create_container(
                task_id=f"{task.task_id}_{persona.persona_id}_{run_index}",
                data_dir=staged_data_dir,
                docs_dir=staged_docs_dir,
                student_code_dir=student_code_dir,
                sandbox_image=(
                    task.environment.sandbox_image if task.environment else None
                ),
                network_enabled=(
                    task.environment.network_enabled if task.environment else False
                ),
                lean_data_dir=lean_data_dir,
                custom_data_dir=custom_data_dir,
            )

            # 1b.5. Start tool executor daemon inside the container (Docker only).
            # All core tools will be routed through this persistent process.
            # Pass QTB_MAX_BACKTEST_TRIALS so run_backtest.sh can enforce budget.
            max_bt = task.environment.max_backtest_trials if task.environment else 0
            if self.container_manager.use_docker:
                self.container_manager.start_executor(
                    container.container_id,
                    env_vars={
                        "QTB_MAX_BACKTEST_TRIALS": str(max_bt),
                        "LEAN_RUN_TIMEOUT": "300",
                    },
                )

            # 1c. Set environment vars for tool implementations (lazy reads in tools.py)
            # In Docker mode these are unused (container uses default /data etc.);
            # kept for local-mode backward compatibility.
            os.environ["QTB_DATA_DIR"] = staged_data_dir
            os.environ["QTB_DOCS_DIR"] = staged_docs_dir
            os.environ["QTB_WORKSPACE_DIR"] = container.workspace_path
            os.environ["QTB_STUDENT_CODE_DIR"] = student_code_dir or ""
            # Pass max_backtest_trials so trial tools know the budget
            max_bt = task.environment.max_backtest_trials if task.environment else 0
            os.environ["QTB_MAX_BACKTEST_TRIALS"] = str(max_bt)

            # Expose workspace path for live file serving (web dashboard)
            try:
                from orchestrator.live_monitor import set_active_workspace

                set_active_workspace(container.workspace_path)
                print(
                    f"  [DIAG] set_active_workspace({container.workspace_path})",
                    flush=True,
                )
            except Exception as _ws_exc:
                print(f"  [DIAG] set_active_workspace FAILED: {_ws_exc}", flush=True)

            # 1d. Configure MCP proxy with task-specific tools + container info
            proxy = create_proxy_for_task(
                core_tool_names=task.environment.core_mcp_tools,
                convenient_tool_names=(
                    task.ground_truth.convenient_tools if task.ground_truth else []
                ),
                seed=(
                    task.seed
                    if task.seed is not None
                    else hash(f"{task.task_id}_{run_index}")
                ),
                container_manager=self.container_manager,
                container_id=container.container_id,
                workspace_path=container.workspace_path,
                use_docker=self.container_manager.use_docker,
            )

            # === PHASE 1.5: INJECT DYNAMIC CONTEXT ===
            # Temporarily augment the agent's system prompt with task/persona
            # context so it knows what to teach and who the student is.
            if prompt_mode == "oracle":
                dynamic_context = build_oracle_context(task, persona)
            else:
                dynamic_context = build_tutor_context(task, persona)
            agent.set_task_context(dynamic_context)

            # Apply per-task agent step limit (controls SDK internal loop depth)
            agent.set_agent_max_steps(task.agent_max_steps)

            # === PHASE 2: INTERACT (agent-driven via MCP tools) ===
            # The agent drives the conversation through tool calls.
            # send_message is a regular MCP tool backed by TutoringSession.
            # TC checking and student simulation happen inside send_message.
            try:
                from mcp_servers.session import TutoringSession
                from mcp_servers.student_sim import StudentSimulator
                from mcp_servers.tc_checker import TCChecker, parse_tc_items

                simulator_cost = None

                # Build student simulator
                has_tc_items = False
                tc_text = (
                    task.ground_truth.termination_criteria
                    if task.ground_truth
                    else None
                )
                tc_items = parse_tc_items(
                    tc_text,
                    task.category.value,
                    persona_id=persona.persona_id,
                )
                has_tc_items = tc_items is not None

                from config.model_resolver import resolve_deepeval_model

                resolved_sim_model = resolve_deepeval_model(
                    self.simulator_model or SIMULATOR_DEFAULT_MODEL,
                )
                student_sim = StudentSimulator(
                    scenario=build_scenario(
                        task,
                        persona.persona_id,
                        has_incremental_tc=has_tc_items,
                    ),
                    user_description=build_user_description(
                        persona,
                        has_incremental_tc=has_tc_items,
                    ),
                    model=resolved_sim_model,
                )

                tc_checker = None
                if tc_items:
                    tc_checker = TCChecker(tc_items)

                # Compute deadline
                effective_timeout = timeout_minutes or task.timeout_minutes
                deadline = None
                if effective_timeout and effective_timeout > 0:
                    deadline = time.time() + effective_timeout * 60

                session = TutoringSession(
                    task=task,
                    persona=persona,
                    student_sim=student_sim,
                    tc_checker=tc_checker,
                    max_turns=max_turns,
                    deadline=deadline,
                    proxy=proxy,
                )

                # Register send_message + get_session_info on the proxy
                register_session_tools(proxy, session)

                if tools_enabled:
                    print(
                        f"  Agent-driven session "
                        f"(simulator={self.simulator_model or 'default'}, "
                        f"tc={'incremental' if tc_items else 'native'})..."
                    )
                    conversation = run_agent_session(
                        task=task,
                        persona=persona,
                        agent_adapter=agent,
                        proxy=proxy,
                        session=session,
                        timeout_minutes=timeout_minutes,
                        cancel_event=cancel_event,
                    )
                else:
                    # No tools: fall back to legacy DeepEval path
                    # (pure LLM conditions without tool calling)
                    if not DEEPEVAL_AVAILABLE:
                        raise RuntimeError(
                            "DeepEval required for no-tools mode. "
                            "Install with: pip install deepeval"
                        )
                    conversational_test_case, simulator_cost = (
                        run_conversation_simulation(
                            task=task,
                            persona=persona,
                            agent_adapter=agent,
                            proxy=proxy,
                            simulator_model=(
                                self.simulator_model or SIMULATOR_DEFAULT_MODEL
                            ),
                            max_turns=max_turns,
                            tools_enabled=False,
                            timeout_minutes=timeout_minutes,
                            cancel_event=cancel_event,
                        )
                    )
                    conversation = [
                        {"role": t.role, "content": t.content}
                        for t in conversational_test_case.turns
                    ]

                # Populate result turns from conversation
                for m in conversation:
                    result.turns.append(
                        ConversationTurn(role=m["role"], content=m["content"])
                    )
                print(
                    f"  Session completed: {len(conversation)} messages, "
                    f"{session.turn} turns"
                )
            finally:
                # Clear task context for next task+persona
                agent.set_task_context("")

            # === PHASE 3: CAPTURE ===
            # Capture workspace file list before teardown destroys the container.
            # Uses os.walk to include files in subdirectories (e.g. data/).
            if container.workspace_path and os.path.isdir(container.workspace_path):
                result.workspace_files = sorted(
                    os.path.relpath(os.path.join(root, f), container.workspace_path)
                    for root, _, files in os.walk(container.workspace_path)
                    for f in files
                )

            # Capture sandbox execution metadata (diagnostic, not scored).
            result.sandbox_info = {
                "container_id": container.container_id,
                "network_enabled": container.network_enabled,
                "network_mode": container.network_mode,
                "use_docker": self.container_manager.use_docker,
                "sandbox_image": (
                    task.environment.sandbox_image if task.environment else "N/A"
                ),
            }

            # === PHASE 3.25: TRIAL FINALIZATION ===
            # If the task uses the trial system, auto-select best trial
            # when the agent didn't explicitly call select_submission.
            # Check for either manifest (tool-invoked runs) or JSONL log
            # (shell_exec-invoked runs).
            max_bt = task.environment.max_backtest_trials if task.environment else 0
            if max_bt > 0 and container.workspace_path:
                manifest_path = os.path.join(
                    container.workspace_path, ".trials", "manifest.json"
                )
                jsonl_path = os.path.join(
                    container.workspace_path, ".backtest_runs.jsonl"
                )
                if os.path.exists(manifest_path) or os.path.exists(jsonl_path):
                    try:
                        from mcp_servers.core.trial_manager import TrialManager

                        tm = TrialManager(container.workspace_path, max_trials=max_bt)
                        status = tm.get_status()
                        if not status.get("selected_trial") and status["trials"]:
                            selected = tm.auto_select()
                            print(
                                f"  [Trials] Auto-selected trial {selected} "
                                f"(agent did not call select_submission)"
                            )
                        result.trial_metadata = tm.get_status()
                    except Exception as e:
                        print(f"  [Trials] Warning: finalization failed: {e}")

            # === PHASE 3.5: PRE-TEARDOWN HOOK ===
            # Allows callers (e.g. reference generator) to capture full proxy
            # logs and workspace files before evaluation and teardown.
            if pre_teardown_hook is not None:
                pre_teardown_hook(
                    result=result,
                    proxy=proxy,
                    workspace_path=container.workspace_path,
                )

            # === PHASE 4: EVALUATE ===
            if cancel_event and cancel_event.is_set():
                print("  [CANCEL] Run cancelled by user, skipping evaluation")
            elif skip_eval:
                print("  [RUNONLY] Skipping evaluation (--runonly mode)")
            else:
                conversation = [
                    {"role": t.role, "content": t.content} for t in result.turns
                ]
                eval_results = self._evaluate_task(
                    task,
                    persona,
                    container.workspace_path,
                    proxy,
                    conversation,
                    cancel_event=cancel_event,
                    eval_mode=eval_mode,
                )
                from orchestrator.eval_helpers import populate_eval_results

                populate_eval_results(
                    result,
                    eval_results,
                    category=task.category.value,
                    requires_code=task.requires_code,
                    eval_mode=eval_mode,
                )

            # === PHASE 5: TEARDOWN ===
            self.container_manager.destroy_container(container.container_id)

        except EvalAbortError as e:
            result.error = str(e)
            result.eval_aborted = True
            print(f"  *** EVAL ABORTED: {e}")
        except Exception as e:
            result.error = str(e)
        finally:
            # Clear live workspace path (no longer accessible after teardown)
            try:
                from orchestrator.live_monitor import set_active_workspace

                set_active_workspace(None)
            except ImportError:
                pass
            # Clean up staged directories (always, even on error)
            if container is not None and result.error:
                try:
                    self.container_manager.destroy_container(container.container_id)
                except Exception:
                    pass
            self._cleanup_staged_dirs(staged_temp_dirs)
            try:
                agent.close()
            except Exception as close_err:
                print(f"  [warn] agent.close() failed: {close_err}")

        result.duration_seconds = time.time() - start_time

        # §6.5: Aggregate cost from actual token tracking
        # Harvest then clear so records reflect only THIS task (not prior ones).
        from orchestrator.eval_helpers import aggregate_eval_cost, build_token_usage

        agent_records = agent.get_token_records()
        agent._token_records.clear()
        agent_input = sum(r.input_tokens for r in agent_records)
        agent_output = sum(r.output_tokens for r in agent_records)
        agent_cost = sum(r.cost_usd for r in agent_records)

        eval_cost, eval_cost_by_model, eval_cost_by_stage_model = aggregate_eval_cost(
            result
        )

        agent_model = getattr(agent, "model", "unknown")
        sim_model_obj = self.simulator_model or SIMULATOR_DEFAULT_MODEL
        sim_model = getattr(sim_model_obj, "name", str(sim_model_obj))
        sim_cost = simulator_cost or 0.0

        result.token_usage, result.cost_usd = build_token_usage(
            agent_input=agent_input,
            agent_output=agent_output,
            agent_cost=agent_cost,
            api_calls=len(agent_records),
            agent_model=agent_model,
            sim_cost=sim_cost,
            sim_model=sim_model,
            eval_cost=eval_cost,
            eval_cost_by_model=eval_cost_by_model,
            eval_cost_by_stage_model=eval_cost_by_stage_model,
        )

        return result

    def run_benchmark(
        self,
        agent: BaseAgentAdapter,
        task_filter: Optional[list[str]] = None,
        persona_filter: Optional[list[str]] = None,
        num_trials: int = 1,
        max_turns_override: Optional[int] = None,
        tools_enabled: bool = True,
    ) -> BenchmarkReport:
        """Run the full benchmark suite."""
        report = BenchmarkReport(agent_name=agent.agent_name)

        # Discover tasks
        task_files = []
        for category_dir in self.tasks_dir.iterdir():
            if category_dir.is_dir():
                for task_file in category_dir.glob("*.json"):
                    task_files.append(task_file)

        for task_file in sorted(task_files):
            task = self.load_task(str(task_file))

            if task_filter and task.task_id not in task_filter:
                continue

            for persona_id in task.persona_ids:
                if persona_filter and persona_id not in persona_filter:
                    continue

                try:
                    persona = self.load_persona(persona_id)
                except FileNotFoundError:
                    continue

                for trial in range(num_trials):
                    result_key = f"{task.task_id}_{persona_id}_t{trial}"
                    print(f"  Running: {result_key}...")

                    result = self.run_single_task(
                        task=task,
                        persona=persona,
                        agent=agent,
                        run_index=trial,
                        max_turns=max_turns_override or task.max_turns,
                        tools_enabled=tools_enabled,
                    )

                    report.results_by_task[result_key] = result
                    report.total_tasks += 1

        # Compute KPIs
        all_result_objects = list(report.results_by_task.values())
        all_scores = [
            compute_task_score(
                r.quant_result_score,
                r.quant_process_score,
                r.tutor_scores,
                category=r.category,
                requires_code=r.requires_code,
            )
            for r in all_result_objects
        ]
        if all_scores:
            kpis = compute_benchmark_kpis(
                all_scores,
                task_result_objects=all_result_objects,
            )
            report.overall_agent_score = kpis.get("overall_agent_score", 0.0)
            report.quant_agent_index = kpis.get("quant_agent_index", 0.0)
            report.tutoring_effectiveness_index = kpis.get(
                "tutoring_effectiveness_index", 0.0
            )
            report.adaptiveness_score = kpis.get("adaptiveness_score", 0.0)
            report.process_mastery_score = kpis.get("process_mastery_score", 0.0)

            # Populate results_by_difficulty and results_by_category (§6.4)
            import statistics as _stats
            from collections import defaultdict

            by_diff = defaultdict(list)
            by_cat = defaultdict(list)
            for r_obj, r_score in zip(all_result_objects, all_scores):
                if r_obj.difficulty:
                    by_diff[r_obj.difficulty].append(r_score["overall_score"])
                if r_obj.category:
                    by_cat[r_obj.category].append(r_score["overall_score"])
            report.results_by_difficulty = {
                k: round(_stats.mean(v), 4) for k, v in sorted(by_diff.items())
            }
            report.results_by_category = {
                k: round(_stats.mean(v), 4) for k, v in sorted(by_cat.items())
            }

        return report

    def _evaluate_task(
        self,
        task,
        persona,
        workspace_path,
        proxy,
        conversation,
        cancel_event=None,
        eval_mode="full",
    ) -> dict:
        """Run full evaluation on a completed task.

        Design doc §4.3 Phase 4: EVALUATION (post-hoc, using DeepEval)
        - Quant Result: run eval/test_*.py against workspace files
        - Quant Process: Reformed process metrics (7 dimensions)
        - Tutor Quality: DeepEval ConversationalGEval with 7D persona-aware rubric

        Design doc §6.3:
        Task Score = 0.70 × Quant Agent Score + 0.30 × Tutor Score
        Quant Agent Score = 0.50 × Result Sub-score + 0.50 × Process Sub-score

        Args:
            task: The benchmark task.
            persona: The student persona.
            workspace_path: Path to the container workspace.
            proxy: The MCPProxy instance with tool call logs.
            conversation: List of {"role", "content"} dicts.
            eval_mode: "full" (default), "qr_only", or "qp_only".
        """
        from orchestrator.live_monitor import emit

        _eval_id = {"task_id": task.task_id, "persona_id": persona.persona_id}

        def _check_eval_cancel():
            if cancel_event and cancel_event.is_set():
                raise InterruptedError("Evaluation cancelled by user")

        results = {
            "quant_result": 0.0,
            "quant_process": 0.0,
            "tutor_scores": {},
            "process_metrics": {},
        }

        _run_qr = eval_mode in ("full", "qr_only")
        _run_qp = eval_mode in ("full", "qp_only")

        if _run_qr or _run_qp:
            print(f"  Eval mode: {eval_mode}")

        # ── Step 2: Quant Result Score (custom eval scripts) ──
        emit("eval_step", {**_eval_id, "step": "quant_result", "status": "running"})
        if _run_qr:
            print("  Evaluating Quant Result...")
        if _run_qr and task.ground_truth.quant_validation:
            eval_script = (
                self.bench_root / task.ground_truth.quant_validation.eval_script
            )
            if eval_script.exists():
                try:
                    import importlib.util
                    import inspect

                    spec = importlib.util.spec_from_file_location(
                        "eval_module", str(eval_script)
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)

                    # Detect eval script signature — pass data_files if accepted
                    sig = inspect.signature(module.evaluate)
                    eval_kwargs: dict = {}
                    if "data_files" in sig.parameters:
                        eval_kwargs["data_files"] = task.environment.data_files or []
                    eval_result = module.evaluate(
                        workspace_path,
                        proxy.get_logs(),
                        conversation,
                        **eval_kwargs,
                    )
                    results["quant_result"] = eval_result.get("score", 0.0)
                    results["eval_script_detail"] = eval_result
                except Exception as e:
                    results["quant_result_error"] = str(e)

        emit(
            "eval_step",
            {
                **_eval_id,
                "step": "quant_result",
                "status": "done",
                "score": (
                    round(results["quant_result"], 4) if results["quant_result"] else 0
                ),
            },
        )

        _check_eval_cancel()

        # ── Step 2b: Code Execution QR (Phase 1) ──
        # Reference loading is always needed (QP uses it for process_alignment).
        # Code eval scoring is QR-only.
        emit("eval_step", {**_eval_id, "step": "code_eval", "status": "running"})
        if _run_qr:
            print("  Evaluating Code Execution QR...")
        reference = None  # loaded here, also used by Step 2c + Step 3b
        try:
            from reference_generator.reference_store import ReferenceStore

            ref_store = ReferenceStore()
            reference = ref_store.load(task.task_id, persona.persona_id)

            if _run_qr:
                from evaluation.code_eval import evaluate_code_combined

                _sandbox_img = (
                    task.environment.sandbox_image if task.environment else ""
                )
                code_eval_result = evaluate_code_combined(
                    workspace_path=workspace_path,
                    tool_logs=proxy.get_logs(),
                    reference=reference,
                    task_requires_code=task.requires_code,
                    is_lean_task="lean" in _sandbox_img,
                )
                results["code_eval"] = code_eval_result
        except Exception as e:
            if _run_qr:
                results["code_eval_error"] = str(e)

        emit(
            "eval_step",
            {
                **_eval_id,
                "step": "code_eval",
                "status": "done",
                "score": round(results.get("code_eval", {}).get("score", 0), 4),
            },
        )

        _check_eval_cancel()

        # ── Step 2c (pre): Tool Usage (mathematical, no LLM — needed by QP) ──
        emit("eval_step", {**_eval_id, "step": "tool_usage", "status": "running"})
        tool_usage_result = None
        if _run_qp:
            try:
                from evaluation.deepeval_metrics.tool_usage import evaluate_tool_usage

                tool_usage_result = evaluate_tool_usage(
                    proxy_logs=proxy.get_logs(),
                    expected_tools=(
                        task.ground_truth.expected_mcp_tools
                        if task.ground_truth
                        else []
                    ),
                    convenient_tools=(
                        task.ground_truth.convenient_tools if task.ground_truth else []
                    ),
                    distractor_names=proxy.get_distractor_names(),
                    is_adversarial=(task.category.value == "adversarial"),
                )
                results["tool_usage"] = tool_usage_result
            except Exception as e:
                results["tool_usage_error"] = str(e)

        emit(
            "eval_step",
            {
                **_eval_id,
                "step": "tool_usage",
                "status": "done",
                "score": (
                    round(tool_usage_result.get("score", 0), 4)
                    if tool_usage_result
                    else 0
                ),
            },
        )

        # ── Steps 2c/3/4: Parallel LLM evaluation (RJ + QP + Tutor) ──
        # These three evaluators have no cross-dependencies. Each maintains
        # its own async event loop internally. Peak concurrency ~43 requests
        # (RJ=3 + QP≤20 + Tutor≤20), well within OpenRouter limits.
        #
        # Abort mechanism: _abort_event is shared between RJ and QP only.
        # Tutor manages its own abort independently (has internal fallback
        # for non-JSON eval LLM responses). If Tutor fails even after
        # fallback, it degrades gracefully without killing RJ/QP.
        import concurrent.futures

        _logs = proxy.get_logs()  # snapshot once for all threads
        _is_adversarial = task.category.value == "adversarial"
        _abort_event = threading.Event()  # shared by RJ + QP only

        def _run_result_judge() -> dict:
            """Thread 1: LLM Result Judge (Step 2c)."""
            print("  [RJ] Evaluating Result Quality (LLM judge)...")
            from evaluation.deepeval_metrics.result_judge import (
                evaluate_result_quality,
            )

            rj_result = evaluate_result_quality(
                task_description=task.description,
                category=task.category.value,
                workspace_path=workspace_path,
                tool_logs=_logs,
                conversation=conversation,
                model=self.eval_model,
                reference=reference,
                expected_outcome=(
                    task.ground_truth.expected_outcome if task.ground_truth else None
                ),
                abort_event=_abort_event,
            )
            print("  [RJ] Done.")
            return {"result_judge": rj_result}

        def _run_process_metrics() -> dict:
            """Thread 2: DeepEval process-level metrics (Step 3)."""
            print("  [QP] Evaluating Quant Process...")
            from evaluation.deepeval_metrics.process_metrics import (
                evaluate_all_process_metrics,
            )

            agent_outputs = [
                t["content"] for t in conversation if t["role"] == "assistant"
            ]
            combined_output = "\n---\n".join(agent_outputs) if agent_outputs else ""

            process_results = evaluate_all_process_metrics(
                task_description=task.description,
                actual_output=combined_output,
                proxy_logs=_logs,
                category=task.category.value,
                conversation=conversation,
                model=self.eval_model,
                reference_trace=reference,
                is_adversarial=_is_adversarial,
                tool_usage_result=tool_usage_result,
                task_requires_code=task.requires_code,
                abort_event=_abort_event,
            )
            print("  [QP] Done.")
            return {
                "process_metrics": process_results,
                "quant_process": round(
                    process_results.get("aggregate_process_score", 0.5), 4
                ),
            }

        def _run_tutor_eval() -> dict:
            """Thread 3: Tutor Quality Score — 7D ConversationalGEval (Step 4)."""
            print(
                "  [Tutor] Evaluating Tutor Quality "
                "(7D rubric, multi-model × 3x shuffled)..."
            )
            from evaluation.deepeval_metrics.tutor_conv_geval import (
                evaluate_tutor_dimensions,
            )

            # Two-tier conversation input for Tutor 7D evaluation:
            # - D4/D5/D7 (competence dims) use enriched conversation so the
            #   judge can verify domain accuracy against tool outputs.
            # - D1/D2/D3/D6 (teaching-quality dims) use the original
            #   conversation so tool-activity text doesn't interfere with
            #   evaluation of the tutor's own language and empathy.
            enriched_conv = _enrich_conversation_with_tools(conversation, _logs)

            tutor_scores = evaluate_tutor_dimensions(
                conversation_turns=conversation,
                enriched_conversation_turns=enriched_conv,
                persona_level=persona.knowledge_level,
                scenario=build_scenario(task, persona.persona_id),
                expected_outcome=task.ground_truth.expected_outcome,
                user_description=build_user_description(persona),
                model=self.eval_model,
                category=task.category.value,
                requires_code=task.requires_code,
                abort_event=None,  # independent — has internal fallback
            )
            fallback_count = tutor_scores.pop("_fallback_count", 0)
            per_model = tutor_scores.pop("_per_model", None)
            out: dict = {"tutor_scores": tutor_scores}
            if fallback_count > 0:
                out["tutor_fallback_count"] = fallback_count
            if per_model:
                out["tutor_scores_by_model"] = per_model
                print("  [Tutor] Per-model tutor scores:")
                for mname, dim_scores in per_model.items():
                    clean = {
                        k: v
                        for k, v in dim_scores.items()
                        if not k.startswith("_") and isinstance(v, (int, float))
                    }
                    avg = sum(clean.values()) / len(clean) if clean else 0.0
                    print(f"    {mname}: avg={avg:.4f}")
                    for dim, sc in sorted(clean.items()):
                        print(f"      {dim}: {sc:.4f}")
            print("  [Tutor] Done.")
            return out

        _check_eval_cancel()

        # Determine which parallel threads to submit
        _active_labels = []
        if _run_qr:
            _active_labels.append("RJ")
        if _run_qp:
            _active_labels.append("QP")
        _active_labels.append("Tutor")
        print(f"  Running {' / '.join(_active_labels)} in parallel...")

        if _run_qr:
            emit("eval_step", {**_eval_id, "step": "result_judge", "status": "running"})
        if _run_qp:
            emit(
                "eval_step",
                {**_eval_id, "step": "process_metrics", "status": "running"},
            )
        emit("eval_step", {**_eval_id, "step": "tutor_7d", "status": "running"})
        _t_parallel = time.time()
        _thread_errors: list[Exception] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            fut_rj = pool.submit(_run_result_judge) if _run_qr else None
            fut_qp = pool.submit(_run_process_metrics) if _run_qp else None
            fut_tutor = pool.submit(_run_tutor_eval)

            _futures = [f for f in (fut_rj, fut_qp, fut_tutor) if f is not None]
            for fut in concurrent.futures.as_completed(_futures):
                try:
                    fut_result = fut.result()
                    results.update(fut_result)
                    if fut is fut_rj:
                        rj_s = fut_result.get("result_judge", {}).get("score", 0)
                        emit(
                            "eval_step",
                            {
                                **_eval_id,
                                "step": "result_judge",
                                "status": "done",
                                "score": round(rj_s, 4),
                            },
                        )
                    elif fut is fut_qp:
                        qp_s = fut_result.get("quant_process", 0)
                        emit(
                            "eval_step",
                            {
                                **_eval_id,
                                "step": "process_metrics",
                                "status": "done",
                                "score": round(qp_s, 4),
                            },
                        )
                    elif fut is fut_tutor:
                        ts = fut_result.get("tutor_scores", {})
                        t_vals = [v for v in ts.values() if isinstance(v, (int, float))]
                        t_avg = sum(t_vals) / len(t_vals) if t_vals else 0
                        emit(
                            "eval_step",
                            {
                                **_eval_id,
                                "step": "tutor_7d",
                                "status": "done",
                                "score": round(t_avg, 4),
                            },
                        )
                except Exception as e:
                    if fut is fut_tutor:
                        # Tutor is independent — graceful degradation
                        print(f"  *** TUTOR EVAL WARNING: {e}")
                        results["tutor_scores"] = {}
                        results["tutor_eval_error"] = str(e)
                    else:
                        # RJ / QP share abort cascade
                        _abort_event.set()
                        _thread_errors.append(e)

        if _thread_errors:
            first = _thread_errors[0]
            raise EvalAbortError(
                f"Evaluation aborted: {len(_thread_errors)} evaluator(s) failed. "
                f"First error: {first}"
            ) from first

        print(
            f"  Parallel eval done in {time.time() - _t_parallel:.1f}s "
            f"({' + '.join(_active_labels)})"
        )

        _check_eval_cancel()

        if not _run_qr:
            # qp_only mode — skip QR blending entirely
            return results

        # ── Step 2d: Combine QR components (30/30/40 blend) ──
        emit("eval_step", {**_eval_id, "step": "qr_blend", "status": "running"})
        # Must run after RJ completes (needs result_judge score).
        programmatic_score = results["quant_result"]
        code_eval_score = results.get("code_eval", {}).get("score", 0.0)
        code_eval_applicable = results.get("code_eval", {}).get("applicable", False)
        llm_judge_score = results.get("result_judge", {}).get("score", 0.0)

        # Tasks that don't require code: always skip code_eval.
        # Without this, code_eval participates only when the agent happens
        # to write a .py file — making the QR formula non-deterministic
        # across runs of the same task (ICC stability issue).
        if not task.requires_code:
            code_eval_applicable = False

        # When the eval script returns score=None, it signals insufficient
        # programmatic evidence (e.g. A04: no tool calls → can't judge
        # technical dump from tool logs alone).  Defer entirely to LLM Judge.
        if programmatic_score is None:
            if code_eval_applicable:
                results["quant_result"] = round(
                    0.30 * code_eval_score + 0.70 * llm_judge_score, 4
                )
            else:
                results["quant_result"] = round(llm_judge_score, 4)
            print(
                f"    QR: eval script returned None "
                f"(insufficient signal) → deferring to LLM Judge "
                f"({llm_judge_score:.2f})"
            )
            rj = results.get("result_judge")
            if isinstance(rj, dict):
                rj["_eval_script_score"] = None
                rj["_dampening_factor"] = None
            emit(
                "eval_step",
                {
                    **_eval_id,
                    "step": "qr_blend",
                    "status": "done",
                    "score": round(results["quant_result"], 4),
                },
            )
            return results

        # Continuous divergence dampening: smoothly reduce programmatic
        # weight as divergence between eval script and LLM judge increases.
        # Uses sigmoid centered at 0.40 — replaces the old binary threshold.
        import math

        divergence = abs(programmatic_score - llm_judge_score)
        # factor ≈ 1.0 when divergence ≈ 0, ≈ 0.5 at 0.40, ≈ 0.0 at 0.80+
        dampening_factor = 1.0 / (1.0 + math.exp(10 * (divergence - 0.40)))

        if code_eval_applicable:
            # Smoothly interpolate between:
            #   factor=1.0 → (0.30, 0.30, 0.40) [standard]
            #   factor=0.0 → (0.10, 0.30, 0.60) [fully dampened]
            w_prog = 0.10 + 0.20 * dampening_factor
            w_code = 0.30
            w_judge = 1.0 - w_prog - w_code
            results["quant_result"] = round(
                w_prog * programmatic_score
                + w_code * code_eval_score
                + w_judge * llm_judge_score,
                4,
            )
        else:
            # Smoothly interpolate between:
            #   factor=1.0 → (0.40, 0.60) [standard]
            #   factor=0.0 → (0.15, 0.85) [fully dampened]
            w_prog = 0.15 + 0.25 * dampening_factor
            w_judge = 1.0 - w_prog
            results["quant_result"] = round(
                w_prog * programmatic_score + w_judge * llm_judge_score, 4
            )

        if dampening_factor < 0.9:
            print(
                f"    QR dampening active: programmatic={programmatic_score:.2f} "
                f"vs judge={llm_judge_score:.2f} "
                f"(Δ={divergence:.2f}, factor={dampening_factor:.3f})"
            )

        # Store QR blending diagnostics in result_judge dict for score_report
        rj = results.get("result_judge")
        if isinstance(rj, dict):
            rj["_eval_script_score"] = programmatic_score
            rj["_dampening_factor"] = round(dampening_factor, 4)

        emit(
            "eval_step",
            {
                **_eval_id,
                "step": "qr_blend",
                "status": "done",
                "score": round(results["quant_result"], 4),
            },
        )

        return results

    def _create_staged_dirs(
        self,
        data_files: list[str],
        docs_available: list[str],
        data_search_dirs: list[str],
        docs_dir: str,
    ) -> tuple[str, str, list[str]]:
        """Create temp directories with copies of only the allowed files.

        Args:
            data_files: List of allowed data file names (e.g. ["BTCUSDT_1h.csv"]).
            docs_available: List of allowed doc file names (e.g. ["moving_averages.md"]).
            data_search_dirs: Directories to search for data files (from HF cache).
            docs_dir: Directory containing reference docs (from HF cache).

        Returns:
            (staged_data_dir, staged_docs_dir, temp_dirs_to_cleanup)
        """
        temp_dirs: list[str] = []

        if data_files:
            staged_data = tempfile.mkdtemp(prefix="qtb_data_")
            temp_dirs.append(staged_data)
            for fname in data_files:
                for search_dir in data_search_dirs:
                    src = os.path.join(search_dir, fname)
                    if os.path.isfile(src):
                        dst = os.path.join(staged_data, fname)
                        os.makedirs(os.path.dirname(dst), exist_ok=True)
                        shutil.copy2(src, dst)
                        break
        else:
            staged_data = data_search_dirs[0] if data_search_dirs else ""

        if docs_available:
            staged_docs = tempfile.mkdtemp(prefix="qtb_docs_")
            temp_dirs.append(staged_docs)
            for fname in docs_available:
                src = os.path.join(docs_dir, fname)
                if os.path.isfile(src):
                    dst = os.path.join(staged_docs, fname)
                    shutil.copy2(src, dst)
        else:
            staged_docs = docs_dir

        return staged_data, staged_docs, temp_dirs

    def _cleanup_staged_dirs(self, temp_dirs: list[str]) -> None:
        """Remove temporary staged directories."""
        for d in temp_dirs:
            if os.path.exists(d):
                shutil.rmtree(d, ignore_errors=True)

    def save_results(self, report: BenchmarkReport, output_dir: Optional[str] = None):
        """Save benchmark results to disk."""
        out = Path(output_dir or self.results_dir)
        out.mkdir(parents=True, exist_ok=True)

        # Save full report
        report_path = out / f"report_{report.agent_name}_{int(time.time())}.json"
        with open(report_path, "w") as f:
            json.dump(report.model_dump(), f, indent=2, default=str)

        # Save individual task traces
        traces_dir = out / "traces"
        traces_dir.mkdir(exist_ok=True)
        for key, result in report.results_by_task.items():
            trace_path = traces_dir / f"{key}.json"
            with open(trace_path, "w") as f:
                json.dump(result.model_dump(), f, indent=2, default=str)

        return str(report_path)
