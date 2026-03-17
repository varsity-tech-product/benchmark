"""Evaluation script for D04: OHLCV Summary Statistics.

QR Programmatic: checks tool outputs and workspace for evidence that
descriptive statistics were computed, multiple columns were analyzed,
and distribution/outlier analysis was performed.
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
        "descriptive_stats_computed": False,
        "multi_column_analysis": False,
        "distribution_or_outlier_analysis": False,
        "score": 0.0,
    }

    combined = _collect_evidence(workspace_path, tool_logs)

    # 1. Descriptive statistics computed (0.40)
    stat_kws = [
        "describe",
        "mean",
        "std",
        "quantile",
        "median",
        "count",
        "compute_statistics",
        "descriptive",
        "kurtosis",
    ]
    if _has_keywords(combined, stat_kws) and _has_number(combined):
        results["descriptive_stats_computed"] = True

    # 2. Multi-column analysis (0.30)
    ohlcv_cols = ["open", "high", "low", "close", "volume"]
    col_hits = sum(1 for col in ohlcv_cols if col in combined)
    if col_hits >= 2:
        results["multi_column_analysis"] = True

    # 3. Distribution or outlier analysis (0.30)
    dist_kws = [
        "skew",
        "kurtosis",
        "hist",
        "percentile",
        "outlier",
        "iqr",
        "boxplot",
        "distribution",
        "quartile",
    ]
    if _has_keywords(combined, dist_kws):
        results["distribution_or_outlier_analysis"] = True

    _checklist = [
        {
            "item": "descriptive_stats_computed",
            "weight": 0.40,
            "passed": results["descriptive_stats_computed"],
        },
        {
            "item": "multi_column_analysis",
            "weight": 0.30,
            "passed": results["multi_column_analysis"],
        },
        {
            "item": "distribution_or_outlier_analysis",
            "weight": 0.30,
            "passed": results["distribution_or_outlier_analysis"],
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
