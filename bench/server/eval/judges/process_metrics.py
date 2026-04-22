"""Process-level metrics for QuantTutorBench — reformed (5 dimensions).

QP aggregate = weighted average of 5 dimensions:
    tool_usage      0.20  (programmatic — tool_usage.py, pre-computed)
    action_economy  0.15  (programmatic — step count ratio vs reference)
    code_lifecycle  0.15  (programmatic — code_process.py, 4 sub-metrics)
    task_planning   0.25  (LLM — EwanConvGEval, rubric_qp.json)
    problem_solving 0.25  (LLM — EwanConvGEval, rubric_qp.json, N/A when no errors)

    Optional dimensions with score=None are excluded and remaining weights
    renormalized. Required dimensions with score=None make the QP aggregate
    non-computable instead of receiving a default score.
"""

import threading
import time as _time
from typing import Optional

from server.eval.inputs.context_builder import (
    build_problem_solving_context,
    build_task_planning_context,
    has_explicit_errors,
)
from server.eval.inputs.rubric_builder import build_eval_params, load_rubric
from server.eval.judges.runtime.async_utils import (
    ABORT_SENTINEL,
    get_eval_concurrency,
    guarded_gather,
    run_async,
    set_eval_concurrency,  # noqa: F401 — re-export for callers
)
from server.eval.judges.runtime.call_policy import llm_call_with_retry
from server.eval.judges.runtime.conv_geval import EvalTestCase, EwanConvGEval
from server.eval.judges.runtime.model_resolver import (
    model_display_name,
    resolve_eval_model_list,
    resolve_ewan_model,
    short_model_name,
)
from server.tool_filters import NON_SUBSTANTIVE_TOOLS

# ──────────────────────────────────────────────────────────────
# QP Dimension Weights (5 dimensions)
# ──────────────────────────────────────────────────────────────

_QP_DIMENSION_WEIGHTS = {
    "tool_usage": 0.20,
    "action_economy": 0.15,
    "code_lifecycle": 0.15,
    "task_planning": 0.25,
    "problem_solving": 0.25,
}


# ──────────────────────────────────────────────────────────────
# Programmatic: Action Economy
# ──────────────────────────────────────────────────────────────


def _count_substantive_steps(proxy_logs: list) -> int:
    """Count substantive tool calls, excluding benign metadata reads."""
    return sum(
        1
        for log in proxy_logs
        if getattr(log, "name", None) not in NON_SUBSTANTIVE_TOOLS
    )


def _compute_action_economy(agent_steps: int, reference_steps: int) -> float:
    """Compute Action Economy score from step count ratio.

    Thresholds calibrated to 27% natural path variance:
        ratio <= 1.3  →  1.0   (within natural variance)
        ratio <= 1.6  →  0.75  (slightly above variance)
        ratio <= 2.2  →  0.5   (noticeably more steps)
        ratio <= 3.0  →  0.25  (significantly more steps)
        ratio >  3.0  →  0.0   (excessively verbose)
    """
    if reference_steps <= 0:
        raise ValueError("reference_steps must be positive")
    ratio = agent_steps / reference_steps
    if ratio <= 1.3:
        return 1.0
    elif ratio <= 1.6:
        return 0.75
    elif ratio <= 2.2:
        return 0.5
    elif ratio <= 3.0:
        return 0.25
    else:
        return 0.0


def evaluate_action_economy(
    proxy_logs: list, reference_trace: Optional[dict] = None
) -> dict:
    """Evaluate action economy (programmatic).

    Returns dict with score, reason, agent/reference step counts.
    Reference is currently optional; when unavailable this dimension is skipped
    and excluded from QP aggregation.
    """
    agent_steps = _count_substantive_steps(proxy_logs)
    if reference_trace is None:
        return {
            "score": None,
            "status": "skipped",
            "skip_reason": "reference_unavailable",
            "required_for_track_score": False,
            "reason": "No reference trace available",
            "agent_steps": agent_steps,
            "reference_steps": None,
        }
    ref_steps = reference_trace.get("step_count", 0)
    if ref_steps <= 0:
        return {
            "score": None,
            "status": "skipped",
            "skip_reason": "reference_step_count_missing",
            "required_for_track_score": False,
            "reason": "Reference trace has no positive step_count",
            "agent_steps": agent_steps,
            "reference_steps": ref_steps,
        }
    score = _compute_action_economy(agent_steps, ref_steps)
    ratio = agent_steps / ref_steps if ref_steps > 0 else 0
    return {
        "score": score,
        "status": "success",
        "required_for_track_score": True,
        "reason": f"Step ratio: {ratio:.2f} ({agent_steps}/{ref_steps})",
        "agent_steps": agent_steps,
        "reference_steps": ref_steps,
    }


