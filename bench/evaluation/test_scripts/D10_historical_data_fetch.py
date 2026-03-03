"""Evaluation script for D10: Historical Data Fetch (Track A hybrid)."""

import json
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from evaluation.test_scripts._track_a_hybrid_eval import evaluate_track_a_hybrid

PERSONA_RULES = {
    "beginner": ["new to", "beginner", "plain", "simple terms"],
    "intermediate": ["ingestion script", "implementation", "only docs"],
    "advanced": [
        "reproducible",
        "point-in-time",
        "revision leakage",
        "methodology",
    ],
}

ADAPTATION_MARKERS = {
    "beginner_strong": [
        "in plain language",
        "step by step",
        "what this means",
        "let's break this down",
        "quick checklist",
    ],
    "beginner_weak": ["simple", "for example"],
    "beginner_min_strong": 1,
    "beginner_min_total": 2,
    "intermediate_markers": [
        "script",
        "pipeline",
        "schema",
        "validation checklist",
        "python",
        "pandas",
    ],
    "intermediate_min": 2,
    "advanced_markers": [
        "point-in-time",
        "as-of",
        "release lag",
        "revision",
        "corporate action",
        "leakage",
        "vintage",
    ],
    "advanced_min": 3,
    "fallback_markers": [
        "step by step",
        "checklist",
        "point-in-time",
        "revision",
        "adjusted prices",
    ],
    "fallback_min": 2,
}

CONCEPT_BUCKETS = [
    ["adjusted", "adj close", "split", "dividend", "corporate action"],
    ["revision", "release lag", "publication lag", "vintage"],
    ["point-in-time", "as-of", "as of", "look-ahead", "leakage"],
]


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    eval_context: dict = None,
) -> dict:
    """Evaluate tutoring quality plus runnable-code evidence for D10."""
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
