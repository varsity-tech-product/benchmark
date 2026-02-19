"""Evaluation script for D01: Load and inspect OHLCV data."""

import json
import os


def evaluate(workspace_path: str, tool_logs: list = None) -> dict:
    """Evaluate whether the agent successfully helped load and inspect data.

    Args:
        workspace_path: Path to the agent's workspace directory.
        tool_logs: List of dicts recording each MCP tool call, each with
            keys like 'name', 'input_args', 'result', 'success'.

    Returns:
        Dict with boolean metrics and a float score in [0, 1].
    """
    results = {
        "data_loaded_successfully": False,
        "basic_stats_computed": False,
        "score": 0.0,
    }

    # Check if any data was loaded (look for evidence in workspace or logs)
    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # Check tool logs for data loading
    if tool_logs:
        for log in tool_logs:
            if log.get("name") in ("fetch_market_data", "file_read"):
                if log.get("success", False):
                    results["data_loaded_successfully"] = True
            if log.get("name") == "shell_exec":
                output = log.get("result", "")
                if any(
                    kw in output.lower()
                    for kw in ["describe", "mean", "std", "count", "dtype"]
                ):
                    results["basic_stats_computed"] = True

    # Also check for saved exploration artifacts in workspace
    for fname in workspace_files:
        if fname.endswith((".csv", ".parquet")):
            results["data_loaded_successfully"] = True
        if fname.endswith((".txt", ".log", ".md")):
            fpath = os.path.join(workspace_path, fname)
            try:
                with open(fpath) as f:
                    content = f.read().lower()
                if any(kw in content for kw in ["describe", "mean", "std", "count"]):
                    results["basic_stats_computed"] = True
            except (IOError, UnicodeDecodeError):
                pass

    # Score
    score = 0.0
    if results["data_loaded_successfully"]:
        score += 0.5
    if results["basic_stats_computed"]:
        score += 0.5
    results["score"] = score

    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
