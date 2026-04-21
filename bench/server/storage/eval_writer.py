"""Evaluation runner + persistence for QuantTutorBench Server.

Each evaluation run creates a timestamped subdirectory under
``evaluations/`` and updates a ``latest`` symlink::

    results/server/{task_id}/{session_id}/
        run_state.json
        agent_files/
        evaluations/
            eval_20260410_110000/
                scores.md
                trace.md
                cost.md
                eval_meta.json
            latest -> eval_20260410_110000/

"""

import json
import logging
import time
from datetime import datetime
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
    conversation: list[dict],
    tool_logs: list,
    distractor_names: list[str],
    bench_root: str,
    eval_model: str,
    cancel_event=None,
    eval_mode: str = "full",
    tutor_dims: list[str] | None = None,
) -> dict:
    """Run full evaluation and save reports into a new evaluations/ subdirectory.

    Args:
        task: QuantTutorTask instance.
        persona: StudentPersona instance.
        result_dir: Session directory containing run_state.json and agent_files/.
        conversation: Session conversation history.
        tool_logs: Session tool call logs.
        distractor_names: Distractor tool names.
        bench_root: Path to bench/ root.
        eval_model: Model for LLM-based evaluators.
        cancel_event: Optional cancellation event.
        eval_mode: "full" | "qr_only" | "qp_only" | "tutor_only".
        tutor_dims: Optional list of tutor dimensions to evaluate.

    Returns:
        Dict with evaluation results (quant_result, quant_process, tutor_scores, ...).
    """
    result_dir = Path(result_dir)

    # Update run_state.json status
    from server.storage.result_writer import update_evaluation_status

    update_evaluation_status(result_dir, "running")

    eval_start = time.time()

    # --- Run evaluation pipeline ---
    from server.eval.pipeline import evaluate_task

    workspace_path = str(result_dir / "agent_files")

    _dims_info = f", tutor_dims={tutor_dims}" if tutor_dims else ""
    logger.info("Running evaluation (mode=%s%s)...", eval_mode, _dims_info)
    eval_results = evaluate_task(
        task=task,
        persona=persona,
        workspace_path=workspace_path,
        conversation=conversation,
        tool_logs=tool_logs,
        distractor_names=distractor_names,
        bench_root=bench_root,
        eval_model=eval_model,
        cancel_event=cancel_event,
        eval_mode=eval_mode,
        tutor_dims=tutor_dims,
    )

    # --- Compute task score ---
    from server.eval.scoring import compute_task_score

    scores = compute_task_score(
        quant_result_score=eval_results.get("quant_result", 0.0),
        quant_process_score=eval_results.get("quant_process", 0.0),
        tutor_dimension_scores=eval_results.get("tutor_scores", {}),
        category=task.category.value,
        requires_code=task.requires_code,
        eval_mode=eval_mode,
    )

    eval_duration = time.time() - eval_start

    # --- Create timestamped evaluations/ subdirectory ---
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    eval_dir = result_dir / "evaluations" / f"eval_{ts}"
    eval_dir.mkdir(parents=True, exist_ok=True)

    # --- Save reports ---
    _save_reports(
        eval_dir=eval_dir,
        task=task,
        persona=persona,
        conversation=conversation,
        tool_logs=tool_logs,
        eval_results=eval_results,
        scores=scores,
        eval_model=eval_model,
        eval_mode=eval_mode,
        eval_duration=eval_duration,
    )

    # --- Write eval_meta.json ---
    # Collect per-component errors so callers can see *why* a dimension is
    # empty/zero (see issue #42). `pipeline.evaluate_task` stores each
    # component's exception text under its own key when that component
    # silently fell back to an empty/default score.
    errors = _collect_eval_errors(eval_results)
    meta = {
        "timestamp": ts,
        "eval_model": eval_model,
        "eval_mode": eval_mode,
        "eval_duration_seconds": round(eval_duration, 2),
        "quant_result": eval_results.get("quant_result", 0.0),
        "quant_process": eval_results.get("quant_process", 0.0),
        "tutor_scores": eval_results.get("tutor_scores", {}),
        "overall_score": scores.get("overall_score", 0.0),
    }
    if errors:
        meta["errors"] = errors
    meta_path = eval_dir / "eval_meta.json"
    meta_path.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    logger.info("Saved eval_meta.json to %s", meta_path)

    # --- Update latest symlink ---
    latest_link = result_dir / "evaluations" / "latest"
    try:
        if latest_link.is_symlink() or latest_link.exists():
            latest_link.unlink()
        latest_link.symlink_to(eval_dir.name)
        logger.info("Updated latest -> %s", eval_dir.name)
    except OSError as exc:
        logger.warning("Failed to update latest symlink: %s", exc)

    # --- Update run_state.json status ---
    update_evaluation_status(result_dir, "completed")

    return eval_results


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
