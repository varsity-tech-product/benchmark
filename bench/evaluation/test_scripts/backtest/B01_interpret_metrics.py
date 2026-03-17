"""Evaluation script for B01: Interpret basic backtest metrics."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.backtest_engine_check import (
    collect_artifact_text,
    conversation_text,
    has_any,
)
from common.data_source_check import verify_data_source


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate whether backtest metrics are present and explained.

    QR checks:
    1. Backtest was actually run or loaded (tool evidence)
    2. Sharpe ratio present in output
    3. Return/PnL present in output
    4. Drawdown present in output
    5. Interpretation/assessment present in conversation
    """
    results = {
        "backtest_executed": False,
        "sharpe_present": False,
        "return_present": False,
        "drawdown_present": False,
        "interpretation_present": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    assistant_text = conversation_text(conversation, role="assistant")

    # Check if a backtest was actually run (tool evidence)
    if tool_logs:
        for log in tool_logs:
            name = log.name or ""
            if name in ("run_backtest", "analyze_backtest_results"):
                results["backtest_executed"] = True
                break
            if name == "shell_exec":
                args_text = str(log.args.get("command", "")).lower()
                result_text = str(log.result or "").lower()
                # Running Python scripts that compute metrics
                if any(
                    kw in args_text
                    for kw in ["backtest", "sharpe", "drawdown", "return"]
                ):
                    results["backtest_executed"] = True
                    break
                # Check result for metric output
                if re.search(r"sharpe.*-?\d+\.?\d*|return.*-?\d+\.?\d*", result_text):
                    results["backtest_executed"] = True
                    break

    # Helper: check text for metric keyword + nearby number
    def _has_metric(text: str, keywords: list[str]) -> bool:
        for kw in keywords:
            if re.search(
                rf"{re.escape(kw)}[^0-9\-]{{0,60}}-?\d+\.?\d*",
                text,
                re.IGNORECASE,
            ):
                return True
            # Also check number-first patterns: "1.85 (Sharpe)"
            if re.search(
                rf"-?\d+\.?\d*[^0-9]{{0,60}}{re.escape(kw)}",
                text,
                re.IGNORECASE,
            ):
                return True
        return False

    combined = artifact_text + "\n" + assistant_text

    results["sharpe_present"] = _has_metric(
        combined, ["sharpe", "sharpe_ratio", "sharpe ratio"]
    )
    results["return_present"] = _has_metric(
        combined,
        [
            "total return",
            "annual return",
            "annualized return",
            "total_return",
            "annual_return",
            "net profit",
            "cumulative return",
            "cagr",
        ],
    )
    results["drawdown_present"] = _has_metric(
        combined, ["drawdown", "max_drawdown", "max drawdown", "maximum drawdown"]
    )

    # Interpretation: did the tutor explain what metrics mean or assess strategy?
    interpretation_keywords = [
        "good",
        "bad",
        "strong",
        "weak",
        "promising",
        "problematic",
        "overfitting",
        "overfit",
        "risk-adjusted",
        "risk adjusted",
        "benchmark",
        "rule of thumb",
        "above 1",
        "above 2",
        "greater than",
        "means that",
        "indicates",
        "suggests",
        "tells us",
        "interpret",
        "measures",
        "represents",
    ]
    results["interpretation_present"] = has_any(assistant_text, interpretation_keywords)

    _checklist = [
        {
            "item": "backtest_executed",
            "weight": 0.20,
            "passed": results["backtest_executed"],
        },
        {"item": "sharpe_present", "weight": 0.20, "passed": results["sharpe_present"]},
        {"item": "return_present", "weight": 0.20, "passed": results["return_present"]},
        {
            "item": "drawdown_present",
            "weight": 0.20,
            "passed": results["drawdown_present"],
        },
        {
            "item": "interpretation_present",
            "weight": 0.20,
            "passed": results["interpretation_present"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # Hard cap: if no metrics at all, cap at 0.20
    metrics_present = sum(
        1
        for k in ("sharpe_present", "return_present", "drawdown_present")
        if results[k]
    )
    if metrics_present == 0:
        score = min(score, 0.20)

    # Data source verification
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
