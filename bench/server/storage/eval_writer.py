"""Shared report writers for the evaluator.

Output layout: see ``server.evaluator.paths`` —
``evaluations/server/{task_id}/{persona_id}/{sid8}/{eval_run_id}/``.

After issue #46 slice 4 this module owns only the shared helpers
``score_bundle`` reuses (``_save_reports``, ``_collect_eval_errors``);
the prior in-session compat shim is gone since nothing calls it.
"""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


# Maps internal error-stash keys produced by `pipeline.evaluate_task` onto
# the short component name exposed in eval_meta.json + the /scores payload.
# See issue #42 — the tutor path uses `_eval_error` while quant_result /
# code_eval use `_error`, so an ad-hoc suffix strip would mangle them.
_EVAL_ERROR_KEYS: dict[str, str] = {
    "tutor_eval_error": "tutor",
    "quant_result_error": "quant_result",
    "code_eval_error": "code_eval",
    "tool_usage_error": "tool_usage",
}


def _collect_eval_errors(eval_results: dict) -> dict:
    """Return a component → error-text dict for every silently-failed eval.

    Keys are the short component name so the API surface reads cleanly
    and callers can tell an empty/zeroed score apart from a genuine zero.
    """
    out: dict = {}
    for internal, public in _EVAL_ERROR_KEYS.items():
        msg = eval_results.get(internal)
        if msg:
            out[public] = str(msg)
    return out


def _save_reports(
    eval_dir: Path,
    task,
    persona,
    conversation: list[dict],
    tool_logs: list,
    eval_results: dict,
    scores: dict,
    eval_model: str,
    eval_mode: str,
    eval_duration: float,
) -> None:
    """Save scores.md, trace.md, and cost.md into the eval directory."""
    from server.schemas import ConversationTurn, TaskResult

    result = TaskResult(
        task_id=task.task_id,
        persona_id=persona.persona_id,
        difficulty=task.difficulty.value,
        category=task.category.value,
        requires_code=task.requires_code,
    )
    for t in conversation:
        result.turns.append(
            ConversationTurn(role=t["role"], content=t["content"]),
        )

    # --- scores.md ---
    try:
        from server.eval.eval_helpers import populate_eval_results
        from server.eval.reports.score_report import generate_score_report

        populate_eval_results(
            result,
            eval_results,
            category=task.category.value,
            requires_code=task.requires_code,
            eval_mode=eval_mode,
        )

        scores_path = eval_dir / "scores.md"
        scores_path.write_text(
            generate_score_report(result),
            encoding="utf-8",
        )
        logger.info("Saved scores.md")
    except Exception as exc:
        logger.warning("Failed to save scores.md: %s", exc)

    # --- trace.md ---
    try:
        from server.eval.reports.trace_report import generate_trace_md

        # trace_report expects TaskResult-like object and proxy logs
        trace_md = generate_trace_md(result, tool_logs)
        trace_path = eval_dir / "trace.md"
        trace_path.write_text(trace_md, encoding="utf-8")
        logger.info("Saved trace.md")
    except Exception as exc:
        logger.warning("Failed to save trace.md: %s", exc)

    # --- cost.md ---
    try:
        from server.eval.reports.cost_report import generate_cost_report

        cost_md = generate_cost_report(
            result,
            task_id=task.task_id,
            persona_id=persona.persona_id,
        )
        cost_path = eval_dir / "cost.md"
        cost_path.write_text(cost_md, encoding="utf-8")
        logger.info("Saved cost.md")
    except Exception as exc:
        logger.warning("Failed to save cost.md: %s", exc)
