"""Shared LLM judge call policy for evaluation tracks."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any


def _is_json_parse_error(exc: Exception, raw_response: str | None) -> bool:
    text = str(exc).lower()
    return (
        bool(raw_response)
        or "invalid json" in text
        or "score" in text
        and "json" in text
    )


async def llm_call_with_retry(
    metric_factory: Callable[[], Any],
    test_case: Any,
    *,
    dimension_name: str,
    required_for_track_score: bool = True,
    max_attempts: int = 3,
    retry_sleep_seconds: float = 1.0,
) -> dict[str, Any]:
    """Call an ``EwanConvGEval`` metric without fallback score extraction.

    The only retryable failure is malformed/missing-score JSON. After all
    attempts fail, the returned dimension has ``score=None`` and must not be
    aggregated by the caller.
    """

    total_cost = 0.0
    last_exc: Exception | None = None
    last_raw: str | None = None

    for attempt in range(max_attempts):
        metric = metric_factory()
        cost_before = float(getattr(metric, "evaluation_cost", 0.0) or 0.0)
        try:
            score = await metric.a_measure(test_case)
            cost_after = float(getattr(metric, "evaluation_cost", 0.0) or 0.0)
            total_cost += max(0.0, cost_after - cost_before)
            return {
                "score": score,
                "status": "success",
                "required_for_track_score": required_for_track_score,
                "reason": getattr(metric, "reason", "") or "",
                "evidence": list(getattr(metric, "evidence", []) or []),
                "_eval_cost": round(total_cost, 6),
            }
        except Exception as exc:  # noqa: BLE001 - caller needs structured failure
            last_exc = exc
            cost_after = float(getattr(metric, "evaluation_cost", 0.0) or 0.0)
            total_cost += max(0.0, cost_after - cost_before)
            raw = getattr(metric, "_raw_failed_response", None)
            if raw:
                last_raw = str(raw)
            if attempt < max_attempts - 1 and _is_json_parse_error(exc, last_raw):
                await asyncio.sleep(retry_sleep_seconds)
                continue
            break

    message = str(last_exc) if last_exc else "LLM judge failed"
    diagnostics: dict[str, Any] = {}
    if last_raw:
        diagnostics["raw_response_excerpt"] = last_raw[:500]
    diagnostics["attempts"] = max_attempts
    return {
        "score": None,
        "status": "failed",
        "required_for_track_score": required_for_track_score,
        "reason": message,
        "evidence": [],
        "error": message,
        "diagnostics": diagnostics,
        "_eval_cost": round(total_cost, 6),
    }
