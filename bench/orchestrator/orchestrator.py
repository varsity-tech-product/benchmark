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
    MCPToolCallRecord,
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
        # Resolve model names for DeepEval (strips "openai/" when using native OpenAI API)
        self.eval_model = resolve_deepeval_model(eval_model)
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
            dimension_relevance=task.dimension_relevance or {},
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
            requested_network = (
                bool(task.environment.network_enabled) if task.environment else False
            )
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
                network_enabled=requested_network,
            )
            result.sandbox_info = {
                "requested_network_enabled": requested_network,
                "effective_network_enabled": bool(container.network_enabled),
                "network_mode": container.network_mode,
                "use_docker": bool(self.container_manager.use_docker),
            }
            print(
                f"  Sandbox network: requested={requested_network} "
                f"effective={container.network_enabled} mode={container.network_mode}"
            )
            if requested_network:
                probe = self._probe_container_network(container.container_id)
                result.sandbox_info["network_probe"] = probe
                print(
                    "  Network probe:",
                    (
                        "ok"
                        if probe.get("ok")
                        else f"failed ({probe.get('error', 'unknown')})"
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
                            tool_calls = []
                            if hasattr(t, "tools_called") and t.tools_called:
                                tool_calls = [
                                    MCPToolCallRecord(
                                        name=tc.name,
                                        args=tc.input_parameters or {},
                                        result=tc.output or "",
                                    )
                                    for tc in t.tools_called
                                ]
                            result.turns.append(
                                ConversationTurn(
                                    role=t.role,
                                    content=t.content,
                                    tool_calls=tool_calls,
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
            result.tool_call_log = [
                MCPToolCallRecord(
                    name=entry.name,
                    args=entry.args,
                    result=entry.result[:500],
                    timestamp=str(entry.timestamp),
                    duration_ms=entry.duration_ms,
                    success=entry.success,
                    turn_index=entry.turn_index,
                )
                for entry in proxy.get_logs()
            ]

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
            result.tool_metrics = eval_results.get("tool_metrics", {})
            result.process_metrics = eval_results.get("process_metrics", {})

            score_breakdown = compute_task_score(
                quant_result_score=result.quant_result_score,
                quant_process_score=result.quant_process_score,
                tutor_dimension_scores=result.tutor_scores,
                category=task.category.value,
                dimension_relevance=task.dimension_relevance or None,
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
                dimension_relevance=(r.dimension_relevance or None),
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
            report.tool_mastery_score = kpis.get("tool_mastery_score", 0.0)

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
        from orchestrator.trace_assembler import (
            enrich_test_case_with_mcp,
        )

        results = {
            "quant_result": 0.0,
            "quant_process": 0.0,
            "tutor_scores": {},
            "process_metrics": {},
            "tool_metrics": {},
        }

        # ── Step 1: Build/enrich ConversationalTestCase with MCP data ──
        # If we got a test case from ConversationSimulator, enrich it with MCP data.
        # Otherwise, build one from the task result.
        # Keep a clean copy for process metrics (role_adherence, knowledge_retention,
        # topic_adherence) that don't need synthetic tool execution turns.
        if conversational_test_case is not None and DEEPEVAL_AVAILABLE:
            import copy

            clean_test_case = copy.deepcopy(conversational_test_case)
            try:
                enrich_test_case_with_mcp(
                    test_case=conversational_test_case,
                    proxy_logs=proxy.get_logs(),
                    core_tools=task.environment.core_mcp_tools,
                    distractor_tools=task.environment.distractor_mcp_tools_pool,
                    tool_schemas=proxy.get_available_tools(),
                )
            except Exception as e:
                print(f"  Warning: Failed to enrich test case with MCP data: {e}")
            # Copy MCP metadata to clean test case (for multi_turn_mcp)
            # but don't copy the synthetic turns
            for attr in ("mcp_tools_called", "mcp_servers"):
                if hasattr(conversational_test_case, attr):
                    setattr(
                        clean_test_case, attr, getattr(conversational_test_case, attr)
                    )
        else:
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
                    eval_context = {
                        "persona_id": persona.persona_id,
                        "persona_level": persona.knowledge_level,
                        "task_id": task.task_id,
                        "task_tags": list(task.tags or []),
                        "category": task.category.value,
                    }
                    sig = inspect.signature(module.evaluate)
                    params = sig.parameters
                    supports_eval_context = "eval_context" in params or any(
                        p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
                    )
                    if supports_eval_context:
                        eval_result = module.evaluate(
                            workspace_path,
                            proxy.to_dict(),
                            conversation,
                            eval_context=eval_context,
                        )
                    else:
                        eval_result = module.evaluate(
                            workspace_path, proxy.to_dict(), conversation
                        )
                    results["quant_result"] = eval_result.get("score", 0.0)
                except Exception as e:
                    results["quant_result_error"] = str(e)

        # ── Step 3: Quant Process Score (DeepEval MCP metrics + precision/recall) ──
        print("  Evaluating Quant Process...")

        # 3a: Manual tool precision/recall (always available)
        from evaluation.deepeval_metrics.mcp_metrics import (
            check_required_capabilities,
            compute_optional_tool_value,
            compute_tool_precision_recall,
        )

        track_a_optional_tools = "track_a_optional_tools" in (task.tags or [])
        called_tool_names = proxy.get_tool_names_called()
        tool_metrics = compute_tool_precision_recall(
            called_tools=called_tool_names,
            expected_tools=task.ground_truth.expected_mcp_tools,
            distractor_tools=task.environment.distractor_mcp_tools_pool,
        )

        # 3a-2: Capability completion check (§6.1.2)
        tool_call_outputs = {}
        for log in proxy.get_logs():
            tool_call_outputs.setdefault(log.name, "")
            tool_call_outputs[log.name] += (log.result or "") + "\n"
        cap_check = check_required_capabilities(
            called_tools=called_tool_names,
            tool_call_outputs=tool_call_outputs,
            required_capabilities=[
                cap.model_dump() for cap in task.ground_truth.required_capabilities
            ],
        )
        tool_metrics["capability_completion"] = cap_check["capability_completion"]
        tool_metrics["capabilities_met"] = cap_check["met"]
        tool_metrics["capabilities_total"] = cap_check["total"]

        optional_tool_value = {}
        if track_a_optional_tools:
            optional_tool_value = compute_optional_tool_value(
                proxy_logs=proxy.get_logs(),
                core_tools=task.environment.core_mcp_tools,
                distractor_tools=task.environment.distractor_mcp_tools_pool,
            )
            tool_metrics["optional_tool_mode"] = True
            tool_metrics.update(
                {
                    "tool_bonus": optional_tool_value.get("bonus", 0.0),
                    "tool_penalty": optional_tool_value.get("penalty", 0.0),
                    "tool_value_score": optional_tool_value.get(
                        "tool_value_score", 0.0
                    ),
                    "used_tools": optional_tool_value.get("used_tools", False),
                }
            )

        results["tool_metrics"] = tool_metrics

        def _track_a_process_score(base_score: float) -> float:
            bonus = float(optional_tool_value.get("bonus", 0.0))
            penalty = float(optional_tool_value.get("penalty", 0.0))
            qp = float(base_score) + bonus - penalty
            return round(max(0.0, min(1.0, qp)), 4)

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
                    expected_tool_names=task.ground_truth.expected_mcp_tools,
                    core_tools=task.environment.core_mcp_tools,
                    distractor_tools=task.environment.distractor_mcp_tools_pool,
                    conversational_test_case=clean_test_case,
                    enriched_test_case=conversational_test_case,
                    model=self.eval_model,
                    tool_schemas=proxy.get_available_tools(),
                    optional_tool_bonus_mode=track_a_optional_tools,
                )
                results["process_metrics"] = process_results

                deepeval_process = process_results.get("aggregate_process_score", 0.5)
                if track_a_optional_tools:
                    results["quant_process"] = _track_a_process_score(deepeval_process)
                else:
                    # Combine: 50% tool F1 + 50% DeepEval aggregate
                    # F1 balances precision (avoid distractor calls) and recall
                    # (cover expected tools). Distractor tools are traps that
                    # always return errors — agents must learn to avoid them.
                    tool_score = tool_metrics["f1"]
                    results["quant_process"] = round(
                        0.5 * tool_score + 0.5 * deepeval_process, 4
                    )
            except Exception as e:
                results["process_eval_error"] = str(e)
                if track_a_optional_tools:
                    results["quant_process"] = _track_a_process_score(0.5)
                else:
                    results["quant_process"] = tool_metrics["f1"]
        else:
            if track_a_optional_tools:
                results["quant_process"] = _track_a_process_score(0.5)
            else:
                results["quant_process"] = tool_metrics["f1"]

        # ── Step 4: Tutor Quality Score (7D ConversationalGEval) ──
        print("  Evaluating Tutor Quality (7D rubric, 3x shuffled)...")
        if self.use_deepeval:
            try:
                from evaluation.deepeval_metrics.tutor_conv_geval import (
                    evaluate_tutor_dimensions,
                )

                # NOTE: Do NOT pass the enriched conversational_test_case here.
                # enrich_test_case_with_mcp() inserts synthetic
                # "[Executed tools: ...]" turns that the 7D rubric judge
                # interprets as empty teaching — tanking D3/D5/D6 scores.
                # Instead, let evaluate_tutor_dimensions build a clean
                # ConversationalTestCase from conversation_turns (text only).
                tutor_scores = evaluate_tutor_dimensions(
                    conversation_turns=conversation,
                    persona_level=persona.knowledge_level,
                    scenario=build_scenario(task, persona.persona_id),
                    expected_outcome=task.ground_truth.expected_outcome,
                    user_description=build_user_description(persona),
                    model=self.eval_model,
                    category=task.category.value,
                    dimension_relevance=task.dimension_relevance or None,
                )
                results["tutor_scores"] = tutor_scores
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

    def _probe_container_network(self, container_id: str) -> dict:
        """Probe outbound network access for progress/result recording."""
        probe_cmd = (
            'python -c "import urllib.request; '
            "urllib.request.urlopen('https://example.com', timeout=5).read(64); "
            "print('ok')\""
        )
        exec_result = self.container_manager.exec_in_container(
            container_id, probe_cmd, timeout=10
        )
        ok = exec_result.exit_code == 0 and "ok" in (exec_result.stdout or "").lower()
        if ok:
            return {"ok": True}
        details = (exec_result.stderr or exec_result.stdout or "").strip()
        return {"ok": False, "error": details[:300]}

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
            # data_root is the parent of data_dir (bench/data/) — used as
            # fallback for files outside the default frozen/ subdirectory
            # (e.g. adversarial/malicious_backtest.py lives in bench/data/adversarial/).
            data_root = os.path.dirname(self.data_dir)
            for fname in data_files:
                src = os.path.join(self.data_dir, fname)
                if not os.path.isfile(src):
                    src = os.path.join(data_root, fname)
                if os.path.isfile(src):
                    # Preserve the declared relative path (so file_read can use
                    # e.g. "adversarial/malicious_backtest.py"), and also place
                    # a flat basename alias for convenience.
                    dst_rel = os.path.join(staged_data, fname)
                    os.makedirs(os.path.dirname(dst_rel), exist_ok=True)
                    if not os.path.exists(dst_rel):
                        os.symlink(src, dst_rel)

                    dst_flat = os.path.join(staged_data, os.path.basename(fname))
                    if dst_flat != dst_rel and not os.path.exists(dst_flat):
                        os.symlink(src, dst_flat)
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
