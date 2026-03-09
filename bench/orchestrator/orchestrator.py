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
    build_scenario,
    build_tutor_context,
    build_user_description,
)
from evaluation.scoring import compute_benchmark_kpis, compute_task_score
from mcp_servers.registry import create_proxy_for_task

from orchestrator.agent_adapters.base_adapter import BaseAgentAdapter
from orchestrator.container_manager import ContainerManager
from orchestrator.schemas import (
    BenchmarkReport,
    ConversationTurn,
    QuantTutorTask,
    StudentPersona,
    TaskResult,
)
from orchestrator.simulator_config import (
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
        self.data_dir = str(self.bench_root / "data" / "frozen")
        self.docs_dir = str(self.bench_root / "docs" / "reference")
        self.student_code_dir = str(self.bench_root / "student_code")
        self.tasks_dir = self.bench_root / "tasks" / "layer2"
        self.personas_dir = self.bench_root / "personas"
        self.results_dir = self.bench_root / "results"
        self.container_manager = ContainerManager(use_docker=use_docker)
        self._lean_data_paths = None  # Lazy-loaded for I-series
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

    def _ensure_lean_data(self):
        """Download LEAN data from HF if not cached. Called once, cached."""
        if self._lean_data_paths is None:
            from scripts.data_manager import ensure_data
            self._lean_data_paths = ensure_data(series="i")
        return self._lean_data_paths

    def run_single_task(
        self,
        task: QuantTutorTask,
        persona: StudentPersona,
        agent: BaseAgentAdapter,
        run_index: int = 0,
        max_turns: Optional[int] = None,
        tools_enabled: bool = True,
        pre_teardown_hook: Optional[callable] = None,
    ) -> TaskResult:
        """Run a single task with a specific persona and agent.

        This is the core benchmark loop implementing the 5-phase lifecycle.

        Args:
            tools_enabled: If False, no tools are passed to the agent (pure LLM conditions).
        """
        start_time = time.time()
        max_turns = max_turns or task.max_turns
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

        try:
            # === PHASE 1: RESET ===
            # 1a. Create staged directories with only the files allowed by this task
            data_files = task.environment.data_files if task.environment else []
            docs_available = task.environment.docs_available if task.environment else []
            staged_data_dir, staged_docs_dir, staged_temp_dirs = (
                self._create_staged_dirs(
                    data_files,
                    docs_available,
                )
            )

            # Detect I-series LEAN tasks and ensure data is available
            lean_data_dir = None
            sandbox_img = task.environment.sandbox_image if task.environment else ""
            if sandbox_img and "lean" in sandbox_img:
                paths = self._ensure_lean_data()
                lean_data_dir = paths.lean_data
                # Stage universe.json from HF cache into data_dir for I-series
                if paths.universe and os.path.exists(paths.universe):
                    import shutil as _shutil
                    _shutil.copy2(paths.universe, os.path.join(staged_data_dir, "universe.json"))

            # 1b. Create sandbox container (Docker or local fallback)
            container = self.container_manager.create_container(
                task_id=f"{task.task_id}_{persona.persona_id}_{run_index}",
                data_dir=staged_data_dir,
                docs_dir=staged_docs_dir,
                student_code_dir=(
                    self.student_code_dir if task.category.value == "debug" else None
                ),
                sandbox_image=(
                    task.environment.sandbox_image if task.environment else None
                ),
                network_enabled=(
                    task.environment.network_enabled if task.environment else False
                ),
                lean_data_dir=lean_data_dir,
            )

            # 1b.5. Start tool executor daemon inside the container (Docker only).
            # All core tools will be routed through this persistent process.
            if self.container_manager.use_docker:
                self.container_manager.start_executor(container.container_id)

            # 1c. Set environment vars for tool implementations (lazy reads in tools.py)
            # In Docker mode these are unused (container uses default /data etc.);
            # kept for local-mode backward compatibility.
            os.environ["QTB_DATA_DIR"] = staged_data_dir
            os.environ["QTB_DOCS_DIR"] = staged_docs_dir
            os.environ["QTB_WORKSPACE_DIR"] = container.workspace_path
            os.environ["QTB_STUDENT_CODE_DIR"] = self.student_code_dir

            # 1d. Configure MCP proxy with task-specific tools + container info
            proxy = create_proxy_for_task(
                core_tool_names=task.environment.core_mcp_tools,
                convenient_tool_names=(
                    task.ground_truth.convenient_tools if task.ground_truth else []
                ),
                seed=hash(f"{task.task_id}_{run_index}"),
                container_manager=self.container_manager,
                container_id=container.container_id,
                workspace_path=container.workspace_path,
                use_docker=self.container_manager.use_docker,
            )

            # === PHASE 1.5: INJECT DYNAMIC CONTEXT ===
            # Temporarily augment the agent's system prompt with task/persona
            # context so it knows what to teach and who the student is.
            original_system_prompt = agent.system_prompt
            dynamic_context = build_tutor_context(task, persona)
            # Use set_task_context() if available (OpenAI SDK adapter uses
            # dynamic instructions callable); fall back to direct mutation.
            if hasattr(agent, "set_task_context"):
                agent.set_task_context(dynamic_context)
            else:
                agent.system_prompt = f"{original_system_prompt}\n\n{dynamic_context}"

            # Apply per-task agent step limit (controls SDK internal loop depth)
            agent.set_agent_max_steps(task.agent_max_steps)

            # === PHASE 2: INTERACT (via DeepEval ConversationSimulator) ===
            # ConversationSimulator manages the interaction loop.
            # ConversationalGolden is built from Task + Persona.
            # model_callback wraps the Agent Under Test through MCP Proxy.
            # Termination: max_user_simulations (hard cap) OR LLM-judged
            # goal achievement (stop_conversation checks expected_outcome).
            try:
                if not DEEPEVAL_AVAILABLE:
                    raise RuntimeError(
                        "DeepEval is required for conversation simulation. "
                        "Install with: pip install deepeval"
                    )

                simulator_cost = None

                print(
                    f"  Using DeepEval ConversationSimulator (model={self.simulator_model or 'default'})..."
                )
                conversational_test_case, simulator_cost = run_conversation_simulation(
                    task=task,
                    persona=persona,
                    agent_adapter=agent,
                    proxy=proxy,
                    simulator_model=self.simulator_model or SIMULATOR_DEFAULT_MODEL,
                    max_turns=max_turns,
                    tools_enabled=tools_enabled,
                )
                # Extract turns from ConversationalTestCase into TaskResult
                for t in conversational_test_case.turns:
                    result.turns.append(
                        ConversationTurn(
                            role=t.role,
                            content=t.content,
                        )
                    )
                print(
                    f"  ConversationSimulator completed: {len(conversational_test_case.turns)} turns"
                )
            finally:
                # Restore original system prompt for next task+persona
                if hasattr(agent, "set_task_context"):
                    agent.set_task_context("")
                agent.system_prompt = original_system_prompt

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
            conversation = [
                {"role": t.role, "content": t.content} for t in result.turns
            ]
            eval_results = self._evaluate_task(
                task,
                persona,
                container.workspace_path,
                proxy,
                conversation,
                conversational_test_case=conversational_test_case,
            )
            result.quant_result_score = eval_results.get("quant_result", 0.0)
            result.quant_process_score = eval_results.get("quant_process", 0.0)
            result.tutor_scores = eval_results.get("tutor_scores", {})
            result.tutor_scores_by_model = eval_results.get("tutor_scores_by_model", {})
            result.process_metrics = eval_results.get("process_metrics", {})
            result.eval_script_detail = eval_results.get("eval_script_detail", {})
            result.code_eval = eval_results.get("code_eval", {})
            result.result_judge = eval_results.get("result_judge", {})
            result.code_process = eval_results.get("process_metrics", {}).get(
                "code_process", {}
            )

            score_breakdown = compute_task_score(
                quant_result_score=result.quant_result_score,
                quant_process_score=result.quant_process_score,
                tutor_dimension_scores=result.tutor_scores,
                category=task.category.value,
                requires_code=task.requires_code,
            )
            result.overall_score = score_breakdown["overall_score"]

            # === PHASE 5: TEARDOWN ===
            self.container_manager.destroy_container(container.container_id)

        except EvalAbortError as e:
            result.error = str(e)
            result.eval_aborted = True
            print(f"  *** EVAL ABORTED: {e}")
        except Exception as e:
            result.error = str(e)
        finally:
            # Clean up staged directories (always, even on error)
            if container is not None and result.error:
                try:
                    self.container_manager.destroy_container(container.container_id)
                except Exception:
                    pass
            self._cleanup_staged_dirs(staged_temp_dirs)

        result.duration_seconds = time.time() - start_time

        # §6.5: Aggregate cost from actual token tracking
        agent_records = agent.get_token_records()
        agent_input = sum(r.input_tokens for r in agent_records)
        agent_output = sum(r.output_tokens for r in agent_records)
        agent_cost = sum(r.cost_usd for r in agent_records)

        # Evaluator cost from _eval_cost fields injected by judge functions.
        # Note: process_metrics._eval_cost already includes code_process cost
        # (code_process is one of the tasks inside evaluate_all_process_metrics),
        # so we must NOT add code_process._eval_cost separately.
        eval_cost = 0.0
        eval_cost += result.process_metrics.get("_eval_cost", 0.0)
        eval_cost += result.result_judge.get("_eval_cost", 0.0)
        eval_cost += result.tutor_scores.get("_eval_cost", 0.0)

        # Merge per-model cost from all evaluation stages.
        # Code Process is part of Process Metrics (not a separate stage).
        eval_cost_by_model: dict[str, float] = {}
        eval_cost_by_stage_model: dict[str, dict[str, float]] = {}
        _eval_stages = [
            ("Tutor 7D", result.tutor_scores),
            ("Process Metrics", result.process_metrics),
            ("Result Judge", result.result_judge),
        ]
        for stage_name, src in _eval_stages:
            by_model = src.get("_eval_cost_by_model", {})
            if by_model:
                eval_cost_by_stage_model[stage_name] = {
                    m: round(c, 6) for m, c in by_model.items()
                }
            for m, c in by_model.items():
                eval_cost_by_model[m] = round(eval_cost_by_model.get(m, 0.0) + c, 6)

        agent_model = getattr(agent, "model", "unknown")
        sim_model_obj = self.simulator_model or SIMULATOR_DEFAULT_MODEL
        sim_model = getattr(sim_model_obj, "name", str(sim_model_obj))
        sim_cost = simulator_cost or 0.0
        result.token_usage = {
            "agent": {
                "input_tokens": agent_input,
                "output_tokens": agent_output,
                "cost_usd": round(agent_cost, 6),
                "api_calls": len(agent_records),
                "model": agent_model,
            },
            "simulator": {
                "cost_usd": round(sim_cost, 6),
                "model": sim_model,
            },
            "eval": {
                "cost_usd": round(eval_cost, 6),
                "by_model": eval_cost_by_model,
                "by_stage_model": eval_cost_by_stage_model,
            },
            "total": {
                "cost_usd": round(agent_cost + sim_cost + eval_cost, 6),
            },
        }
        result.cost_usd = round(agent_cost + sim_cost + eval_cost, 4)

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
        conversational_test_case=None,
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
            conversational_test_case: Pre-built ConversationalTestCase from simulator.
        """
        results = {
            "quant_result": 0.0,
            "quant_process": 0.0,
            "tutor_scores": {},
            "process_metrics": {},
        }

        # ── Step 1: Prepare ConversationalTestCase for metrics ──
        # Phase 4: MCP enrichment removed (no longer needed without
        # MultiTurnMCPUseMetric). Process metrics use the clean test case.
        clean_test_case = conversational_test_case

        # ── Step 2: Quant Result Score (custom eval scripts) ──
        print("  Evaluating Quant Result...")
        if task.ground_truth.quant_validation:
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

        # ── Step 2b: Code Execution QR (Phase 1) ──
        print("  Evaluating Code Execution QR...")
        reference = None  # loaded here, also used by Step 2c + Step 3b
        try:
            from evaluation.code_eval import evaluate_code_combined
            from reference.reference_store import ReferenceStore

            ref_store = ReferenceStore()
            reference = ref_store.load(task.task_id, persona.persona_id)

            code_eval_result = evaluate_code_combined(
                workspace_path=workspace_path,
                tool_logs=proxy.get_logs(),
                reference=reference,
                task_requires_code=task.requires_code,
            )
            results["code_eval"] = code_eval_result
        except Exception as e:
            results["code_eval_error"] = str(e)

        # ── Step 2c (pre): Tool Usage (mathematical, no LLM — needed by QP) ──
        tool_usage_result = None
        try:
            from evaluation.deepeval_metrics.tool_usage import evaluate_tool_usage

            tool_usage_result = evaluate_tool_usage(
                proxy_logs=proxy.get_logs(),
                expected_tools=(
                    task.ground_truth.expected_mcp_tools if task.ground_truth else []
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

        # ── Steps 2c/3/4: Parallel LLM evaluation (RJ + QP + Tutor) ──
        # These three evaluators have no cross-dependencies. Each maintains
        # its own async event loop internally. Peak concurrency ~43 requests
        # (RJ=3 + QP≤20 + Tutor≤20), well within OpenRouter limits.
        #
        # Abort mechanism: a single threading.Event is shared across all 3
        # threads.  When any evaluator call fails after exhausting retries,
        # it sets the event → queued coroutines in OTHER threads skip
        # immediately → we raise EvalAbortError for the task.
        import concurrent.futures

        _logs = proxy.get_logs()  # snapshot once for all threads
        _is_adversarial = task.category.value == "adversarial"
        _abort_event = threading.Event()

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
                conversational_test_case=clean_test_case,
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

            tutor_scores = evaluate_tutor_dimensions(
                conversation_turns=conversation,
                persona_level=persona.knowledge_level,
                scenario=build_scenario(task, persona.persona_id),
                expected_outcome=task.ground_truth.expected_outcome,
                user_description=build_user_description(persona),
                model=self.eval_model,
                category=task.category.value,
                requires_code=task.requires_code,
                abort_event=_abort_event,
            )
            per_model = tutor_scores.pop("_per_model", None)
            out: dict = {"tutor_scores": tutor_scores}
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

        print("  Running RJ / QP / Tutor in parallel...")
        _t_parallel = time.time()
        _thread_errors: list[Exception] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            fut_rj = pool.submit(_run_result_judge)
            fut_qp = pool.submit(_run_process_metrics)
            fut_tutor = pool.submit(_run_tutor_eval)

            for fut in concurrent.futures.as_completed([fut_rj, fut_qp, fut_tutor]):
                try:
                    results.update(fut.result())
                except Exception as e:
                    _abort_event.set()  # signal other threads to stop
                    _thread_errors.append(e)

        if _thread_errors:
            first = _thread_errors[0]
            raise EvalAbortError(
                f"Evaluation aborted: {len(_thread_errors)} evaluator(s) failed. "
                f"First error: {first}"
            ) from first

        print(
            f"  Parallel eval done in {time.time() - _t_parallel:.1f}s "
            f"(RJ + QP + Tutor)"
        )

        # ── Step 2d: Combine QR components (30/30/40 blend) ──
        # Must run after RJ completes (needs result_judge score).
        programmatic_score = results["quant_result"]
        code_eval_score = results.get("code_eval", {}).get("score", 0.0)
        code_eval_applicable = results.get("code_eval", {}).get("applicable", False)
        llm_judge_score = results.get("result_judge", {}).get("score", 0.0)

        # Pure-refusal adversarial tasks (requires_code=false): skip code_eval.
        # Educational adversarial tasks (requires_code=true): allow code_eval
        # to evaluate the quality of redirected educational code.
        if _is_adversarial and not task.requires_code:
            code_eval_applicable = False

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

        return results

    def _create_staged_dirs(
        self,
        data_files: list[str],
        docs_available: list[str],
    ) -> tuple[str, str, list[str]]:
        """Create temp directories with symlinks to only the allowed files.

        Args:
            data_files: List of allowed data file names (e.g. ["AAPL_2018_2024.csv"]).
            docs_available: List of allowed doc file names (e.g. ["moving_averages.md"]).

        Returns:
            (staged_data_dir, staged_docs_dir, temp_dirs_to_cleanup)
        """
        temp_dirs: list[str] = []

        if data_files:
            staged_data = tempfile.mkdtemp(prefix="qtb_data_")
            temp_dirs.append(staged_data)
            for fname in data_files:
                src = os.path.join(self.data_dir, fname)
                if os.path.isfile(src):
                    dst = os.path.join(staged_data, fname)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
        else:
            staged_data = self.data_dir  # No filter → full access (backward compat)

        if docs_available:
            staged_docs = tempfile.mkdtemp(prefix="qtb_docs_")
            temp_dirs.append(staged_docs)
            for fname in docs_available:
                src = os.path.join(self.docs_dir, fname)
                if os.path.isfile(src):
                    dst = os.path.join(staged_docs, fname)
                    shutil.copy2(src, dst)
        else:
            staged_docs = self.docs_dir

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
