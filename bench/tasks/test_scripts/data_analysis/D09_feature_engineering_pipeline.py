"""Evaluation script for D09: Feature Engineering Pipeline.

QR Programmatic: checks tool outputs and workspace for evidence that
features were constructed, leakage was checked, and redundancy was assessed.
"""

import json
import os
import re
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
        "features_constructed": False,
        "leakage_check_performed": False,
        "redundancy_check_performed": False,
        "score": 0.0,
    }

    combined = collect_evidence(workspace_path, tool_logs)

    # 1. Features constructed (0.35)
    feat_kws = [
        "rolling",
        "pct_change",
        "ewm",
        "shift",
        "momentum",
        "volatility",
        "feature",
        "indicator",
        "compute_indicator",
    ]
    if has_keywords(combined, feat_kws) and has_number(combined):
        results["features_constructed"] = True

    # 2. Leakage check performed (0.35)
    leak_kws = [
        "look-ahead",
        "look_ahead",
        "leakage",
        "future",
        "train.*test.*split",
        "timeseriessplit",
        "point-in-time",
    ]
    if has_keywords(combined, leak_kws):
        results["leakage_check_performed"] = True
    # Also check via regex for shift-based leakage patterns
    if not results["leakage_check_performed"]:
        if re.search(r"shift\(.*\d+\)", combined):
            results["leakage_check_performed"] = True

    # 3. Redundancy check performed (0.30)
    redund_kws = [
        ".corr()",
        "heatmap",
        "vif",
        "multicollinearity",
        "variance_inflation",
        "correlation matrix",
        "feature selection",
        "redundant",
        "compute_statistics",
    ]
    if has_keywords(combined, redund_kws):
        results["redundancy_check_performed"] = True

    _checklist = [
        {
            "item": "features_constructed",
            "weight": 0.35,
            "passed": results["features_constructed"],
        },
        {
            "item": "leakage_check_performed",
            "weight": 0.35,
            "passed": results["leakage_check_performed"],
        },
        {
            "item": "redundancy_check_performed",
            "weight": 0.30,
            "passed": results["redundancy_check_performed"],
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
