"""QP track: tool usage, process metrics, and aggregate process score."""

from __future__ import annotations

import threading
from typing import Any

from eval.contracts.output import TrackResult
from eval.tracks.common import (
    check_cancel,
    cost_by_model_from,
    dimension_optional,
    eval_cost_from,
    preflight_blockers,
    track_status,
)

_QP_DIMS = (
    "tool_usage",
    "action_economy",
    "code_lifecycle",
    "task_planning",
    "problem_solving",
)


def _category_value(task: Any) -> str:
    category = getattr(task, "category", "")
    return getattr(category, "value", category) or ""


def evaluate(
    *,
    task: Any,
    conversation: list[dict[str, Any]],
    enriched_conversation: list[dict[str, Any]],
    tool_logs: list[Any],
    distractor_names: list[str],
    eval_model: str | None,
    preflight: Any = None,
    cancel_event: threading.Event | None = None,
) -> TrackResult:
    blockers: list[dict[str, Any]] = list(preflight_blockers(preflight, "qp"))
    model_unavailable = any(
        blocker.get("code") == "eval_model_unavailable" for blocker in blockers
    )
    tool_usage_result = None
    tool_usage_error = None

    try:
        from eval.programmatic.tool_usage import evaluate_tool_usage

        tool_usage_result = evaluate_tool_usage(
            proxy_logs=tool_logs,
            expected_tools=(
                task.ground_truth.expected_mcp_tools if task.ground_truth else []
            ),
            convenient_tools=(
                task.ground_truth.convenient_tools if task.ground_truth else []
            ),
            distractor_names=distractor_names,
            is_adversarial=(_category_value(task) == "adversarial"),
        )
    except Exception as exc:  # noqa: BLE001 - convert to dimension failure
        tool_usage_error = str(exc)
        blockers.append({"track": "qp", "dimension": "tool_usage", "reason": str(exc)})

    check_cancel(cancel_event)

    if model_unavailable:
        from eval.inputs.context_builder import has_explicit_errors
        from eval.judges.process_metrics import (
            evaluate_programmatic_process_metrics,
            finalize_process_metrics,
        )

        process = evaluate_programmatic_process_metrics(
            proxy_logs=tool_logs,
            category=_category_value(task),
            reference_trace=None,
            is_adversarial=(_category_value(task) == "adversarial"),
            tool_usage_result=tool_usage_result,
            task_requires_code=task.requires_code,
        )
        process["task_planning"] = {
            "score": None,
            "status": "failed",
            "required_for_track_score": True,
            "reason": "Evaluation model is unavailable",
        }
        if has_explicit_errors(tool_logs):
            process["problem_solving"] = {
                "score": None,
                "status": "failed",
                "required_for_track_score": True,
                "reason": "Evaluation model is unavailable",
            }
        else:
            process["problem_solving"] = {
                "score": None,
                "status": "skipped",
                "skip_reason": "no_explicit_errors",
                "required_for_track_score": False,
                "reason": "No explicit errors in execution trace (N/A)",
                "skipped": True,
            }
        finalize_process_metrics(process)
    else:
        try:
            from eval.judges.process_metrics import evaluate_all_process_metrics

            assistant_outputs = [
                turn.get("content", "")
                for turn in conversation
                if isinstance(turn, dict) and turn.get("role") == "assistant"
            ]
            process = evaluate_all_process_metrics(
                task_description=task.description,
                actual_output="\n---\n".join(assistant_outputs),
                proxy_logs=tool_logs,
                category=_category_value(task),
                conversation=conversation,
                model=eval_model,
                reference_trace=None,
                is_adversarial=(_category_value(task) == "adversarial"),
                tool_usage_result=tool_usage_result,
                task_requires_code=task.requires_code,
                abort_event=cancel_event,
                enriched_conversation=enriched_conversation,
                required_capabilities=(
                    task.ground_truth.required_capabilities
                    if task.ground_truth
                    else None
                ),
            )
        except (
            Exception
        ) as exc:  # noqa: BLE001 - QP track can still persist error detail
            process = {}
            blockers.append(
                {"track": "qp", "dimension": "process_metrics", "reason": str(exc)}
            )

    if tool_usage_error:
        process["tool_usage"] = {
            "score": None,
            "status": "failed",
            "required_for_track_score": True,
            "reason": tool_usage_error,
            "error": tool_usage_error,
        }
        if model_unavailable:
            from eval.judges.process_metrics import finalize_process_metrics

            finalize_process_metrics(process)

    for dim in _QP_DIMS:
        data = process.get(dim)
        if data is None:
            blockers.append(
                {
                    "track": "qp",
                    "dimension": dim,
                    "reason": "Required QP dimension is missing",
                }
            )
            continue
        if isinstance(data, dict) and data.get("score") is None:
            if dimension_optional(data):
                continue
            blockers.append(
                {
                    "track": "qp",
                    "dimension": dim,
                    "reason": data.get("reason") or data.get("error") or "No score",
                }
            )

    score = process.get("aggregate_process_score") if not blockers else None
    return TrackResult(
        track="qp",
        score=score,
        status=track_status(score, blockers),
        detail=process,
        blocking_missing=blockers,
        eval_cost=eval_cost_from(process),
        eval_cost_by_model=cost_by_model_from(process),
    )