# ──────────────────────────────────────────────────────────────
# LLM dimensions: task_planning + problem_solving via EwanConvGEval
# ──────────────────────────────────────────────────────────────


def _build_qp_llm_tasks(
    eval_models: list,
    task_description: str,
    enriched_conversation: list[dict],
    rc_list: list[str],
    proxy_logs: list,
) -> list[tuple[str, str, object]]:
    """Build async coroutines for QP LLM dimensions across all models.

    Returns: list of (model_name, metric_name, coroutine).
    """
    rubric = load_rubric("qp")
    tp_params = build_eval_params(rubric, "task_planning")
    ps_params = build_eval_params(rubric, "problem_solving")

    # Build contexts
    tp_context = build_task_planning_context(
        enriched_conversation=enriched_conversation,
        rc_list=rc_list,
        task_description=task_description,
    )
    tp_test_case = EvalTestCase(context=tp_context)

    _has_errors = has_explicit_errors(proxy_logs)
    ps_test_case = None
    if _has_errors:
        ps_context = build_problem_solving_context(
            enriched_conversation=enriched_conversation,
            task_description=task_description,
            tool_logs=proxy_logs,
        )
        ps_test_case = EvalTestCase(context=ps_context)

    flat_tasks: list[tuple[str, str, object]] = []
    for m in eval_models:
        mname = model_display_name(m)

        # task_planning — always evaluated
        async def _eval_tp(model=m):
            return await llm_call_with_retry(
                lambda: EwanConvGEval(
                    name="task_planning",
                    model=resolve_ewan_model(model),
                    **tp_params,
                ),
                tp_test_case,
                dimension_name="task_planning",
            )

        flat_tasks.append((mname, "task_planning", _eval_tp()))

        # problem_solving — only when explicit errors exist
        if ps_test_case is not None:

            async def _eval_ps(model=m):
                return await llm_call_with_retry(
                    lambda: EwanConvGEval(
                        name="problem_solving",
                        model=resolve_ewan_model(model),
                        **ps_params,
                    ),
                    ps_test_case,
                    dimension_name="problem_solving",
                )

            flat_tasks.append((mname, "problem_solving", _eval_ps()))

    return flat_tasks


def _dimension_status(data: dict, *, required: bool) -> dict:
    out = dict(data)
    out.setdefault("required_for_track_score", required)
    if out.get("score") is None:
        out.setdefault("status", "missing" if required else "skipped")
    else:
        out.setdefault("status", "success")
    return out


def evaluate_programmatic_process_metrics(
    *,
    proxy_logs: list,
    category: str = "",
    reference_trace: Optional[dict] = None,
    is_adversarial: bool = False,
    tool_usage_result: Optional[dict] = None,
    task_requires_code: bool = False,
) -> dict:
    """Run QP dimensions that do not require an LLM judge."""

    results: dict = {}

    ae_result = evaluate_action_economy(proxy_logs, reference_trace)
    results["action_economy"] = ae_result
    print(f"      action_economy: {ae_result['score']}")

    if category not in ("conceptual_qa",) and not (
        is_adversarial and not task_requires_code
    ):
        try:
            from server.eval.programmatic.code_process import (
                evaluate_code_lifecycle,
            )

            cl_result = evaluate_code_lifecycle(proxy_logs)
            cl_result = _dimension_status(cl_result, required=bool(task_requires_code))
            if cl_result.get("score") is None and not task_requires_code:
                cl_result["status"] = "skipped"
                cl_result["required_for_track_score"] = False
                cl_result.setdefault("skip_reason", "not_applicable")
            results["code_lifecycle"] = cl_result
            print(f"      code_lifecycle: {cl_result.get('score', '?')}")
        except Exception as e:
            results["code_lifecycle_error"] = str(e)
            results["code_lifecycle"] = {
                "score": None,
                "status": "failed",
                "required_for_track_score": True,
                "reason": str(e),
                "error": str(e),
            }
    else:
        results["code_lifecycle"] = {
            "score": None,
            "status": "skipped",
            "skip_reason": "not_applicable",
            "required_for_track_score": False,
            "reason": "Code lifecycle is not applicable for this task.",
        }

    if tool_usage_result is not None:
        results["tool_usage"] = _dimension_status(tool_usage_result, required=True)
        print(f"      tool_usage: {tool_usage_result.get('score', '?')}")
    else:
        results["tool_usage"] = {
            "score": None,
            "status": "missing",
            "required_for_track_score": True,
            "reason": "Tool usage result was not computed",
        }

    return results


