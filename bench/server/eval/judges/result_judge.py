"""LLM-as-Judge for Quant Result quality — reformed.

Evaluates deliverable quality using a 1-5 progressive rubric anchored to
required_capabilities (RC). Uses EwanConvGEval for unified LLM call,
JSON parsing, normalization, and error handling.

Rubric source: rubrics/rubric_qr.json
Context source: context_builder.build_result_judge_context()
Normalization: (score - 1) / 4 → {0, 0.25, 0.5, 0.75, 1.0}
"""

import threading

from server.eval.inputs.context_builder import build_result_judge_context
from server.eval.inputs.rubric_builder import build_eval_params, load_rubric
from server.eval.judges.runtime.async_utils import (
    ABORT_SENTINEL,
    guarded_gather,
    run_async,
)
from server.eval.judges.runtime.call_policy import llm_call_with_retry
from server.eval.judges.runtime.conv_geval import EvalTestCase, EwanConvGEval
from server.eval.judges.runtime.model_resolver import (
    resolve_eval_model_list,
    resolve_ewan_model,
    short_model_name,
)

# ──────────────────────────────────────────────────────────────
# Main evaluation entry point
# ──────────────────────────────────────────────────────────────


async def async_evaluate_result_quality(
    task_description: str,
    category: str,
    workspace_path: str,
    tool_logs: list,
    conversation: list,
    model=None,
    reference: dict | None = None,
    required_capabilities: list[str] | None = None,
    abort_event: threading.Event | None = None,
) -> dict:
    """Evaluate result quality using LLM-as-Judge (multi-model).

    Calls all EVAL_DEFAULT_MODELS in parallel and averages scores.

    Args:
        task_description: The task's description text.
        category: Task category (e.g. "implementation", "data_analysis").
        workspace_path: Path to agent's workspace directory.
        tool_logs: Tool call logs (list of ToolCallLog from proxy.get_logs()).
        conversation: Full conversation (list of {role, content} dicts).
        model: LLM judge model — single string, list of strings, or None.
            None → use all EVAL_DEFAULT_MODELS in parallel.
        reference: Reference execution data from ReferenceStore, or None.
        required_capabilities: RC checklist from task ground_truth.
        abort_event: Shared threading.Event for cross-thread abort signaling.

    Returns:
        Dict with score, reason, has_reference, and _per_model breakdown.
    """
    # ── Resolve model list ──
    eval_models, multi_model = resolve_eval_model_list(model)

    # ── Build context via context_builder ──
    rc_list = required_capabilities or []
    context = build_result_judge_context(
        task_description=task_description,
        category=category,
        rc_list=rc_list,
        tool_logs=tool_logs,
        workspace_path=workspace_path,
        conversation=conversation,
        reference=reference,
    )
    test_case = EvalTestCase(context=context)

    # ── Load rubric and build eval params ──
    rubric = load_rubric("qr")
    params = build_eval_params(rubric, "result_judge")

    # ── Call all models in parallel with abort protection ──
    async def _call_single_model(m):
        return await llm_call_with_retry(
            lambda: EwanConvGEval(
                name="result_judge",
                model=resolve_ewan_model(m),
                **params,
            ),
            test_case,
            dimension_name="result_judge",
        )

    coros = [_call_single_model(m) for m in eval_models]
    raw_results, _first_error = await guarded_gather(coros, abort_event=abort_event)

    if _first_error:
        raise _first_error[0]

    # ── Parse per-model results ──
    per_model: dict[str, dict] = {}
    total_eval_cost = 0.0
    cost_by_model: dict[str, float] = {}
    for i, m in enumerate(eval_models):
        raw = raw_results[i]
        if raw is ABORT_SENTINEL:
            raise RuntimeError(f"ResultJudge: unexpected abort for model {m}")
        if isinstance(raw, Exception):
            raise raw
        m_cost = raw.get("_eval_cost", 0.0)
        cost_by_model[m] = round(m_cost, 6)
        total_eval_cost += m_cost
        per_model[m] = {
            "score": raw.get("score"),
            "status": raw.get("status", "success"),
            "reason": raw.get("reason", ""),
            "evidence": raw.get("evidence", []),
        }
        if raw.get("error"):
            per_model[m]["error"] = raw.get("error")
        if raw.get("diagnostics"):
            per_model[m]["diagnostics"] = raw.get("diagnostics")

    failed = {m: data for m, data in per_model.items() if data.get("score") is None}
    if failed:
        return {
            "score": None,
            "status": "failed",
            "required_for_track_score": True,
            "reason": "; ".join(
                f"{short_model_name(m)}: {data.get('error') or data.get('reason')}"
                for m, data in failed.items()
            ),
            "evidence": [],
            "has_reference": reference is not None,
            "per_model": per_model,
            "_eval_cost": total_eval_cost,
            "_eval_cost_by_model": cost_by_model,
        }

    # ── Cross-model average ──
    avg_score = round(
        sum(per_model[m]["score"] for m in eval_models) / len(eval_models), 4
    )

    # Log per-model scores
    if multi_model:
        model_parts = []
        for m in eval_models:
            model_parts.append(f"{short_model_name(m)}={per_model[m]['score']}")
        print(f"    ResultJudge per-model: {', '.join(model_parts)}")

    result = {
        "score": avg_score,
        "status": "success",
        "required_for_track_score": True,
        "reason": per_model[eval_models[0]].get("reason", ""),
        "evidence": per_model[eval_models[0]].get("evidence", []),
        "has_reference": reference is not None,
        "per_model": per_model,
        "_eval_cost": total_eval_cost,
        "_eval_cost_by_model": cost_by_model,
    }

    return result


def evaluate_result_quality(
    task_description: str,
    category: str,
    workspace_path: str,
    tool_logs: list,
    conversation: list,
    model=None,
    reference: dict | None = None,
    required_capabilities: list[str] | None = None,
    abort_event: threading.Event | None = None,
) -> dict:
    """Synchronous wrapper for result quality evaluation."""
    return run_async(
        async_evaluate_result_quality(
            task_description,
            category,
            workspace_path,
            tool_logs,
            conversation,
            model,
            reference,
            required_capabilities,
            abort_event,
        )
    )
