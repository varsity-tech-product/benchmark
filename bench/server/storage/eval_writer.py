"""Evaluation JSON persistence for QuantTutorBench Server."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval.contracts.output import EvalOutput, TrackResult
from eval.contracts.request import EvalRequest, normalize_eval_mode
from eval.judge_reliability import build_judge_reliability_metadata
from server.storage.score_store import (
    allocate_score_run,
    summarize_score,
    update_score_run,
    write_score_files,
)

logger = logging.getLogger(__name__)


_EVAL_ERROR_KEYS: dict[str, str] = {
    "quant_result_error": "quant_result",
    "code_eval_error": "code_eval",
    "tool_usage_error": "tool_usage",
    "process_metrics_error": "process_metrics",
    "qr_track_error": "qr",
    "qp_track_error": "qp",
}


def _collect_eval_errors(eval_results: dict) -> dict:
    """Return a component → error-text dict for known eval component failures."""

    out: dict = {}
    for internal, public in _EVAL_ERROR_KEYS.items():
        msg = eval_results.get(internal)
        if msg:
            out[public] = str(msg)
    return out


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mode_includes(eval_mode: str, track: str) -> bool:
    return eval_mode == "full" or eval_mode == track


def _cost_by_model_from(*sources: dict | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for src in sources:
        if not isinstance(src, dict):
            continue
        by_model = src.get("_eval_cost_by_model") or src.get("eval_cost_by_model") or {}
        if not isinstance(by_model, dict):
            continue
        for model, cost in by_model.items():
            try:
                out[str(model)] = round(out.get(str(model), 0.0) + float(cost), 6)
            except (TypeError, ValueError):
                continue
    return out


def _eval_cost_from(*sources: dict | None) -> float:
    total = 0.0
    for src in sources:
        if not isinstance(src, dict):
            continue
        try:
            total += float(src.get("_eval_cost", 0.0) or src.get("eval_cost", 0.0))
        except (TypeError, ValueError):
            continue
    return round(total, 6)


def _track_status(
    score: float | None, blockers: list[dict], *, skipped: bool = False
) -> str:
    if skipped:
        return "skipped"
    if blockers:
        return "not_computable"
    return "success" if score is not None else "not_computable"


def _dimension_optional(data: Any) -> bool:
    return isinstance(data, dict) and (
        data.get("required_for_track_score") is False
        or data.get("skipped") is True
        or data.get("status") == "skipped"
    )


def _preflight_blockers(eval_results: dict, track: str) -> list[dict[str, Any]]:
    preflight = eval_results.get("preflight")
    if not isinstance(preflight, dict):
        return []
    blockers = preflight.get("track_blockers")
    if not isinstance(blockers, dict):
        return []
    items = blockers.get(track) or []
    return [item for item in items if isinstance(item, dict)]


def _make_qr_track(eval_results: dict, duration: float) -> TrackResult:
    blockers: list[dict[str, Any]] = list(_preflight_blockers(eval_results, "qr"))
    for key in ("quant_result_error", "code_eval_error"):
        if eval_results.get(key):
            blockers.append(
                {
                    "track": "qr",
                    "dimension": key.removesuffix("_error"),
                    "reason": str(eval_results[key]),
                }
            )

    rj = (
        eval_results.get("result_judge")
        if isinstance(eval_results.get("result_judge"), dict)
        else {}
    )
    score = eval_results.get("quant_result")
    if rj and rj.get("score") is None and rj.get("error"):
        blockers.append(
            {
                "track": "qr",
                "dimension": "result_judge",
                "reason": str(rj.get("error")),
            }
        )
    if blockers:
        score = None

    if eval_results.get("qr_track_error"):
        blockers.append(
            {
                "track": "qr",
                "dimension": "qr",
                "reason": str(eval_results["qr_track_error"]),
            }
        )
        score = None

    code_eval = eval_results.get("code_eval") or {}
    eval_script_detail = eval_results.get("eval_script_detail") or {}
    detail = {
        "programmatic": {
            "score": eval_script_detail.get(
                "score", eval_results.get("quant_result_programmatic")
            ),
            "status": eval_script_detail.get(
                "status",
                "failed" if eval_results.get("quant_result_error") else "success",
            ),
            "required_for_track_score": eval_script_detail.get(
                "required_for_track_score",
                eval_script_detail.get("status") != "skipped",
            ),
            "detail": eval_script_detail,
        },
        "code_eval": code_eval,
        "result_judge": rj,
        "blend_weights": eval_results.get("qr_blend_weights"),
    }
    return TrackResult(
        track="qr",
        score=score,
        status=_track_status(score, blockers),
        detail=detail,
        blocking_missing=blockers,
        eval_cost=_eval_cost_from(rj),
        eval_cost_by_model=_cost_by_model_from(rj),
        duration_seconds=duration,
    )


def _make_qp_track(eval_results: dict, duration: float) -> TrackResult:
    process = eval_results.get("process_metrics") or {}
    blockers: list[dict[str, Any]] = list(_preflight_blockers(eval_results, "qp"))
    for key in ("tool_usage_error", "process_metrics_error", "qp_track_error"):
        if eval_results.get(key):
            blockers.append(
                {
                    "track": "qp",
                    "dimension": key.removesuffix("_error"),
                    "reason": str(eval_results[key]),
                }
            )
    for dim in (
        "tool_usage",
        "action_economy",
        "code_lifecycle",
        "task_planning",
        "problem_solving",
    ):
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
            if _dimension_optional(data):
                continue
            blockers.append(
                {
                    "track": "qp",
                    "dimension": dim,
                    "reason": data.get("reason")
                    or "Required QP dimension has no score",
                }
            )
    score = eval_results.get("quant_process")
    if blockers:
        score = None
    return TrackResult(
        track="qp",
        score=score,
        status=_track_status(score, blockers),
        detail=process,
        blocking_missing=blockers,
        eval_cost=_eval_cost_from(process),
        eval_cost_by_model=_cost_by_model_from(process),
        duration_seconds=duration,
    )


def _compute_overall(
    *,
    eval_mode: str,
    qr: TrackResult | None,
    qp: TrackResult | None,
) -> tuple[float | None, list[dict[str, Any]]]:
    tracks = [t for t in (qr, qp) if t is not None]
    blockers = [b for t in tracks for b in t.blocking_missing]
    from eval.core.scoring import compute_overall

    return compute_overall(qr=qr, qp=qp, eval_mode=eval_mode), blockers


def _build_eval_output(
    *,
    score_id: str,
    eval_results: dict,
    task,
    eval_mode: str,
    eval_model: str | None,
    created_at: str,
    completed_at: str,
    duration: float,
) -> EvalOutput:
    qr = (
        _make_qr_track(eval_results, duration)
        if _mode_includes(eval_mode, "qr")
        else None
    )
    qp = (
        _make_qp_track(eval_results, duration)
        if _mode_includes(eval_mode, "qp")
        else None
    )
    overall, blockers = _compute_overall(eval_mode=eval_mode, qr=qr, qp=qp)
    status = "completed_scored" if overall is not None else "completed_not_computable"
    return EvalOutput(
        score_id=score_id,
        score_status=status,
        qr=qr,
        qp=qp,
        overall_score=overall,
        eval_mode=eval_mode,
        eval_model=eval_model,
        created_at=created_at,
        completed_at=completed_at,
        duration_seconds=duration,
        judge_reliability=build_judge_reliability_metadata(eval_model),
        blocking_missing=blockers,
    )


def _build_cost(
    output: EvalOutput, stage_costs: dict[str, dict[str, float]] | None = None
) -> dict[str, Any]:
    tracks = [t for t in (output.qr, output.qp) if t is not None]
    by_track = {t.track: round(t.eval_cost, 6) for t in tracks}
    by_model: dict[str, float] = {}
    by_stage_model: dict[str, dict[str, float]] = dict(stage_costs or {})
    for t in tracks:
        if t.eval_cost_by_model and not stage_costs:
            by_stage_model[t.track] = t.eval_cost_by_model
        for model, cost in t.eval_cost_by_model.items():
            by_model[model] = round(by_model.get(model, 0.0) + cost, 6)
    return {
        "version": "2.0",
        "score_id": output.score_id,
        "eval_cost_usd": round(sum(by_track.values()), 6),
        "eval_cost_by_track": by_track,
        "eval_cost_by_model": by_model,
        "eval_cost_by_stage_model": by_stage_model,
    }


def save_eval_results(
    *,
    task,
    result_dir: Path,
    eval_results: dict,
    eval_mode: str = "full",
    eval_model: str | None = None,
    score_id: str | None = None,
    created_at: str | None = None,
    duration: float = 0.0,
    stage_costs: dict[str, dict[str, float]] | None = None,
) -> dict:
    """Persist existing evaluation results as a new ``score_n`` JSON run."""

    result_dir = Path(result_dir)
    eval_mode = normalize_eval_mode(eval_mode)
    if score_id is None:
        run, _created = allocate_score_run(
            result_dir,
            eval_mode=eval_mode,
            eval_model=eval_model,
        )
        score_id = run.score_id
        created_at = run.created_at
    elif created_at is None:
        from server.storage.score_store import load_index

        index = load_index(result_dir)
        entry = next(
            (e for e in index.get("scores", []) if e.get("score_id") == score_id),
            {},
        )
        created_at = entry.get("created_at") or _now()

    completed_at = _now()
    output = _build_eval_output(
        score_id=score_id,
        eval_results=eval_results,
        task=task,
        eval_mode=eval_mode,
        eval_model=eval_model,
        created_at=created_at or completed_at,
        completed_at=completed_at,
        duration=duration,
    )
    score_data = output.to_dict()
    cost_data = _build_cost(output, stage_costs=stage_costs)
    write_score_files(result_dir, score_id, score_data, cost_data)
    update_score_run(
        result_dir,
        score_id,
        status=output.score_status,
        overall_score=output.overall_score,
        completed_at=completed_at,
    )
    logger.info("Saved %s score.json + cost.json", score_id)
    return summarize_score(score_data, cost_data)


def save_terminal_eval_result(
    *,
    result_dir: Path,
    score_id: str,
    eval_mode: str,
    eval_model: str | None,
    created_at: str | None,
    status: str,
    error: str,
    duration: float = 0.0,
    preflight: dict[str, Any] | None = None,
    interrupted: bool = False,
) -> dict:
    completed_at = _now()
    score_data = {
        "version": "2.0",
        "score_id": score_id,
        "score_status": status,
        "created_at": created_at or completed_at,
        "completed_at": completed_at,
        "eval_model": eval_model,
        "eval_mode": eval_mode,
        "duration_seconds": round(duration, 2),
        "judge_reliability": build_judge_reliability_metadata(eval_model),
        "interrupted": interrupted,
        "blocking_missing": [],
        "overall_score": None,
        "qr": None,
        "qp": None,
        "error": error,
        "preflight": preflight or {},
    }
    cost_data = {
        "version": "2.0",
        "score_id": score_id,
        "eval_cost_usd": 0.0,
        "eval_cost_by_track": {},
        "eval_cost_by_model": {},
        "eval_cost_by_stage_model": {},
    }
    write_score_files(result_dir, score_id, score_data, cost_data)
    update_score_run(
        result_dir,
        score_id,
        status=status,
        overall_score=None,
        completed_at=completed_at,
        error=error,
    )
    return summarize_score(score_data, cost_data)


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
    score_id: str | None = None,
) -> dict:
    """Run evaluation and save score.json + cost.json under a score_n directory."""

    result_dir = Path(result_dir)
    eval_mode = normalize_eval_mode(eval_mode)
    if score_id is None:
        run, _created = allocate_score_run(
            result_dir,
            eval_mode=eval_mode,
            eval_model=eval_model,
        )
        score_id = run.score_id
        created_at = run.created_at
    else:
        # Keep index-created timestamp when possible.
        from server.storage.score_store import load_index

        index = load_index(result_dir)
        entry = next(
            (e for e in index.get("scores", []) if e.get("score_id") == score_id),
            {},
        )
        created_at = entry.get("created_at") or _now()

    from eval.core.coordinator import EvalCoordinator

    run_state = {
        "conversation": conversation,
        "tool_logs": [
            vars(log) if hasattr(log, "__dict__") else log for log in (tool_logs or [])
        ],
        "distractor_names": distractor_names,
        "task_id": getattr(task, "task_id", ""),
        "persona_id": getattr(persona, "persona_id", ""),
    }
    request = EvalRequest(
        session_id=(run_state.get("session_id") or ""),
        eval_mode=eval_mode,
        eval_model=eval_model,
    )
    logger.info("Running evaluation score_id=%s mode=%s...", score_id, eval_mode)
    output = EvalCoordinator(bench_root=bench_root).run(
        request=request,
        result_dir=result_dir,
        score_id=score_id,
        task=task,
        persona=persona,
        run_state=run_state,
        created_at=created_at,
        conversation=conversation,
        tool_logs=tool_logs,
        distractor_names=distractor_names,
        cancel_event=cancel_event,
        persist=True,
    )
    return output.to_summary()
