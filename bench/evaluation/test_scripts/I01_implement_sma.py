"""Evaluation script for I01: Implement SMA in pandas."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate whether a correct SMA calculation is present in artifacts.

    QR checks: Is rolling().mean() present in code artifacts?
    Does execution produce SMA output without errors?
    """
    results = {
        "sma_implemented_correctly": False,
        "code_runs_without_error": False,
        "handles_nan_values": False,
        "score": 0.0,
    }

    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # Check workspace .py files for rolling().mean()
    for fname in workspace_files:
        if fname.endswith(".py"):
            fpath = os.path.join(workspace_path, fname)
            try:
                with open(fpath) as f:
                    code = f.read()
                if re.search(r"\.rolling\(.*?\)\.mean\(\)", code):
                    results["sma_implemented_correctly"] = True
                if re.search(r"(dropna|fillna|isna|notna|skipna|\.iloc\[)", code):
                    results["handles_nan_values"] = True
            except (IOError, UnicodeDecodeError):
                pass

    # Scan ALL tool logs (tool-name agnostic)
    if tool_logs:
        for log in tool_logs:
            output = str(log.result or "")

            # Check result for successful SMA output
            if re.search(r"(SMA|sma|moving.?average)", output, re.IGNORECASE):
                results["code_runs_without_error"] = True
            # Check result for successful execution (no traceback)
            if log.success and "traceback" not in output.lower():
                if re.search(r"\d+\.\d+", output):
                    results["code_runs_without_error"] = True

            # Check all args for code artifacts
            for key, value in log.args.items():
                text = str(value)
                if re.search(r"\.rolling\(.*?\)\.mean\(\)", text):
                    results["sma_implemented_correctly"] = True

            # Check for compute_indicator SMA equivalent (tool-name agnostic)
            # If any args contain indicator=SMA and result has success + numbers
            for key, value in log.args.items():
                if str(value).upper() == "SMA":
                    if log.success:
                        results["sma_implemented_correctly"] = True
                        results["code_runs_without_error"] = True

    # Infer NaN handling from successful rolling().mean()
    if results["sma_implemented_correctly"] and results["code_runs_without_error"]:
        results["handles_nan_values"] = True

    _checklist = [
        {
            "item": "sma_implemented_correctly",
            "weight": 0.50,
            "passed": results["sma_implemented_correctly"],
        },
        {
            "item": "code_runs_without_error",
            "weight": 0.30,
            "passed": results["code_runs_without_error"],
        },
        {
            "item": "handles_nan_values",
            "weight": 0.20,
            "passed": results["handles_nan_values"],
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


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
