"""Shared scoring utilities for LLM-as-judge evaluators.

Provides:
- normalize_10pt(): Convert integer 1-10 to float 0.0-1.0
- build_10pt_rubric(): Generate standard 10-point scale description
- parse_llm_score(): Extract and normalize score from LLM response
"""


def normalize_10pt(score: int | float, default: float = 0.5) -> float:
    """Normalize a 1-10 integer score to 0.0-1.0 range.

    Mapping: 1→0.0, 2→0.111, 3→0.222, ..., 9→0.889, 10→1.0
    Formula: (score - 1) / 9

    Non-integer floats are rounded to nearest integer first.
    Out-of-range values are clamped to [1, 10].
    """
    try:
        v = float(score)
    except (TypeError, ValueError):
        return default
    v = round(v)
    v = max(1, min(10, v))
    return round((v - 1) / 9, 4)


def denormalize_10pt(normalized: float) -> int:
    """Convert normalized 0.0-1.0 back to 1-10 scale (inverse of normalize_10pt)."""
    return round(normalized * 9 + 1)


# 10-point ordinal values (for reference/testing)
SCALE_10PT = [round((i - 1) / 9, 4) for i in range(1, 11)]
# [0.0, 0.1111, 0.2222, 0.3333, 0.4444, 0.5556, 0.6667, 0.7778, 0.8889, 1.0]
