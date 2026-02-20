"""Evaluation script for I01: Implement SMA in pandas."""

import json
import os
import re


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    """Evaluate whether the agent helped implement a correct SMA calculation.

    Args:
        workspace_path: Path to the agent's workspace directory.
        tool_logs: List of dicts recording each MCP tool call.
        conversation: List of {role, content} dicts from the conversation.

    Returns:
        Dict with boolean metrics and a float score in [0, 1].
    """
    results = {
        "sma_implemented_correctly": False,
        "code_runs_without_error": False,
        "handles_nan_values": False,
        "score": 0.0,
    }

    # Check workspace for implementation files
    for fname in os.listdir(workspace_path) if os.path.isdir(workspace_path) else []:
        fpath = os.path.join(workspace_path, fname)
        if fname.endswith(".py"):
            try:
                with open(fpath) as f:
                    code = f.read()
                # Check for rolling().mean() pattern — canonical SMA implementation
                if re.search(r"\.rolling\(.*?\)\.mean\(\)", code):
                    results["sma_implemented_correctly"] = True
                # Check for NaN handling (dropna, fillna, isna, notna, skipna)
                if re.search(r"(dropna|fillna|isna|notna|skipna|\.iloc\[)", code):
                    results["handles_nan_values"] = True
            except (IOError, UnicodeDecodeError):
                pass

    # Check tool logs for successful execution
    if tool_logs:
        for log in tool_logs:
            if log.get("name") == "shell_exec":
                output = str(log.get("result", ""))
                # Check if code ran without error
                if (
                    log.get("success", False)
                    and "error" not in output.lower().split("traceback")[0]
                    if "traceback" not in output.lower()
                    else False
                ):
                    results["code_runs_without_error"] = True
                # Alternatively, check for successful output patterns
                if re.search(r"(SMA|sma|moving.?average)", output, re.IGNORECASE):
                    results["code_runs_without_error"] = True

                # Check for rolling implementation in executed code
                cmd = str(log.get("input_args", {}).get("command", ""))
                if re.search(r"\.rolling\(.*?\)\.mean\(\)", cmd):
                    results["sma_implemented_correctly"] = True

                # Check for NaN awareness in output or code
                if re.search(r"(NaN|nan|null|missing)", output):
                    results["handles_nan_values"] = True

    # If SMA is implemented and code runs, infer NaN handling from rolling()
    # since pandas rolling().mean() naturally produces NaN for the first (window-1) rows
    if results["sma_implemented_correctly"] and results["code_runs_without_error"]:
        results["handles_nan_values"] = True

    score = sum(
        [
            0.5 if results["sma_implemented_correctly"] else 0,
            0.3 if results["code_runs_without_error"] else 0,
            0.2 if results["handles_nan_values"] else 0,
        ]
    )
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
