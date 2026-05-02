"""Evaluation script for X01: Fix off-by-one in MA calculation."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.evidence_helpers import apply_data_source_cap


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate whether the off-by-one bug was fixed in code artifacts.

    QR checks: Is rolling(20) present and rolling(19) absent in code artifacts?
    Does the fixed code run successfully?
    """
    results = {
        "bug_is_fixed": False,
        "code_runs_without_error": False,
        "score": 0.0,
    }

    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # Check workspace .py files for the fix
    for fname in workspace_files:
        if fname.endswith(".py"):
            fpath = os.path.join(workspace_path, fname)
            try:
                with open(fpath) as f:
                    code = f.read()
                all_rolling = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", code)
                has_20 = "20" in all_rolling
                has_19 = "19" in all_rolling
                has_50 = "50" in all_rolling
                if has_20 and has_50 and not has_19:
                    results["bug_is_fixed"] = True
            except (IOError, UnicodeDecodeError):
                pass

    # Scan ALL tool logs (tool-name agnostic)
    if tool_logs:
        for log in tool_logs:
            output = str(log.result or "")

            # Check result for successful execution
            if log.success and "traceback" not in output.lower():
                if any(
                    kw in output.lower()
                    for kw in ["sharpe", "return", "signal", "crossover", "sma"]
                ):
                    results["code_runs_without_error"] = True

            # Check all args for fix evidence
            for key, value in log.args.items():
                text = str(value)
                all_rolling = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", text)
                has_20 = "20" in all_rolling
                has_19 = "19" in all_rolling
                if has_20 and not has_19:
                    results["bug_is_fixed"] = True
                # Check for fix in shell commands
                if "rolling(20)" in text or "rolling(window=20)" in text:
                    results["bug_is_fixed"] = True

    # Also check student_code path for in-place edits
    buggy_file = os.path.join(workspace_path, "..", "student_code", "ma_offbyone.py")
    if os.path.exists(buggy_file):
        try:
            with open(buggy_file) as f:
                code = f.read()
            all_rolling = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", code)
            if "20" in all_rolling and "19" not in all_rolling:
                results["bug_is_fixed"] = True
        except (IOError, UnicodeDecodeError):
            pass

    _checklist = [
        {"item": "bug_is_fixed", "weight": 0.65, "passed": results["bug_is_fixed"]},
        {
            "item": "code_runs_without_error",
            "weight": 0.35,
            "passed": results["code_runs_without_error"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # Data source verification — cap score if task data wasn't accessed
    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
