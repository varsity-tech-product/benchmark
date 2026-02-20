"""Evaluation script for S01: MA crossover strategy."""

import json
import os
import re


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    """Evaluate whether the agent helped build a valid MA crossover strategy.

    Args:
        workspace_path: Path to the agent's workspace directory.
        tool_logs: List of dicts recording each MCP tool call.
        conversation: List of {role, content} dicts from the conversation.

    Returns:
        Dict with boolean metrics and a float score in [0, 1].
    """
    results = {
        "sharpe_ratio_in_range": False,
        "strategy_uses_two_ma_windows": False,
        "backtest_produces_return": False,
        "score": 0.0,
    }

    # Check workspace for strategy files
    for fname in os.listdir(workspace_path) if os.path.isdir(workspace_path) else []:
        fpath = os.path.join(workspace_path, fname)
        if fname.endswith(".py"):
            try:
                with open(fpath) as f:
                    code = f.read()
                # Check for two distinct MA windows via rolling() calls
                rolling_calls = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", code)
                if len(set(rolling_calls)) >= 2:
                    results["strategy_uses_two_ma_windows"] = True
            except (IOError, UnicodeDecodeError):
                pass

    # Check tool logs for backtest output
    if tool_logs:
        for log in tool_logs:
            output = str(log.get("result", ""))
            # Check for Sharpe ratio in output — use strict patterns first
            sharpe_patterns = [
                r"[Ss]harpe\s*[Rr]atio\s*[:=]\s*(-?\d+\.?\d*)",
                r"[Ss]harpe\s*[:=]\s*(-?\d+\.?\d*)",
                r"[Ss]harpe.*?(-?\d+\.?\d*)",
            ]
            for sp in sharpe_patterns:
                sharpe_match = re.search(sp, output)
                if sharpe_match:
                    try:
                        sharpe = float(sharpe_match.group(1))
                        if -0.5 <= sharpe <= 3.0:
                            results["sharpe_ratio_in_range"] = True
                    except ValueError:
                        pass
                    break
            # Check for return output
            if re.search(r"[Rr]eturn.*?-?\d+\.?\d*%?", output):
                results["backtest_produces_return"] = True

            # Also check shell_exec code content for rolling windows
            if log.get("name") == "shell_exec":
                cmd = str(log.get("input_args", {}).get("command", ""))
                rolling_calls = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", cmd)
                if len(set(rolling_calls)) >= 2:
                    results["strategy_uses_two_ma_windows"] = True

    score = sum(
        [
            0.3 if results["strategy_uses_two_ma_windows"] else 0,
            0.4 if results["sharpe_ratio_in_range"] else 0,
            0.3 if results["backtest_produces_return"] else 0,
        ]
    )
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
