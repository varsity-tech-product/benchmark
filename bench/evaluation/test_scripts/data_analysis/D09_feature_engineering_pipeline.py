"""Evaluation script for D09: Feature Engineering Pipeline.

QR Programmatic: checks tool outputs and workspace for evidence that
features were constructed, leakage was checked, and redundancy was assessed.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.data_source_check import verify_data_source


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

    combined = _collect_evidence(workspace_path, tool_logs)

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
    if _has_keywords(combined, feat_kws) and _has_number(combined):
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
    if _has_keywords(combined, leak_kws):
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
    if _has_keywords(combined, redund_kws):
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
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # Data source verification — cap score if task data wasn't accessed
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


def _collect_evidence(workspace_path: str, tool_logs: list) -> str:
    parts = []
    for log in tool_logs or []:
        parts.append(log.name)
        parts.append(str(log.args))
        parts.append(str(log.result or ""))
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if fname.endswith((".txt", ".json", ".md", ".csv", ".log")):
                try:
                    with open(os.path.join(workspace_path, fname)) as f:
                        parts.append(f.read()[:2000])
                except (IOError, UnicodeDecodeError):
                    pass
    return " ".join(parts).lower()


def _has_keywords(text: str, keywords: list[str]) -> bool:
    return any(kw in text for kw in keywords)


def _has_number(text: str) -> bool:
    return bool(re.search(r"-?\d+\.?\d*", text))


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
