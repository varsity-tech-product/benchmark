"""Evaluation script for I01: Implement SMA in pandas."""

import json
import os
import re


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
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
            output = str(log.get("result", ""))

            # Check result for successful SMA output
            if re.search(r"(SMA|sma|moving.?average)", output, re.IGNORECASE):
                results["code_runs_without_error"] = True
            # Check result for successful execution (no traceback)
            if log.get("success", False) and "traceback" not in output.lower():
                if re.search(r"\d+\.\d+", output):
                    results["code_runs_without_error"] = True

            # Check all input_args for code artifacts
            for key, value in log.get("input_args", {}).items():
                text = str(value)
                if re.search(r"\.rolling\(.*?\)\.mean\(\)", text):
                    results["sma_implemented_correctly"] = True

            # Check for compute_indicator SMA equivalent (tool-name agnostic)
            # If any input_args contain indicator=SMA and result has success + numbers
            for key, value in log.get("input_args", {}).items():
                if str(value).upper() == "SMA":
                    if log.get("success", False):
                        results["sma_implemented_correctly"] = True
                        results["code_runs_without_error"] = True

    # Infer NaN handling from successful rolling().mean()
    if results["sma_implemented_correctly"] and results["code_runs_without_error"]:
        results["handles_nan_values"] = True

    score = sum(
        [
            0.50 if results["sma_implemented_correctly"] else 0,
            0.30 if results["code_runs_without_error"] else 0,
            0.20 if results["handles_nan_values"] else 0,
        ]
    )
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
