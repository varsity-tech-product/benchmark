"""Evaluation script for D06: Tick Data Aggregation."""

import json
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from evaluation.test_scripts._track_a_hybrid_eval import evaluate_track_a_hybrid

PERSONA_RULES = {
    "beginner": ["new to", "beginner", "simple terms", "plain language"],
    "intermediate": ["resample", "pandas", "ohlc", "implementation"],
    "advanced": ["microstructure", "out-of-order", "latency", "session"],
}

ADAPTATION_MARKERS = {
    "beginner_strong": [
        "in plain language",
        "step by step",
        "what this means",
        "let's break this down",
        "quick checklist",
    ],
    "beginner_weak": ["simple", "for example", "intuition"],
    "beginner_min_strong": 1,
    "beginner_min_total": 2,
    "intermediate_markers": [
        "resample",
        "ohlc",
        "1min",
        "5min",
        "timestamp",
        "deduplicate",
    ],
    "intermediate_min": 2,
    "advanced_markers": [
        "out-of-order",
        "latency",
        "stale ticks",
        "trade vs quote",
        "session boundary",
        "timezone normalization",
    ],
    "advanced_min": 3,
    "fallback_markers": [
        "resample",
        "ohlc",
        "timezone",
        "checklist",
    ],
    "fallback_min": 2,
}

CONCEPT_BUCKETS = [
    ["resample", "ohlc", "bar", "1min", "5min", "candlestick"],
    ["timestamp", "out-of-order", "duplicate", "stale", "latency"],
    ["volume aggregation", "utc", "market hours", "session", "timezone"],
]


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    eval_context: dict = None,
) -> dict:
    """Evaluate tutoring quality plus executable-code evidence for D06."""
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