def finalize_process_metrics(results: dict) -> dict:
    """Attach aggregate, blocking, weights, and total cost to QP results."""

    available_dims: dict[str, float] = {}
    blocking_missing: list[dict] = []
    for dim in _QP_DIMENSION_WEIGHTS:
        v = results.get(dim)
        if not isinstance(v, dict):
            blocking_missing.append(
                {
                    "track": "qp",
                    "dimension": dim,
                    "reason": "Required QP dimension is missing",
                }
            )
            continue
        if v.get("score") is not None and v.get("status") != "skipped":
            available_dims[dim] = v["score"]
            continue
        if v.get("required_for_track_score") is False or v.get("status") == "skipped":
            continue
        blocking_missing.append(
            {
                "track": "qp",
                "dimension": dim,
                "reason": v.get("reason")
                or v.get("error")
                or "Required QP dimension has no score",
            }
        )

    if blocking_missing:
        aggregate = None
        effective_weights: dict[str, float] = {}
    elif available_dims:
        total_weight = sum(_QP_DIMENSION_WEIGHTS[d] for d in available_dims)
        aggregate = sum(
            _QP_DIMENSION_WEIGHTS[d] * available_dims[d] / total_weight
            for d in available_dims
        )
        effective_weights = {
            dim: round(_QP_DIMENSION_WEIGHTS[dim] / total_weight, 4)
            for dim in available_dims
        }
    else:
        aggregate = None
        effective_weights = {}

    results["aggregate_process_score"] = (
        round(aggregate, 4) if aggregate is not None else None
    )
    results["_blocking_missing"] = blocking_missing
    results["_weights_used"] = dict(_QP_DIMENSION_WEIGHTS)
    results["_weights_effective"] = effective_weights
    print(f"      aggregate_process_score: {results['aggregate_process_score']}")

    total_eval_cost = 0.0
    for v in results.values():
        if isinstance(v, dict):
            total_eval_cost += v.get("_eval_cost", 0.0)
    results["_eval_cost"] = total_eval_cost
    return results


# ──────────────────────────────────────────────────────────────
# Aggregate evaluation entry point
# ──────────────────────────────────────────────────────────────


