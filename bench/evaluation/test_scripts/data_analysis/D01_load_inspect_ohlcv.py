"""Evaluation script for D01: Load and inspect OHLCV data."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.evidence_helpers import apply_data_source_cap, checklist_score


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate whether the agent successfully helped load and inspect data.

    QR checks: Are data artifacts present? Are statistical summaries produced?
    Does NOT check which tools were used (that's QP's job).
    """
    results = {
        "data_loaded_successfully": False,
        "basic_stats_computed": False,
        "score": 0.0,
    }

    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # Check workspace for data files
    for fname in workspace_files:
        if fname.endswith((".csv", ".parquet")):
            results["data_loaded_successfully"] = True
            break

    # Scan ALL tool logs for evidence (tool-name agnostic)
    if tool_logs:
        for log in tool_logs:
            tool_name = log.name.lower() if log.name else ""
            # Check all result values for data evidence
            output = str(log.result or "").lower()
            if any(
                kw in output
                for kw in [
                    "ohlcv",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                    "rows",
                    "columns",
                    "date range",
                ]
            ):
                results["data_loaded_successfully"] = True
            if (
                any(
                    kw in output
                    for kw in [
                        "describe",
                        "mean",
                        "std",
                        "count",
                        "dtype",
                        "min",
                        "max",
                        "25%",
                        "50%",
                        "75%",
                        "kurtosis",
                        "missing_count",
                    ]
                )
                or tool_name == "compute_statistics"
            ):
                results["basic_stats_computed"] = True

            # Check all args values for code/data artifacts
            for key, value in log.args.items():
                text = str(value).lower()
                if re.search(r"\.(describe|info|dtypes|head|tail|shape)\(\)", text):
                    results["basic_stats_computed"] = True
                if re.search(r"(read_csv|fetch_market|ohlcv)", text):
                    results["data_loaded_successfully"] = True

    # Check workspace text files for stats output
    for fname in workspace_files:
        if fname.endswith((".txt", ".log", ".md", ".json")):
            fpath = os.path.join(workspace_path, fname)
            try:
                with open(fpath) as f:
                    content = f.read().lower()
                if any(kw in content for kw in ["describe", "mean", "std", "count"]):
                    results["basic_stats_computed"] = True
            except (IOError, UnicodeDecodeError):
                pass

    _checklist = [
        {
            "item": "data_loaded_successfully",
            "weight": 0.50,
            "passed": results["data_loaded_successfully"],
        },
        {
            "item": "basic_stats_computed",
            "weight": 0.50,
            "passed": results["basic_stats_computed"],
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
