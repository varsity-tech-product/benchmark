"""Evaluation script for S01: MA crossover strategy."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.data_source_check import verify_data_source


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
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
        "visualization_present": False,
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

    # Check *_analysis.json and *_metrics.json workspace files
    # (produced by analyze_backtest_results / run_backtest tools)
    _METRIC_SUFFIXES = ("_analysis.json", "_metrics.json")
    for fname in workspace_files:
        if any(fname.endswith(s) for s in _METRIC_SUFFIXES):
            fpath = os.path.join(workspace_path, fname)
            try:
                with open(fpath) as f:
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

    # Check other workspace files for metric values (text-based fallback)
    for fname in workspace_files:
        if any(fname.endswith(s) for s in _METRIC_SUFFIXES):
            continue
        fpath = os.path.join(workspace_path, fname)
        if fname.endswith((".json", ".txt", ".csv", ".log")):
            try:
                with open(fpath) as f:
                    content = f.read()
                _check_metrics_in_text(content, results)
            except (IOError, UnicodeDecodeError):
                pass

    # Check workspace for visualization artifacts
    for fname in workspace_files:
        if fname.endswith((".png", ".jpg", ".jpeg", ".svg")):
            results["visualization_present"] = True
            break

    # --- Source 2: Scan ALL tool logs (tool-name agnostic) ---
    if tool_logs:
        _indicator_windows: set[int] = set()
        for log in tool_logs:
            # Check result values
            output = str(log.result or "")
            _check_metrics_in_text(output, results)

            # Check all args for code artifacts
            for key, value in log.args.items():
                text = str(value)
                rolling_calls = re.findall(r"rolling\((?:window\s*=\s*)?(\d+)\)", text)
                if len(set(rolling_calls)) >= 2:
                    results["strategy_uses_two_ma_windows"] = True
                _check_metrics_in_text(text, results)

            # Detect MA windows from compute_indicator tool
            if log.name == "compute_indicator":
                indicator = str(log.args.get("indicator", "")).upper()
                if indicator in ("SMA", "EMA"):
                    params = log.args.get("params") or {}
                    if isinstance(params, dict):
                        window = params.get("window")
                        if window is not None:
                            _indicator_windows.add(int(window))

            # Detect MA windows from run_backtest tool
            if log.name == "run_backtest":
                strategy = str(log.args.get("strategy", "")).lower()
                if strategy == "ma_crossover":
                    sp = log.args.get("strategy_params") or {}
                    if (
                        isinstance(sp, dict)
                        and sp.get("short_window")
                        and sp.get("long_window")
                    ):
                        results["strategy_uses_two_ma_windows"] = True

            # Detect visualization from tool calls
            if log.name == "plot_chart":
                results["visualization_present"] = True
            if log.name == "shell_exec":
                cmd = str(log.args.get("command", ""))
                if re.search(r"(?:matplotlib|plt\.savefig|\.plot\()", cmd):
                    results["visualization_present"] = True

        if len(_indicator_windows) >= 2:
            results["strategy_uses_two_ma_windows"] = True

    _checklist = [
        {
            "item": "strategy_uses_two_ma_windows",
            "weight": 0.25,
            "passed": results["strategy_uses_two_ma_windows"],
        },
        {
            "item": "sharpe_ratio_in_range",
            "weight": 0.35,
            "passed": results["sharpe_ratio_in_range"],
        },
        {
            "item": "backtest_produces_return",
            "weight": 0.25,
            "passed": results["backtest_produces_return"],
        },
        {
            "item": "visualization_present",
            "weight": 0.15,
            "passed": results["visualization_present"],
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
