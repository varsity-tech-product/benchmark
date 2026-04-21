"""Evaluation runner + persistence for QuantTutorBench Server.

Output layout (post-#46 split): see ``server.evaluator.paths`` —
``evaluations/server/{task_id}/{persona_id}/{sid8}/{eval_run_id}/``.

This module is now a thin compat shim around
``server.evaluator.score_bundle`` plus the shared report writers
(``_save_reports``, ``_collect_eval_errors``) the evaluator imports back.
The legacy in-bundle ``evaluations/`` subdir is no longer written; readers
fall back to it via ``paths.find_latest_eval_dir`` for old runs only.
"""

import json
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


def run_evaluation(
    task,
    persona,
    result_dir: Path,
    bench_root: str,
    eval_model: str,
    cancel_event=None,
    eval_mode: str = "full",
    tutor_dims: list[str] | None = None,
) -> dict:
    """Score the bundle at ``result_dir`` and persist outputs.

    Compat shim around :func:`server.evaluator.score_bundle` for the
    in-session call site (``SessionState._run_evaluation``). Output goes
    to the new ``evaluations/server/...`` sibling tree; the legacy
    in-bundle ``evaluations/`` subdir is no longer written.

    Conversation, tool_logs, and distractor_names are read from the
    bundle on disk via ``load_bundle`` — the producer (``_save_results``)
    runs first, so the data is always there.
    """
    from server.evaluator import score_bundle

    return score_bundle(
        bundle_dir=Path(result_dir),
        task=task,
        persona=persona,
        bench_root=bench_root,
        eval_model=eval_model,
        eval_mode=eval_mode,
        tutor_dims=tutor_dims,
        cancel_event=cancel_event,
    )


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
