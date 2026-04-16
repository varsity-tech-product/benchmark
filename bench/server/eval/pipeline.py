"""Standalone evaluation pipeline for QuantTutorBench.

Extracted from ``orchestrator.BenchmarkOrchestrator._evaluate_task()`` to
enable evaluation without depending on the orchestrator or any agent adapter.

Only two attributes from the original class are needed:
- ``bench_root`` (Path) — to locate eval scripts
- ``eval_model`` (str) — passed to LLM-based evaluators

Usage::

    from server.eval.pipeline import evaluate_task

    results = evaluate_task(
        task=task, persona=persona,
        workspace_path="/path/to/agent_files",
        conversation=[{"role": "user", "content": "..."}, ...],
        tool_logs=[ToolCallLog(...)],
        distractor_names=["search_web"],
        bench_root="/path/to/bench",
        eval_model="anthropic/claude-sonnet-4-6",
    )
"""

import concurrent.futures
import logging
import math
import threading
from pathlib import Path

logger = logging.getLogger(__name__)


class EvalAbortError(RuntimeError):
    """Evaluation failed — abort this task only.

    Aligned with orchestrator.EvalAbortError.
    """

    pass


def evaluate_task(
    task,
    persona,
    workspace_path: str,
    conversation: list[dict],
    tool_logs: list,
    distractor_names: list[str],
    bench_root: str,
    eval_model: str,
    cancel_event=None,
    eval_mode: str = "full",
    tutor_dims: list[str] | None = None,
) -> dict:
    """Run full evaluation on a completed task.

    This is a standalone extraction of ``orchestrator._evaluate_task()``.
    All logic is identical; the only change is replacing ``self.bench_root``
    and ``self.eval_model`` with explicit parameters.

    Args:
        task: QuantTutorTask instance.
        persona: StudentPersona instance.
        workspace_path: Path to workspace / agent_files directory.
        conversation: [{role, content}, ...] conversation history.
        tool_logs: List of ToolCallLog (or dicts with same fields).
        distractor_names: Names of distractor tools (for tool_usage scoring).
        bench_root: Path to bench/ root (for locating eval scripts).
        eval_model: Model name for LLM-based evaluators.
        cancel_event: Optional threading.Event for cancellation.
        eval_mode: "full" | "qr_only" | "qp_only" | "tutor_only".
        tutor_dims: Optional list of tutor dimensions to evaluate
            (e.g. ["D3_scaffolding_calibration", "D4_domain_accuracy"]).
            If None, evaluates all dimensions with non-zero weight.

    Returns:
        Dict with keys: quant_result, quant_process, tutor_scores,
        process_metrics, and optional detail keys.
    """
    bench_root = Path(bench_root)

    def _check_cancel():
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Evaluation cancelled")

    # Proxy-like interface for evaluation code that calls proxy.get_logs()
    class _LogsAccessor:
        def __init__(self, logs, distractors):
            self._logs = logs
            self._distractors = distractors

        def get_logs(self):
            return self._logs

        def get_distractor_names(self):
            return self._distractors

    proxy = _LogsAccessor(tool_logs, distractor_names)

    results = {
        "quant_result": 0.0,
        "quant_process": 0.0,
        "tutor_scores": {},
        "process_metrics": {},
    }

    _run_qr = eval_mode in ("full", "qr_only")
    _run_qp = eval_mode in ("full", "qp_only")
    _run_tutor = eval_mode in ("full", "tutor_only")

    # ── Step 2: Quant Result Score (custom eval scripts) ──
    if _run_qr and task.ground_truth and task.ground_truth.quant_validation:
        eval_script = bench_root / task.ground_truth.quant_validation.eval_script
        if eval_script.exists():
            try:
                import importlib.util
                import inspect

                spec = importlib.util.spec_from_file_location(
                    "eval_module", str(eval_script)
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)

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

    _check_cancel()

    # ── Step 2b: Code Execution QR ──
    reference = None
    try:
        from server.eval.reference_store import ReferenceStore

        ref_store = ReferenceStore()
        reference = ref_store.load(task.task_id, persona.persona_id)

        if _run_qr:
            from server.eval.code_eval import evaluate_code_combined

            _sandbox_img = task.environment.sandbox_image if task.environment else ""
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

    _check_cancel()

    # ── Step 2c (pre): Tool Usage ──
    tool_usage_result = None
    if _run_qp:
        try:
            from server.eval.ewan_eval.tool_usage import evaluate_tool_usage

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

    _check_cancel()

    # ── Steps 2c/3/4: Parallel LLM evaluation (RJ + QP + Tutor) ──
    _logs = proxy.get_logs()
    _is_adversarial = task.category.value == "adversarial"
    _abort_event = threading.Event()

    def _run_result_judge() -> dict:
        from server.eval.ewan_eval.result_judge import evaluate_result_quality

        rj_result = evaluate_result_quality(
            task_description=task.description,
            category=task.category.value,
            workspace_path=workspace_path,
            tool_logs=_logs,
            conversation=conversation,
            model=eval_model,
            reference=reference,
            expected_outcome=(
                task.ground_truth.expected_outcome if task.ground_truth else None
            ),
            abort_event=_abort_event,
        )
        return {"result_judge": rj_result}

    def _run_process_metrics() -> dict:
        from server.eval.ewan_eval.process_metrics import (
            evaluate_all_process_metrics,
        )

        agent_outputs = [t["content"] for t in conversation if t["role"] == "assistant"]
        combined_output = "\n---\n".join(agent_outputs) if agent_outputs else ""

        process_results = evaluate_all_process_metrics(
            task_description=task.description,
            actual_output=combined_output,
            proxy_logs=_logs,
            category=task.category.value,
            conversation=conversation,
            model=eval_model,
            reference_trace=reference,
            is_adversarial=_is_adversarial,
            tool_usage_result=tool_usage_result,
            task_requires_code=task.requires_code,
            abort_event=_abort_event,
        )
        return {
            "process_metrics": process_results,
            "quant_process": round(
                process_results.get("aggregate_process_score", 0.5), 4
            ),
        }

    def _run_tutor_eval() -> dict:
        from server.config.prompt_config import build_scenario, build_user_description
        from server.eval.ewan_eval.tutor_conv_geval import (
            evaluate_tutor_dimensions,
        )

        # Multi-tier conversation enrichment:
        # - Full: D4/D5/D7 (tool names + truncated args + truncated results)
        # - Lightweight: D3 (tool names + status only, no content)
        enriched_conv = _enrich_conversation_with_tools(
            conversation, _logs, mode="full"
        )
        enriched_conv_lightweight = _enrich_conversation_with_tools(
            conversation, _logs, mode="lightweight"
        )

        _tutor_kwargs = {}
        if tutor_dims:
            _tutor_kwargs["dimension_order"] = tutor_dims

        tutor_scores = evaluate_tutor_dimensions(
            conversation_turns=conversation,
            enriched_conversation_turns=enriched_conv,
            enriched_conversation_turns_lightweight=enriched_conv_lightweight,
            persona_level=persona.knowledge_level,
            scenario=build_scenario(task, persona.persona_id),
            expected_outcome=task.ground_truth.expected_outcome,
            user_description=build_user_description(persona),
            model=eval_model,
            category=task.category.value,
            requires_code=task.requires_code,
            abort_event=None,
            **_tutor_kwargs,
        )
        fallback_count = tutor_scores.pop("_fallback_count", 0)
        per_model = tutor_scores.pop("_per_model", None)
        out: dict = {"tutor_scores": tutor_scores}
        if fallback_count > 0:
            out["tutor_fallback_count"] = fallback_count
        if per_model:
            out["tutor_scores_by_model"] = per_model
        return out

    _check_cancel()

    _thread_errors: list[Exception] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        fut_rj = pool.submit(_run_result_judge) if _run_qr else None
        fut_qp = pool.submit(_run_process_metrics) if _run_qp else None
        fut_tutor = pool.submit(_run_tutor_eval) if _run_tutor else None

        _futures = [f for f in (fut_rj, fut_qp, fut_tutor) if f is not None]
        for fut in concurrent.futures.as_completed(_futures):
            try:
                results.update(fut.result())
            except Exception as e:
                if fut is fut_tutor:
                    results["tutor_scores"] = {}
                    results["tutor_eval_error"] = str(e)
                else:
                    _abort_event.set()
                    _thread_errors.append(e)

    if _thread_errors:
        first = _thread_errors[0]
        raise EvalAbortError(
            f"Evaluation aborted: {len(_thread_errors)} evaluator(s) failed. "
            f"First error: {first}"
        ) from first

    _check_cancel()

    if not _run_qr:
        return results

    if eval_mode == "tutor_only":
        return results

    # ── Step 2d: QR blending (30/30/40 with dampening) ──
    programmatic_score = results["quant_result"]
    code_eval_score = results.get("code_eval", {}).get("score", 0.0)
    code_eval_applicable = results.get("code_eval", {}).get("applicable", False)
    llm_judge_score = results.get("result_judge", {}).get("score", 0.0)

    if not task.requires_code:
        code_eval_applicable = False

    if programmatic_score is None:
        if code_eval_applicable:
            results["quant_result"] = round(
                0.30 * code_eval_score + 0.70 * llm_judge_score, 4
            )
        else:
            results["quant_result"] = round(llm_judge_score, 4)
        rj = results.get("result_judge")
        if isinstance(rj, dict):
            rj["_eval_script_score"] = None
            rj["_dampening_factor"] = None
        return results

    divergence = abs(programmatic_score - llm_judge_score)
    dampening_factor = 1.0 / (1.0 + math.exp(10 * (divergence - 0.40)))

    if code_eval_applicable:
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
        w_prog = 0.15 + 0.25 * dampening_factor
        w_judge = 1.0 - w_prog
        results["quant_result"] = round(
            w_prog * programmatic_score + w_judge * llm_judge_score, 4
        )

    rj = results.get("result_judge")
    if isinstance(rj, dict):
        rj["_eval_script_score"] = programmatic_score
        rj["_dampening_factor"] = round(dampening_factor, 4)

    return results


# ---------------------------------------------------------------------------
# Tool enrichment — shared with server.eval.enrichment.
# ---------------------------------------------------------------------------

from server.eval.enrichment import (
    enrich_conversation_with_tools as _enrich_conversation_with_tools,
)
