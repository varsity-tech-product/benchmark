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
import time
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.llm_config import SIMULATOR_DEFAULT_MODEL, resolve_deepeval_model
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
    run_conversation_manual,
    run_conversation_simulation,
)

try:
    from deepeval.test_case import ConversationalTestCase  # noqa: F401

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


class BenchmarkOrchestrator:
    """Orchestrates the full benchmark evaluation lifecycle."""

    def __init__(
        self,
        bench_root: Optional[str] = None,
        use_docker: bool = False,
        max_concurrent: int = 1,
        use_deepeval: bool = True,
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
        self.max_concurrent = max_concurrent
        self.use_deepeval = use_deepeval
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
            )

            # 1c. Set environment vars for tool implementations (lazy reads in tools.py)
            os.environ["QTB_DATA_DIR"] = staged_data_dir
            os.environ["QTB_DOCS_DIR"] = staged_docs_dir
            os.environ["QTB_WORKSPACE_DIR"] = container.workspace_path
            os.environ["QTB_STUDENT_CODE_DIR"] = self.student_code_dir

            # 1d. Configure MCP proxy with task-specific tools + container info
            proxy = create_proxy_for_task(
                core_tool_names=task.environment.core_mcp_tools,
                distractor_pool=task.environment.distractor_mcp_tools_pool,
                num_distractors=task.environment.num_distractors,
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

            # === PHASE 2: INTERACT (via DeepEval ConversationSimulator or manual fallback) ===
            # Design doc §4.3: ConversationSimulator manages the interaction loop.
            # ConversationalGolden is built from Task + Persona (§4.7).
            # model_callback wraps the Agent Under Test through MCP Proxy.
            try:
                conversational_test_case = None

                if self.use_deepeval and DEEPEVAL_AVAILABLE:
                    try:
                        print(
                            f"  Using DeepEval ConversationSimulator (model={self.simulator_model or 'default'})..."
                        )
                        conversational_test_case = run_conversation_simulation(
                            task=task,
                            persona=persona,
                            agent_adapter=agent,
                            proxy=proxy,
                            simulator_model=self.simulator_model
                            or SIMULATOR_DEFAULT_MODEL,
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
                    except Exception as e:
                        print(
                            f"  ConversationSimulator failed ({e}), falling back to manual mode"
                        )
                        conversational_test_case = None

                if conversational_test_case is None:
                    # Fallback: manual conversation loop via simulator_config
                    # Uses simple student simulation (hardcoded responses by persona level)
                    print("  Using manual conversation mode...")
                    conv_turns = run_conversation_manual(
                        task=task,
                        persona=persona,
                        agent_adapter=agent,
                        proxy=proxy,
                        max_turns=max_turns,
                        tools_enabled=tools_enabled,
                    )
                    for t in conv_turns:
                        result.turns.append(
                            ConversationTurn(
                                role=t["role"],
                                content=t["content"],
                            )
                        )
            finally:
                # Restore original system prompt for next task+persona
                if hasattr(agent, "set_task_context"):
                    agent.set_task_context("")
                agent.system_prompt = original_system_prompt

            # === PHASE 3: CAPTURE ===
            # Capture workspace file list before teardown destroys the container.
            if container.workspace_path and os.path.isdir(container.workspace_path):
                result.workspace_files = sorted(
                    f
                    for f in os.listdir(container.workspace_path)
                    if os.path.isfile(os.path.join(container.workspace_path, f))
                )

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
            )
            result.overall_score = score_breakdown["overall_score"]

            # === PHASE 5: TEARDOWN ===
            self.container_manager.destroy_container(container.container_id)

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

        # §6.5: Estimate cost per task (rough: ~4 chars per token, $0.001 per 1K tokens)
        total_chars = sum(len(t.content) for t in result.turns)
        estimated_tokens = total_chars / 4.0
        # Rough cost estimate: input+output + judge runs (3x7 judge calls)
        agent_cost = estimated_tokens * 0.001 / 1000
        judge_cost = estimated_tokens * 0.003 / 1000 * 21  # 3 runs × 7 dims
        result.cost_usd = round(agent_cost + judge_cost, 4)

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
        - Quant Process: DeepEval MCP metrics + tool precision/recall
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

                    spec = importlib.util.spec_from_file_location(
                        "eval_module", str(eval_script)
                    )
                    module = importlib.util.module_from_spec(spec)
                    spec.loader.exec_module(module)
                    eval_result = module.evaluate(
                        workspace_path, proxy.get_logs(), conversation
                    )
                    results["quant_result"] = eval_result.get("score", 0.0)
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

        # ── Step 2c: LLM Result Judge (Phase 3) ──
        print("  Evaluating Result Quality (LLM judge)...")
        try:
            from evaluation.deepeval_metrics.result_judge import (
                evaluate_result_quality,
            )

            result_judge_result = evaluate_result_quality(
                task_description=task.description,
                category=task.category.value,
                workspace_path=workspace_path,
                tool_logs=proxy.get_logs(),
                conversation=conversation,
                model=self.eval_model,
                reference=reference,
            )
            results["result_judge"] = result_judge_result
        except Exception as e:
            results["result_judge_error"] = str(e)

        # ── Step 2d: Combine QR components (30/30/40 blend) ──
        programmatic_score = results["quant_result"]
        code_eval_score = results.get("code_eval", {}).get("score", 0.0)
        code_eval_applicable = results.get("code_eval", {}).get("applicable", False)
        llm_judge_score = results.get("result_judge", {}).get("score", 0.0)

        if code_eval_applicable:
            # Full 3-component blend: 30% programmatic + 30% code_eval + 40% LLM judge
            results["quant_result"] = round(
                0.30 * programmatic_score
                + 0.30 * code_eval_score
                + 0.40 * llm_judge_score,
                4,
            )
        else:
            # No code_eval: redistribute to 40% programmatic + 60% LLM judge
            results["quant_result"] = round(
                0.40 * programmatic_score + 0.60 * llm_judge_score, 4
            )

        # ── Step 3: Quant Process Score (Reformed Process Metrics) ──
        print("  Evaluating Quant Process...")

        # 3b: DeepEval process-level metrics
        if self.use_deepeval:
            try:
                from evaluation.deepeval_metrics.process_metrics import (
                    evaluate_all_process_metrics,
                )

                # Build combined agent output for single-turn metrics
                agent_outputs = [
                    t["content"] for t in conversation if t["role"] == "assistant"
                ]
                combined_output = "\n---\n".join(agent_outputs) if agent_outputs else ""

                process_results = evaluate_all_process_metrics(
                    task_description=task.description,
                    actual_output=combined_output,
                    proxy_logs=proxy.get_logs(),
                    category=task.category.value,
                    conversational_test_case=clean_test_case,
                    model=self.eval_model,
                    reference_trace=reference,
                    is_adversarial=(task.category.value == "adversarial"),
                )
                results["process_metrics"] = process_results

                # QP = deepeval aggregate (reformed process metrics).
                results["quant_process"] = round(
                    process_results.get("aggregate_process_score", 0.5), 4
                )
            except Exception as e:
                results["process_eval_error"] = str(e)
                results["quant_process"] = 0.5
        else:
            results["quant_process"] = 0.5

        # ── Step 4: Tutor Quality Score (7D ConversationalGEval) ──
        print("  Evaluating Tutor Quality (7D rubric, multi-model × 3x shuffled)...")
        if self.use_deepeval:
            try:
                from evaluation.deepeval_metrics.tutor_conv_geval import (
                    evaluate_tutor_dimensions,
                )

                # Use conversation_turns (text only) so the 7D rubric judge
                # isn't distracted by tool execution metadata.
                tutor_scores = evaluate_tutor_dimensions(
                    conversation_turns=conversation,
                    persona_level=persona.knowledge_level,
                    scenario=build_scenario(task, persona.persona_id),
                    expected_outcome=task.ground_truth.expected_outcome,
                    user_description=build_user_description(persona),
                    model=self.eval_model,
                    category=task.category.value,
                )
                # Extract per-model breakdown before storing dimension scores
                per_model = tutor_scores.pop("_per_model", None)
                results["tutor_scores"] = tutor_scores
                if per_model:
                    results["tutor_scores_by_model"] = per_model
                    print("  Per-model tutor scores:")
                    for mname, dim_scores in per_model.items():
                        avg = (
                            sum(dim_scores.values()) / len(dim_scores)
                            if dim_scores
                            else 0.0
                        )
                        print(f"    {mname}: avg={avg:.4f}")
                        for dim, sc in sorted(dim_scores.items()):
                            print(f"      {dim}: {sc:.4f}")
            except Exception as e:
                results["tutor_eval_error"] = str(e)
                for dim in [
                    "D1_level_detection",
                    "D2_language_adaptation",
                    "D3_scaffolding_calibration",
                    "D4_domain_accuracy",
                    "D5_code_teaching",
                    "D6_empathetic_response",
                    "D7_safety_boundaries",
                ]:
                    results["tutor_scores"][dim] = 0.5
        else:
            for dim in [
                "D1_level_detection",
                "D2_language_adaptation",
                "D3_scaffolding_calibration",
                "D4_domain_accuracy",
                "D5_code_teaching",
                "D6_empathetic_response",
                "D7_safety_boundaries",
            ]:
                results["tutor_scores"][dim] = 0.5

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
                    os.symlink(src, os.path.join(staged_data, fname))
        else:
            staged_data = self.data_dir  # No filter → full access (backward compat)

        if docs_available:
            staged_docs = tempfile.mkdtemp(prefix="qtb_docs_")
            temp_dirs.append(staged_docs)
            for fname in docs_available:
                src = os.path.join(self.docs_dir, fname)
                if os.path.isfile(src):
                    os.symlink(src, os.path.join(staged_docs, fname))
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
