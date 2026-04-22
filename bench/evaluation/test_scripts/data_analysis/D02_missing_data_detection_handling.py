"""Evaluation script for D02: Missing Data Detection & Handling.

QR Programmatic: checks tool outputs and workspace for evidence that
missing values were profiled, gaps were analyzed, and handling was applied.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.evidence_helpers import (
    apply_data_source_cap,
    checklist_score,
    collect_evidence,
    has_keywords,
    has_number,
)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "missing_values_profiled": False,
        "gap_analysis_performed": False,
        "handling_applied": False,
        "score": 0.0,
    }

    combined = collect_evidence(workspace_path, tool_logs)

    # 1. Missing values profiled (0.35)
    profile_ops = [
        "isna",
        "isnull",
        "info()",
        "count()",
        "notnull",
        "notna",
        "compute_statistics",
        "missing_count",
        "missing_pct",
    ]
    if has_keywords(combined, profile_ops) and has_number(combined):
        results["missing_values_profiled"] = True

    # 2. Gap analysis performed (0.35)
    gap_ops = ["diff()", "timedelta", "asfreq", "date_range", "freq=", "bday"]
    if has_keywords(combined, gap_ops):
        results["gap_analysis_performed"] = True

    # 3. Handling applied (0.30)
    handle_ops = ["fillna", "dropna", "interpolate", "ffill", "bfill", "pad"]
    if has_keywords(combined, handle_ops):
        results["handling_applied"] = True

    _checklist = [
        {
            "item": "missing_values_profiled",
            "weight": 0.35,
            "passed": results["missing_values_profiled"],
        },
        {
            "item": "gap_analysis_performed",
            "weight": 0.35,
            "passed": results["gap_analysis_performed"],
        },
        {
            "item": "handling_applied",
            "weight": 0.30,
            "passed": results["handling_applied"],
        },
    ]
    score = checklist_score(_checklist)

    score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
