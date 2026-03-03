"""Evaluation script for D02: Missing Data Detection & Handling."""

import json
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from evaluation.test_scripts._track_a_hybrid_eval import evaluate_track_a_hybrid

PERSONA_RULES = {
    "beginner": ["new to", "beginner", "simple terms", "plain language"],
    "intermediate": ["pandas", "pipeline", "dataframe", "implementation"],
    "advanced": ["calendar-aware", "methodology", "production", "audit"],
}

ADAPTATION_MARKERS = {
    "beginner_strong": [
        "in plain language",
        "step by step",
        "what this means",
        "let's break this down",
        "quick checklist",
    ],
    "beginner_weak": ["simple", "for example", "don't worry"],
    "beginner_min_strong": 1,
    "beginner_min_total": 2,
    "intermediate_markers": [
        "pandas",
        "isna",
        "notna",
        "validation checklist",
        "pipeline",
        "data quality checks",
    ],
    "intermediate_min": 2,
    "advanced_markers": [
        "trading calendar",
        "holiday",
        "imputation bias",
        "point-in-time",
        "audit trail",
        "corporate action",
    ],
    "advanced_min": 3,
    "fallback_markers": [
        "step by step",
        "checklist",
        "missing values",
        "holiday",
    ],
    "fallback_min": 2,
}

CONCEPT_BUCKETS = [
    ["missing", "isna", "null", "nan", "missingness"],
    ["holiday", "weekend", "trading calendar", "business day", "date gap"],
    ["dropna", "ffill", "bfill", "interpolate", "imputation", "fillna"],
]


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    eval_context: dict = None,
) -> dict:
    """Evaluate tutoring quality plus executable-code evidence for D02."""
    return evaluate_track_a_hybrid(
        workspace_path=workspace_path,
        tool_logs=tool_logs,
        conversation=conversation,
        eval_context=eval_context,
        persona_rules=PERSONA_RULES,
        adaptation_markers=ADAPTATION_MARKERS,
        concept_buckets=CONCEPT_BUCKETS,
        concept_min_covered=2,
    )


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
