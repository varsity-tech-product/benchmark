"""Evaluation script for S01: MA crossover strategy."""

import json
import os
import re


def evaluate(
    workspace_path: str, tool_logs: list = None, conversation: list = None
) -> dict:
    """Evaluate whether the agent helped build a valid MA crossover strategy.

    QR checks: Does the workspace/tool output contain two MA windows,
    Sharpe ratio in range, and return metrics?
    Does NOT check which tools were used.
    """
    results = {
        "sharpe_ratio_in_range": False,
        "strategy_uses_two_ma_windows": False,
        "backtest_produces_return": False,
        "score": 0.0,
    }

    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # --- Source 1: Check workspace files ---

    # Check .py files for rolling() patterns
    for fname in workspace_files:
        fpath = os.path.join(workspace_path, fname)
        if fname.endswith(".py"):
            try:
                with open(fpath) as f:
                    code = f.read()
                rolling_calls = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", code)
                if len(set(rolling_calls)) >= 2:
                    results["strategy_uses_two_ma_windows"] = True
            except (IOError, UnicodeDecodeError):
                pass

    # Check backtest_analysis.json (produced by analyze_backtest_results tool)
    analysis_path = os.path.join(workspace_path, "backtest_analysis.json")
    if os.path.exists(analysis_path):
        try:
            with open(analysis_path) as f:
                analysis = json.load(f)
            sharpe = analysis.get("sharpe_ratio")
            if sharpe is not None and -0.5 <= float(sharpe) <= 3.0:
                results["sharpe_ratio_in_range"] = True
            if (
                analysis.get("annual_return") is not None
                or analysis.get("total_return") is not None
            ):
                results["backtest_produces_return"] = True
        except (json.JSONDecodeError, IOError, ValueError):
            pass

    # Check other workspace files for metric values
    for fname in workspace_files:
        fpath = os.path.join(workspace_path, fname)
        if (
            fname.endswith((".json", ".txt", ".csv", ".log"))
            and fname != "backtest_analysis.json"
        ):
            try:
                with open(fpath) as f:
                    content = f.read()
                _check_metrics_in_text(content, results)
            except (IOError, UnicodeDecodeError):
                pass

    # --- Source 2: Scan ALL tool logs (tool-name agnostic) ---
    if tool_logs:
        for log in tool_logs:
            # Check result values
            output = str(log.get("result", ""))
            _check_metrics_in_text(output, results)

            # Check all input_args for code artifacts
            for key, value in log.get("input_args", {}).items():
                text = str(value)
                rolling_calls = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", text)
                if len(set(rolling_calls)) >= 2:
                    results["strategy_uses_two_ma_windows"] = True
                _check_metrics_in_text(text, results)

    score = sum(
        [
            0.30 if results["strategy_uses_two_ma_windows"] else 0,
            0.40 if results["sharpe_ratio_in_range"] else 0,
            0.30 if results["backtest_produces_return"] else 0,
        ]
    )
    results["score"] = round(score, 2)
    return results


def _check_metrics_in_text(text: str, results: dict):
    """Check a text blob for Sharpe and Return values. Mutates results dict."""
    sharpe_patterns = [
        r"[Ss]harpe\s*[Rr]atio\s*[:=]\s*(-?\d+\.?\d*)",
        r"[Ss]harpe\s*[:=]\s*(-?\d+\.?\d*)",
        r"[Ss]harpe.*?(-?\d+\.?\d*)",
    ]
    if not results["sharpe_ratio_in_range"]:
        for sp in sharpe_patterns:
            match = re.search(sp, text)
            if match:
                try:
                    sharpe = float(match.group(1))
                    if -0.5 <= sharpe <= 3.0:
                        results["sharpe_ratio_in_range"] = True
                except ValueError:
                    pass
                break
    if not results["backtest_produces_return"]:
        if re.search(r"[Rr]eturn.*?-?\d+\.?\d*%?", text):
            results["backtest_produces_return"] = True
        if re.search(r"[Dd]rawdown.*?-?\d+\.?\d*%?", text):
            results["backtest_produces_return"] = True


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
