"""Evaluation script for E01: Build complete MA crossover system from scratch."""

import json
import os
import re


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    """Evaluate whether the agent helped build a complete MA crossover system.

    A complete system includes: data loading, indicator computation, signal
    generation, backtesting, and result reporting. This is an end-to-end task,
    so we check for all components.

    Args:
        workspace_path: Path to the agent's workspace directory.
        tool_logs: List of dicts recording each MCP tool call.
        conversation: List of {role, content} dicts from the conversation.

    Returns:
        Dict with boolean metrics and a float score in [0, 1].
    """
    results = {
        "system_produces_signals": False,
        "backtest_produces_metrics": False,
        "sharpe_ratio_in_range": False,
        "code_is_modular": False,
        "visualization_produced": False,
        "score": 0.0,
    }

    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # Check workspace for strategy files
    py_files = []
    for fname in workspace_files:
        fpath = os.path.join(workspace_path, fname)
        if fname.endswith(".py"):
            try:
                with open(fpath) as f:
                    code = f.read()
                py_files.append((fname, code))
            except (IOError, UnicodeDecodeError):
                pass

    # Check for modularity — either multiple .py files or defined functions
    if len(py_files) >= 2:
        results["code_is_modular"] = True
    elif len(py_files) == 1:
        _, code = py_files[0]
        # Count function definitions as a proxy for modularity
        func_defs = re.findall(r"^def \w+\(", code, re.MULTILINE)
        if len(func_defs) >= 3:
            results["code_is_modular"] = True

    # Check all code for signal generation and backtesting components
    all_code = " ".join(code for _, code in py_files)

    # Signal generation: look for crossover logic
    signal_patterns = [
        r"(cross|signal|buy|sell).*?(sma|ma|moving)",
        r"(sma|ma|moving).*?(cross|signal|buy|sell)",
        r"rolling\(.*?\).*?rolling\(",
        r"Signal",
    ]
    if any(re.search(p, all_code, re.IGNORECASE) for p in signal_patterns):
        results["system_produces_signals"] = True

    # Check for visualization files
    for fname in workspace_files:
        if fname.endswith((".png", ".jpg", ".svg", ".pdf")):
            results["visualization_produced"] = True
            break

    # Check tool logs
    if tool_logs:
        for log in tool_logs:
            output = str(log.get("result", ""))

            # Check for Sharpe ratio in output
            sharpe_match = re.search(r"[Ss]harpe.*?(-?\d+\.?\d*)", output)
            if sharpe_match:
                try:
                    sharpe = float(sharpe_match.group(1))
                    if -1.0 <= sharpe <= 4.0:
                        results["sharpe_ratio_in_range"] = True
                    results["backtest_produces_metrics"] = True
                except ValueError:
                    pass

            # Check for return metrics
            if re.search(r"[Rr]eturn.*?-?\d+\.?\d*%?", output):
                results["backtest_produces_metrics"] = True

            # Check for drawdown metrics
            if re.search(r"[Dd]rawdown.*?-?\d+\.?\d*%?", output):
                results["backtest_produces_metrics"] = True

            # Check for plot_chart calls
            if log.get("name") == "plot_chart":
                if log.get("success", False):
                    results["visualization_produced"] = True

            # Check for signals in output
            if log.get("name") == "shell_exec":
                if re.search(r"(signal|crossover|buy|sell)", output, re.IGNORECASE):
                    results["system_produces_signals"] = True

    score = sum(
        [
            0.20 if results["system_produces_signals"] else 0,
            0.25 if results["backtest_produces_metrics"] else 0,
            0.20 if results["sharpe_ratio_in_range"] else 0,
            0.20 if results["code_is_modular"] else 0,
            0.15 if results["visualization_produced"] else 0,
        ]
    )
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
