"""Tutor track: persona-aware 6D tutoring evaluation."""

from __future__ import annotations

import threading
from typing import Any

from server.eval.contracts.output import TrackResult
from server.eval.contracts.request import normalize_tutor_dims
from server.eval.tracks.common import (
    cost_by_model_from,
    eval_cost_from,
    preflight_blockers,
    track_status,
)


def _category_value(task: Any) -> str:
    category = getattr(task, "category", "")
    return getattr(category, "value", category) or ""


def _expected_dimensions(
    *,
    category: str,
    requires_code: bool,
    tutor_dims: list[str] | None,
) -> list[str]:
    from server.eval.judges.tutor_6d import DIMENSIONS, get_dimension_weight

    normalized = normalize_tutor_dims(tutor_dims)
    if normalized:
        return list(normalized)
    return [
        dim
        for dim in DIMENSIONS
        if get_dimension_weight(category, dim, requires_code=requires_code) > 0.0
    ]


def _tutor_score(
    tutor_scores: dict, *, category: str, requires_code: bool
) -> float | None:
    clean = {
        k: v
        for k, v in tutor_scores.items()
        if not str(k).startswith("_") and isinstance(v, (int, float))
    }
    if not clean:
        return None
    from server.eval.judges.tutor_6d import compute_tutor_score

    return compute_tutor_score(clean, category=category, requires_code=requires_code)


def _weights_used(expected_dims: list[str]) -> dict[str, int]:
    from server.eval.judges.tutor_6d import DIMENSIONS

    expected = set(expected_dims)
    return {dim[:2]: (1 if dim in expected else 0) for dim in DIMENSIONS}


def evaluate(
    *,
    task: Any,
    persona: Any,
    conversation: list[dict[str, Any]],
    enriched_conversation: list[dict[str, Any]],
    eval_model: str | None,
    tutor_dims: list[str] | None,
    preflight: Any = None,
    cancel_event: threading.Event | None = None,
) -> TrackResult:
    category = _category_value(task)
    requires_code = bool(task.requires_code)
    normalized_tutor_dims = normalize_tutor_dims(tutor_dims)
    expected_dims = _expected_dimensions(
        category=category,
        requires_code=requires_code,
        tutor_dims=normalized_tutor_dims,
    )
    blockers: list[dict[str, Any]] = list(preflight_blockers(preflight, "tutor"))
    model_unavailable = any(
        blocker.get("code") == "eval_model_unavailable" for blocker in blockers
    )

    if model_unavailable:
        tutor_scores = {
            "_dim_errors": {
                dim: ["Evaluation model is unavailable"] for dim in expected_dims
            }
        }
    else:
        try:
            from server.config.prompt_config import (
                build_scenario,
                build_user_description,
            )
            from server.eval.judges.tutor_6d import evaluate_tutor_dimensions

            kwargs: dict[str, Any] = {}
            if normalized_tutor_dims:
                kwargs["dimension_order"] = normalized_tutor_dims
            tutor_scores = evaluate_tutor_dimensions(
                conversation_turns=conversation,
                enriched_conversation_turns=enriched_conversation,
                persona_id=persona.persona_id,
                scenario=build_scenario(task, persona.persona_id),
                user_description=build_user_description(persona),
                model=eval_model,
                category=category,
                requires_code=requires_code,
                abort_event=cancel_event,
                **kwargs,
            )
        except Exception as exc:  # noqa: BLE001 - persist as track-level failure
            tutor_scores = {"_dim_errors": {"tutor": [str(exc)]}}
            blockers.append(
                {"track": "tutor", "dimension": "tutor", "reason": str(exc)}
            )

    per_model = tutor_scores.get("_per_model") if isinstance(tutor_scores, dict) else {}
    reasons = tutor_scores.get("_dim_reasons") if isinstance(tutor_scores, dict) else {}
    evidences = (
        tutor_scores.get("_dim_evidence") if isinstance(tutor_scores, dict) else {}
    )
    dim_errors = (
        tutor_scores.get("_dim_errors") if isinstance(tutor_scores, dict) else {}
    )

    detail: dict[str, Any] = {}
    for dim in expected_dims:
        value = tutor_scores.get(dim) if isinstance(tutor_scores, dict) else None
        missing = not isinstance(value, (int, float))
        error_list = dim_errors.get(dim, []) if isinstance(dim_errors, dict) else []
        if missing and not error_list:
            error_list = ["Required Tutor dimension is missing"]
        if error_list:
            blockers.append(
                {
                    "track": "tutor",
                    "dimension": dim,
                    "reason": "; ".join(str(e) for e in error_list),
                }
            )
        detail[dim] = {
            "score": value if not missing else None,
            "status": "failed" if error_list else "success",
            "required_for_track_score": True,
            "reason": (
                "; ".join(str(e) for e in error_list)
                if error_list
                else ((reasons or {}).get(dim, "") if isinstance(reasons, dict) else "")
            ),
            "evidence": (
                (evidences or {}).get(dim, []) if isinstance(evidences, dict) else []
            ),
            "per_model": (
                {
                    model: dims.get(dim)
                    for model, dims in per_model.items()
                    if isinstance(dims, dict) and dim in dims
                }
                if isinstance(per_model, dict)
                else {}
            ),
        }
        for model_data in detail[dim]["per_model"].values():
            if isinstance(model_data, dict) and model_data.get("judge_metadata"):
                detail[dim]["judge_metadata"] = model_data["judge_metadata"]
                break
        if error_list:
            detail[dim]["error"] = "; ".join(str(e) for e in error_list)

    for key, value in tutor_scores.items():
        if str(key).startswith("_"):
            detail[key] = value
    detail.setdefault("_weights_used", _weights_used(expected_dims))
    detail["_blocking_missing"] = list(blockers)

    score = (
        None
        if blockers
        else _tutor_score(
            tutor_scores,
            category=category,
            requires_code=requires_code,
        )
    )
    return TrackResult(
        track="tutor",
        score=score,
        status=track_status(score, blockers),
        detail=detail,
        blocking_missing=blockers,
        eval_cost=eval_cost_from(tutor_scores),
        eval_cost_by_model=cost_by_model_from(tutor_scores),
    )
