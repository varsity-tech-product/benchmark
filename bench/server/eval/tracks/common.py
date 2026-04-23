"""Small shared helpers for evaluation track implementations."""

from __future__ import annotations

import threading
from typing import Any


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise KeyboardInterrupt("Evaluation interrupted")


def cost_by_model_from(*sources: dict | None) -> dict[str, float]:
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


def eval_cost_from(*sources: dict | None) -> float:
    total = 0.0
    for src in sources:
        if not isinstance(src, dict):
            continue
        try:
            total += float(src.get("_eval_cost", 0.0) or src.get("eval_cost", 0.0))
        except (TypeError, ValueError):
            continue
    return round(total, 6)


def track_status(
    score: float | None, blockers: list[dict[str, Any]], *, skipped: bool = False
) -> str:
    if skipped:
        return "skipped"
    if blockers:
        return "not_computable"
    return "success" if score is not None else "not_computable"


def dimension_optional(data: Any) -> bool:
    return isinstance(data, dict) and (
        data.get("required_for_track_score") is False
        or data.get("skipped") is True
        or data.get("status") == "skipped"
    )


def preflight_blockers(preflight: Any, track: str) -> list[dict[str, Any]]:
    if not preflight:
        return []
    payload = preflight.to_dict() if hasattr(preflight, "to_dict") else preflight
    if not isinstance(payload, dict):
        return []
    blockers = payload.get("track_blockers")
    if not isinstance(blockers, dict):
        return []
    return [item for item in blockers.get(track, []) if isinstance(item, dict)]
