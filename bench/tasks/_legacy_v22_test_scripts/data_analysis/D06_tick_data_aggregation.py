"""Evaluation script for D06: Tick Data Aggregation.

QR Programmatic: checks tool outputs and workspace for evidence that
tick data was resampled, timestamps were handled, and OHLCV bars were produced.
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
)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "resampling_performed": False,
        "timestamp_handling": False,
        "bar_data_produced": False,
        "score": 0.0,
    }

    combined = collect_evidence(workspace_path, tool_logs)
    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # 1. Resampling performed (0.40)
    resample_kws = ["resample", "ohlc()", "grouper", "timegrouper", "groupby", "agg("]
    if has_keywords(combined, resample_kws):
        results["resampling_performed"] = True

    # 2. Timestamp handling (0.30)
    ts_kws = [
        "to_datetime",
        "sort_values",
        "sort_index",
        "drop_duplicates",
        "tz_convert",
        "tz_localize",
        "datetimeindex",
    ]
    if has_keywords(combined, ts_kws):
        results["timestamp_handling"] = True

    # 3. Bar data produced (0.30)
    ohlc_cols = ["open", "high", "low", "close"]
    ohlc_hits = sum(1 for col in ohlc_cols if col in combined)
    has_data_file = any(f.endswith((".csv", ".parquet")) for f in workspace_files)
    if ohlc_hits >= 3 or has_data_file:
        results["bar_data_produced"] = True

    _checklist = [
        {
            "item": "resampling_performed",
            "weight": 0.40,
            "passed": results["resampling_performed"],
        },
        {
            "item": "timestamp_handling",
            "weight": 0.30,
            "passed": results["timestamp_handling"],
        },
        {
            "item": "bar_data_produced",
            "weight": 0.30,
            "passed": results["bar_data_produced"],
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
