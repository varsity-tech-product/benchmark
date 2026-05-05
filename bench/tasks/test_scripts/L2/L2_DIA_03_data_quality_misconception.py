"""Eval script for L2_DIA_03_data_quality_misconception.

Heuristic conversation-concept checker. The agent must steer the student
toward upstream data validation (split-adjustment artifact) and resist
adding model complexity.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from common.debug_check import check_conversation_concepts  # noqa: E402
from common.evidence_helpers import apply_data_source_cap  # noqa: E402


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "data_quality_flagged": False,
        "split_adjustment_identified": False,
        "programmatic_sanity_check_proposed": False,
        "model_changes_resisted": False,
        "data_validation_taught": False,
        "score": 0.0,
    }

    flagged = check_conversation_concepts(
        conversation or [],
        [
            "data corruption",
            "data quality",
            "data integrity",
            "suspicious data",
            "ask about the data",
            "check the data",
            "level shift",
            "jumps",
            "discontinuity",
        ],
        tool_logs or [],
    )
    results["data_quality_flagged"] = flagged["fraction"] > 0

    split_adj = check_conversation_concepts(
        conversation or [],
        [
            "split adjustment",
            "split-adjusted",
            "split adjusted",
            "adjusted price",
            "adjusted vs raw",
            "inconsistent adjustment",
            "corporate action",
        ],
        tool_logs or [],
    )
    results["split_adjustment_identified"] = split_adj["fraction"] > 0

    sanity = check_conversation_concepts(
        conversation or [],
        [
            "sanity check",
            "diff against",
            "compare against",
            "level shift",
            "day-over-day jump",
            "sudden jump",
            "reference source",
            "raw close",
            "qqq_clean",
        ],
        tool_logs or [],
    )
    results["programmatic_sanity_check_proposed"] = sanity["fraction"] > 0

    pushback = check_conversation_concepts(
        conversation or [],
        [
            "before tweaking",
            "before adding",
            "do not add",
            "do not tune",
            "fix the data",
            "data first",
            "not a model issue",
            "model is fine",
            "no point retuning",
        ],
        tool_logs or [],
    )
    results["model_changes_resisted"] = pushback["fraction"] > 0

    teaching = check_conversation_concepts(
        conversation or [],
        [
            "upstream",
            "data validation",
            "garbage in",
            "data pipeline",
            "data source",
            "validate data",
        ],
        tool_logs or [],
    )
    results["data_validation_taught"] = teaching["fraction"] > 0

    _checklist = [
        {"item": "data_quality_flagged", "weight": 0.20, "passed": results["data_quality_flagged"]},
        {"item": "split_adjustment_identified", "weight": 0.30, "passed": results["split_adjustment_identified"]},
        {"item": "programmatic_sanity_check_proposed", "weight": 0.20, "passed": results["programmatic_sanity_check_proposed"]},
        {"item": "model_changes_resisted", "weight": 0.20, "passed": results["model_changes_resisted"]},
        {"item": "data_validation_taught", "weight": 0.10, "passed": results["data_validation_taught"]},
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
