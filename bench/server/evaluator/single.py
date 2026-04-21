"""Score a single bundle off-line.

Reuses ``server.eval.pipeline.evaluate_task`` unchanged — this module is
the *driver* that loads inputs from disk, fans them in, and persists the
outputs to the new sibling ``evaluations/server/...`` tree. Reports
(``scores.md``, ``trace.md``, ``cost.md``) and ``eval_meta.json`` are
written exactly as the legacy in-session writer produced them, so the UI
and any downstream consumer see the same payload regardless of which path
ran the scoring.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from server.evaluator.paths import EVAL_RUN_ID_RE, eval_run_dir, new_eval_run_id
from server.storage.bundle import load_bundle, write_manifest
from server.storage.bundle import RUN_STATE_FILENAME, MANIFEST_FILENAME

logger = logging.getLogger(__name__)


def _ensure_manifest(bundle_dir: Path) -> None:
    """Backfill ``manifest.json`` for legacy bundles that predate slice 1.

    Older runs on disk only have ``run_state.json`` + ``agent_files/``.
    Re-scoring them via the new evaluator would otherwise raise on
    ``load_bundle``; backfill is one-time, idempotent, and lossless.
    """
    if (bundle_dir / MANIFEST_FILENAME).exists():
        return
    run_state_path = bundle_dir / RUN_STATE_FILENAME
    if not run_state_path.is_file():
        return
    try:
        run_state = json.loads(run_state_path.read_text(encoding="utf-8"))
    except Exception:
        return
    write_manifest(bundle_dir, run_state)
    logger.info("Backfilled manifest.json for legacy bundle %s", bundle_dir)


def score_bundle(
    bundle_dir: Path | str,
    *,
    task,
    persona,
    bench_root: Path | str,
    eval_model: str,
    eval_mode: str = "full",
    tutor_dims: Optional[list[str]] = None,
    cancel_event=None,
    eval_run_id: Optional[str] = None,
) -> dict:
    """Score one bundle and persist the result tree.

    Args:
        bundle_dir: Path to the bundle directory. ``manifest.json`` is
            backfilled on-the-fly for legacy bundles.
        task: ``QuantTutorTask`` looked up by ``task_id`` upstream.
        persona: ``StudentPersona`` looked up by ``persona_id`` upstream.
        bench_root: Repo root used to locate eval scripts and to anchor the
            sibling output tree.
        eval_model: Judge LLM identifier passed through to the pipeline.
        eval_mode: ``full | qr_only | qp_only | tutor_only``.
        tutor_dims: Optional subset of tutor dimensions to score.
        cancel_event: Optional ``threading.Event`` for cooperative cancellation.
        eval_run_id: Override the auto-generated run id; must match
            ``EVAL_RUN_ID_RE`` so readers (UI, REST) find the result.

    Returns:
        The ``evaluate_task`` result dict, augmented with ``_eval_run_dir``
        (Path) and ``_eval_run_id`` (str) so callers can locate the output.
    """
    from server.eval.pipeline import evaluate_task
    from server.eval.scoring import compute_task_score
    from server.storage.eval_writer import _collect_eval_errors, _save_reports
    from server.storage.result_writer import update_evaluation_status

    bundle_path = Path(bundle_dir)
    if eval_run_id is not None and not EVAL_RUN_ID_RE.match(eval_run_id):
        raise ValueError(
            f"--eval-run-id {eval_run_id!r} must match {EVAL_RUN_ID_RE.pattern} "
            "so the UI and REST readers can discover the result."
        )

    _ensure_manifest(bundle_path)
    bundle = load_bundle(bundle_path)

    update_evaluation_status(bundle_path, "running")
    start = time.time()
    try:
        eval_results = evaluate_task(
            task=task,
            persona=persona,
            workspace_path=bundle.workspace_path,
            conversation=bundle.conversation,
            tool_logs=bundle.tool_logs,
            distractor_names=bundle.distractor_names,
            bench_root=str(bench_root),
            eval_model=eval_model,
            cancel_event=cancel_event,
            eval_mode=eval_mode,
            tutor_dims=tutor_dims,
        )

        scores = compute_task_score(
            quant_result_score=eval_results.get("quant_result", 0.0),
            quant_process_score=eval_results.get("quant_process", 0.0),
            tutor_dimension_scores=eval_results.get("tutor_scores", {}),
            category=task.category.value,
            requires_code=task.requires_code,
            eval_mode=eval_mode,
        )

        duration = time.time() - start
        run_id = eval_run_id or new_eval_run_id()
        out_dir = eval_run_dir(
            bench_root,
            task_id=bundle.task_id,
            persona_id=bundle.persona_id,
            session_id=bundle.session_id,
            eval_run_id=run_id,
        )
        out_dir.mkdir(parents=True, exist_ok=True)

        _save_reports(
            eval_dir=out_dir,
            task=task,
            persona=persona,
            conversation=bundle.conversation,
            tool_logs=bundle.tool_logs,
            eval_results=eval_results,
            scores=scores,
            eval_model=eval_model,
            eval_mode=eval_mode,
            eval_duration=duration,
        )

        errors = _collect_eval_errors(eval_results)
        meta = {
            "timestamp": datetime.now().strftime("%Y%m%d_%H%M%S"),
            "eval_run_id": run_id,
            "bundle_dir": str(bundle_path),
            "eval_model": eval_model,
            "eval_mode": eval_mode,
            "eval_duration_seconds": round(duration, 2),
            "quant_result": eval_results.get("quant_result", 0.0),
            "quant_process": eval_results.get("quant_process", 0.0),
            "tutor_scores": eval_results.get("tutor_scores", {}),
            "overall_score": scores.get("overall_score", 0.0),
        }
        if errors:
            meta["errors"] = errors
        (out_dir / "eval_meta.json").write_text(
            json.dumps(meta, indent=2, default=str), encoding="utf-8"
        )
        logger.info("Scored bundle %s → %s", bundle_path.name, out_dir)
    except BaseException:
        # Mark the bundle's eval as failed before re-raising so CLI / batch
        # callers don't leave run_state stuck on "running" forever.
        try:
            update_evaluation_status(bundle_path, "failed")
        except Exception:
            pass
        raise

    update_evaluation_status(bundle_path, "completed")

    eval_results["_eval_run_id"] = run_id
    eval_results["_eval_run_dir"] = out_dir
    eval_results["_overall_score"] = scores.get("overall_score", 0.0)
    return eval_results