def evaluate_all_process_metrics(
    task_description: str,
    actual_output: str,
    proxy_logs: list,
    category: str = "",
    conversation: list[dict] | None = None,
    model=None,
    reference_trace: Optional[dict] = None,
    is_adversarial: bool = False,
    tool_usage_result: Optional[dict] = None,
    task_requires_code: bool = False,
    abort_event: Optional[threading.Event] = None,
    enriched_conversation: Optional[list[dict]] = None,
    required_capabilities: Optional[list[str]] = None,
) -> dict:
    """Run all process-level metrics and return consolidated results.

    Args:
        task_description: Text description of the task.
        actual_output: Agent's combined text output.
        proxy_logs: Tool call logs from MCPProxy.
        category: Task category (e.g. "implementation", "data_analysis").
        conversation: Raw conversation [{role, content}, ...].
        model: LLM judge model — single string, list, or None.
        reference_trace: Reference execution data for action_economy.
        is_adversarial: Whether this is an adversarial task.
        tool_usage_result: Pre-computed tool usage score.
        task_requires_code: Whether the task expects code output.
        abort_event: Shared threading.Event for cross-thread abort.
        enriched_conversation: Conversation with tool activity summaries appended.
        required_capabilities: RC checklist from task ground_truth.

    Returns:
        Dict with per-metric scores, aggregate process score, and _per_model breakdown.
    """
    # ── Resolve model list ──
    eval_models, multi_model = resolve_eval_model_list(model)
    model_names = [model_display_name(m) for m in eval_models]

    results = evaluate_programmatic_process_metrics(
        proxy_logs=proxy_logs,
        category=category,
        reference_trace=reference_trace,
        is_adversarial=is_adversarial,
        tool_usage_result=tool_usage_result,
        task_requires_code=task_requires_code,
    )

    # ── LLM: task_planning + problem_solving ──
    _enriched = enriched_conversation or conversation or []
    _rc_list = required_capabilities or []

    flat_tasks = _build_qp_llm_tasks(
        eval_models=eval_models,
        task_description=task_description,
        enriched_conversation=_enriched,
        rc_list=_rc_list,
        proxy_logs=proxy_logs,
    )

    _concurrency = get_eval_concurrency()
    model_llm_results: dict[str, dict[str, dict]] = {mname: {} for mname in model_names}

    if flat_tasks:
        total_calls = len(flat_tasks)
        print(
            f"    Running {total_calls} QP LLM calls "
            f"({len(eval_models)} model(s)) in parallel "
            f"(concurrency={_concurrency})..."
        )
        t0 = _time.time()

        coros = [coro for _, _, coro in flat_tasks]

        async def _run_all():
            return await guarded_gather(
                coros, abort_event=abort_event, concurrency=_concurrency
            )

        raw_results, _first_error = run_async(_run_all())
        elapsed = _time.time() - t0

        aborted = sum(1 for r in raw_results if r is ABORT_SENTINEL)
        if aborted:
            print(
                f"    QP LLM: {total_calls - aborted}/{total_calls} completed, "
                f"{aborted} aborted in {elapsed:.1f}s"
            )
        else:
            print(f"    Completed {total_calls} QP LLM calls in {elapsed:.1f}s")

        if _first_error:
            raise _first_error[0]

        # ── Collect per-model LLM results ──
        for i, (mname, metric_name, _) in enumerate(flat_tasks):
            raw = raw_results[i]
            if raw is ABORT_SENTINEL or isinstance(raw, Exception):
                raw = {
                    "score": None,
                    "status": "failed",
                    "required_for_track_score": True,
                    "reason": f"Unexpected abort/error in {metric_name}",
                    "error": str(raw),
                    "evidence": [],
                }
            model_llm_results[mname][metric_name] = raw

        # ── Cross-model average for LLM dimensions ──
        for metric_name in ("task_planning", "problem_solving"):
            per_metric = {
                mname: model_llm_results[mname].get(metric_name, {})
                for mname in model_names
                if model_llm_results[mname].get(metric_name)
            }
            if not per_metric:
                continue
            total_cost = sum(
                item.get("_eval_cost", 0.0) for item in per_metric.values()
            )
            failed = {
                mname: item
                for mname, item in per_metric.items()
                if item.get("score") is None or item.get("status") == "failed"
            }
            if failed:
                results[metric_name] = {
                    "score": None,
                    "status": "failed",
                    "required_for_track_score": True,
                    "reason": "; ".join(
                        f"{short_model_name(mname)}: {item.get('error') or item.get('reason')}"
                        for mname, item in failed.items()
                    ),
                    "evidence": [],
                    "per_model": per_metric,
                    "_eval_cost": total_cost,
                }
                continue
            scores = []
            for mname in model_names:
                r = model_llm_results[mname].get(metric_name)
                if r and r.get("score") is not None:
                    scores.append(r["score"])
            if scores:
                avg = round(sum(scores) / len(scores), 4)
                base = model_llm_results[model_names[0]].get(metric_name, {})
                results[metric_name] = {
                    "score": avg,
                    "status": "success",
                    "required_for_track_score": True,
                    "reason": base.get("reason", ""),
                    "evidence": base.get("evidence", []),
                    "per_model": {
                        mname: model_llm_results[mname].get(metric_name, {})
                        for mname in model_names
                        if model_llm_results[mname].get(metric_name)
                    },
                    "_eval_cost": total_cost,
                }
                if multi_model:
                    per_model_str = ", ".join(
                        f"{short_model_name(mname)}="
                        f"{model_llm_results[mname].get(metric_name, {}).get('score', '?')}"
                        for mname in model_names
                    )
                    print(f"      {metric_name}: {avg}  ({per_model_str})")
                else:
                    print(f"      {metric_name}: {avg}")

    # problem_solving N/A when no explicit errors (uses cached _has_errors from _build_qp_llm_tasks)
    if "problem_solving" not in results:
        results["problem_solving"] = {
            "score": None,
            "status": "skipped",
            "reason": "No explicit errors in execution trace (N/A)",
            "skipped": True,
            "skip_reason": "no_explicit_errors",
            "required_for_track_score": False,
        }
        print("      problem_solving: N/A (no explicit errors)")

    finalize_process_metrics(results)

    # Per-model cost breakdown for QP LLM dimensions
    if flat_tasks:
        cost_by_model: dict[str, float] = {}
        for mname in model_names:
            m_cost = sum(
                model_llm_results[mname].get(mn, {}).get("_eval_cost", 0.0)
                for mn in ("task_planning", "problem_solving")
            )
            if m_cost > 0:
                cost_by_model[mname] = round(m_cost, 6)
        results["_eval_cost_by_model"] = cost_by_model

    return results
