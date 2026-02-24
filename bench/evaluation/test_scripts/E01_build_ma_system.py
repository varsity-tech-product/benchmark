"""Evaluation script for E01: Build complete MA crossover system from scratch."""

import json
import os
import re


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    """Evaluate whether a complete MA crossover system was built.

    QR checks: Are signals, metrics, modular code, and visualizations present?
    """
    results = {
        "system_produces_signals": False,
        "backtest_produces_metrics": False,
        "code_is_modular": False,
        "visualization_produced": False,
        "score": 0.0,
    }

    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # --- Workspace artifacts ---

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

    # Modularity check
    if len(py_files) >= 2:
        results["code_is_modular"] = True
    elif len(py_files) == 1:
        _, code = py_files[0]
        func_defs = re.findall(r"^def \w+\(", code, re.MULTILINE)
        if len(func_defs) >= 3:
            results["code_is_modular"] = True

    # Signal patterns in workspace code
    all_code = " ".join(code for _, code in py_files)
    signal_patterns = [
        r"(cross|signal|buy|sell).*?(sma|ma|moving)",
        r"(sma|ma|moving).*?(cross|signal|buy|sell)",
        r"rolling\(.*?\).*?rolling\(",
        r"Signal",
    ]
    if any(re.search(p, all_code, re.IGNORECASE) for p in signal_patterns):
        results["system_produces_signals"] = True

    # Visualization files
    for fname in workspace_files:
        if fname.endswith((".png", ".jpg", ".svg", ".pdf")):
            results["visualization_produced"] = True
            break

    # backtest_analysis.json
    analysis_path = os.path.join(workspace_path, "backtest_analysis.json")
    if os.path.exists(analysis_path):
        try:
            with open(analysis_path) as f:
                analysis = json.load(f)
            if any(
                k in analysis for k in ["sharpe_ratio", "annual_return", "max_drawdown"]
            ):
                results["backtest_produces_metrics"] = True
        except (json.JSONDecodeError, IOError):
            pass

    # --- Tool logs (tool-name agnostic) ---
    if tool_logs:
        for log in tool_logs:
            output = str(log.get("result", ""))

            # Metric values in output
            if re.search(r"[Ss]harpe.*?-?\d+\.?\d*", output):
                results["backtest_produces_metrics"] = True
            if re.search(r"[Rr]eturn.*?-?\d+\.?\d*%?", output):
                results["backtest_produces_metrics"] = True
            if re.search(r"[Dd]rawdown.*?-?\d+\.?\d*%?", output):
                results["backtest_produces_metrics"] = True

            # Chart saved
            if "chart saved" in output.lower() or "chart_" in output.lower():
                results["visualization_produced"] = True

            # Signal evidence in output
            if re.search(r"(signal|crossover|buy|sell)", output, re.IGNORECASE):
                results["system_produces_signals"] = True

            # Check input_args for code artifacts
            for key, value in log.get("input_args", {}).items():
                text = str(value)
                if any(re.search(p, text, re.IGNORECASE) for p in signal_patterns):
                    results["system_produces_signals"] = True
                rolling_calls = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", text)
                if len(set(rolling_calls)) >= 2:
                    results["system_produces_signals"] = True

            # Indicator computation counts as signal evidence
            for key, value in log.get("input_args", {}).items():
                if str(value).upper() in ("SMA", "EMA", "MACD"):
                    if log.get("success", False):
                        results["system_produces_signals"] = True

    score = sum(
        [
            0.25 if results["system_produces_signals"] else 0,
            0.30 if results["backtest_produces_metrics"] else 0,
            0.25 if results["code_is_modular"] else 0,
            0.20 if results["visualization_produced"] else 0,
        ]
    )
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
