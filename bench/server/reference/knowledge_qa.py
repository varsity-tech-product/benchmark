"""Small deterministic scorer for reference single-answer QA checks."""

from __future__ import annotations

import re
from typing import Any


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    return {
        token
        for token in _TOKEN_RE.findall(text.lower())
        if len(token) > 2 and token not in {"the", "and", "for", "with", "that"}
    }


def evaluate(
    *,
    question: str,
    reference_answer: str,
    actual_output: str,
    context: str | None = None,
) -> dict[str, Any]:
    """Score an answer against a reference answer with transparent overlap."""

    if not actual_output.strip():
        return {
            "score": 0.0,
            "status": "failed",
            "reason": "empty output",
            "evidence": [],
        }

    reference_tokens = _tokens(reference_answer)
    output_tokens = _tokens(actual_output)
    if not reference_tokens:
        return {
            "score": 0.5,
            "status": "skipped",
            "reason": "reference answer unavailable",
            "evidence": [],
            "required_for_track_score": False,
        }

    overlap = reference_tokens & output_tokens
    recall = len(overlap) / len(reference_tokens)
    precision = len(overlap) / max(len(output_tokens), 1)
    score = min(1.0, round((0.75 * recall + 0.25 * precision) * 1.25, 4))
    return {
        "score": score,
        "status": "success",
        "reason": f"reference token recall={recall:.2%}, precision={precision:.2%}",
        "evidence": sorted(overlap)[:20],
        "question": question,
        "context_present": bool(context),
    }
