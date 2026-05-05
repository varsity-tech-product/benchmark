"""Evaluation script for D04: OHLCV Summary Statistics.

QR Programmatic: checks tool outputs and workspace for evidence that
descriptive statistics were computed, multiple columns were analyzed,
and distribution/outlier analysis was performed.
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
        "descriptive_stats_computed": False,
        "multi_column_analysis": False,
        "distribution_or_outlier_analysis": False,
        "score": 0.0,
    }

    combined = collect_evidence(workspace_path, tool_logs)

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
    if has_keywords(combined, stat_kws) and has_number(combined):
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
    if has_keywords(combined, dist_kws):
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
    score = checklist_score(_checklist)

    score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
