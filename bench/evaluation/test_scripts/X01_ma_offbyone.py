"""Evaluation script for X01: Fix off-by-one in MA calculation."""

import json
import os
import re


def evaluate(workspace_path: str, tool_logs: list = None) -> dict:
    """Evaluate whether the agent helped fix the off-by-one bug.

    The buggy code in student_code/ma_offbyone.py has rolling(19) instead
    of rolling(20) for the 20-day SMA. The agent should guide the student
    to find and fix this bug.

    Args:
        workspace_path: Path to the agent's workspace directory.
        tool_logs: List of dicts recording each MCP tool call.

    Returns:
        Dict with boolean metrics and a float score in [0, 1].
    """
    results = {
        "bug_is_fixed": False,
        "code_runs_without_error": False,
        "score": 0.0,
    }

    # Check workspace for fixed code files
    for fname in os.listdir(workspace_path) if os.path.isdir(workspace_path) else []:
        fpath = os.path.join(workspace_path, fname)
        if fname.endswith(".py"):
            try:
                with open(fpath) as f:
                    code = f.read()
                # Check if the fix is present: rolling(20) for SMA_20
                # The bug was rolling(19) for SMA_20
                rolling_calls = re.findall(
                    r'["\']?SMA_20["\']?\s*.*?rolling\((?:window\s*=\s*)?(\d+)\)',
                    code,
                    re.DOTALL,
                )
                # Also check generic pattern — if rolling(19) is gone and
                # rolling(20) is present alongside rolling(50)
                all_rolling = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", code)
                has_20 = "20" in all_rolling
                has_19 = "19" in all_rolling
                has_50 = "50" in all_rolling

                if has_20 and has_50 and not has_19:
                    results["bug_is_fixed"] = True
                elif rolling_calls and "20" in rolling_calls:
                    results["bug_is_fixed"] = True
            except (IOError, UnicodeDecodeError):
                pass

    # Check tool logs for evidence of the fix
    if tool_logs:
        for log in tool_logs:
            if log.get("name") == "shell_exec":
                output = str(log.get("result", ""))
                cmd = str(log.get("input_args", {}).get("command", ""))

                # Check if the fixed code was executed successfully
                if log.get("success", False) and "traceback" not in output.lower():
                    # Look for signs the code ran to completion
                    if any(
                        kw in output.lower()
                        for kw in ["sharpe", "return", "signal", "crossover", "sma"]
                    ):
                        results["code_runs_without_error"] = True

                # Check if the fix appears in executed commands
                if "rolling(20)" in cmd or "rolling(window=20)" in cmd:
                    results["bug_is_fixed"] = True

            # Check file_write for the fix
            if log.get("name") == "file_write":
                content = str(log.get("input_args", {}).get("content", ""))
                all_rolling = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", content)
                has_20 = "20" in all_rolling
                has_19 = "19" in all_rolling
                if has_20 and not has_19:
                    results["bug_is_fixed"] = True

    # Also check the original student_code path in case it was edited in-place
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

    score = sum(
        [
            0.7 if results["bug_is_fixed"] else 0,
            0.3 if results["code_runs_without_error"] else 0,
        ]
    )
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
