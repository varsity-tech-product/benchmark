"""Evaluation script for D10: Historical Data Fetch.

QR Programmatic: checks tool outputs and workspace for evidence that
a data workflow was demonstrated, price adjustment awareness is present,
data validation was performed, and actual data was saved. (network-enabled sandbox)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.evidence_helpers import (
    checklist_score,
    collect_evidence,
    has_keywords,
    has_number,
)


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    results = {
        "data_workflow_demonstrated": False,
        "price_adjustment_awareness": False,
        "data_validation_performed": False,
        "data_saved_to_workspace": False,
        "score": 0.0,
    }

    combined = collect_evidence(workspace_path, tool_logs)
    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # 1. Data workflow demonstrated (0.30)
    workflow_kws = [
        "read_csv",
        "yfinance",
        "pd.read",
        "requests.get",
        "urlopen",
        "api",
        "download",
        "fred",
        "alpha_vantage",
        "pandas_datareader",
        "compute_statistics",
    ]
    has_fetch_code = any(f.endswith(".py") for f in workspace_files) and has_keywords(
        combined, workflow_kws
    )
    if has_fetch_code or has_keywords(combined, workflow_kws):
        results["data_workflow_demonstrated"] = True

    # 2. Price adjustment awareness (0.25)
    adj_kws = [
        "adjusted",
        "adj close",
        "adj_close",
        "split",
        "dividend",
        "corporate action",
        "unadjusted",
    ]
    if has_keywords(combined, adj_kws):
        results["price_adjustment_awareness"] = True

    # 3. Data validation performed (0.25)
    val_kws = [
        "shape",
        "dtypes",
        "describe",
        "date_range",
        "head",
        "info",
        "columns",
        "len(",
        "isnull",
        "isna",
        "descriptive",
        "missing_count",
        "missing_pct",
    ]
    if has_keywords(combined, val_kws) and has_number(combined):
        results["data_validation_performed"] = True

    # 4. Data saved to workspace (0.20)
    csv_files = [f for f in workspace_files if f.endswith(".csv")]
    for csv_f in csv_files:
        try:
            fpath = os.path.join(workspace_path, csv_f)
            with open(fpath) as fh:
                lines = fh.readlines()
            if len(lines) >= 5:
                results["data_saved_to_workspace"] = True
                break
        except (IOError, UnicodeDecodeError):
            pass

    _checklist = [
        {
            "item": "data_workflow_demonstrated",
            "weight": 0.30,
            "passed": results["data_workflow_demonstrated"],
        },
        {
            "item": "price_adjustment_awareness",
            "weight": 0.25,
            "passed": results["price_adjustment_awareness"],
        },
        {
            "item": "data_validation_performed",
            "weight": 0.25,
            "passed": results["data_validation_performed"],
        },
        {
            "item": "data_saved_to_workspace",
            "weight": 0.20,
            "passed": results["data_saved_to_workspace"],
        },
    ]
    score = checklist_score(_checklist)
    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
