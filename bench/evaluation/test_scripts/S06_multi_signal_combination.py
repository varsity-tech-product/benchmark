"""Evaluation script for S06: Multi-signal combination and alpha synthesis."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source
from _strategy_research_check import (
    collect_evidence_text,
    conversation_text,
    count_keyword_groups,
    has_any,
    has_metric_evidence,
    has_signal_definition,
)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "multiple_signal_sources_present": False,
        "signal_correlation_analysis_present": False,
        "combination_method_stated": False,
        "composite_signal_present": False,
        "composite_vs_individual_comparison_present": False,
        "limitations_discussed": False,
        "score": 0.0,
    }

    combined = collect_evidence_text(workspace_path, tool_logs, conversation)
    assistant_text = conversation_text(conversation, role="assistant")

    signal_groups = [
        ["trend", "momentum", "moving average", "breakout"],
        ["mean reversion", "reversion", "bollinger", "rsi", "z-score"],
        ["cross-asset", "btc/eth", "relative value", "lead-lag", "spread"],
        ["funding", "carry"],
    ]
    if count_keyword_groups(combined, signal_groups) >= 3:
        results["multiple_signal_sources_present"] = True

    if has_any(
        combined,
        ["correlation matrix", ".corr()", "signal correlation", "heatmap", "pairwise correlation"],
    ):
        results["signal_correlation_analysis_present"] = True

    if has_any(
        assistant_text,
        ["equal weight", "equal-weight", "ic-weighted", "regression", "weighted combination"],
    ):
        results["combination_method_stated"] = True

    if has_signal_definition(combined) and has_any(
        combined, ["composite signal", "combined signal", "ensemble", "weighted sum"]
    ):
        results["composite_signal_present"] = True

    comparison_terms = [
        "composite",
        "individual signal",
        "best single signal",
        "higher sharpe",
        "improved ic",
        "ic ir",
    ]
    if has_any(combined, comparison_terms) and has_metric_evidence(combined):
        results["composite_vs_individual_comparison_present"] = True

    limitation_terms = [
        "correlated drawdowns",
        "all signals correlated",
        "regime shift",
        "crisis",
        "diversification fails",
        "tail correlation",
    ]
    if has_any(assistant_text, limitation_terms):
        results["limitations_discussed"] = True

    _checklist = [
        {
            "item": "multiple_signal_sources_present",
            "weight": 0.20,
            "passed": results["multiple_signal_sources_present"],
        },
        {
            "item": "signal_correlation_analysis_present",
            "weight": 0.15,
            "passed": results["signal_correlation_analysis_present"],
        },
        {
            "item": "combination_method_stated",
            "weight": 0.15,
            "passed": results["combination_method_stated"],
        },
        {
            "item": "composite_signal_present",
            "weight": 0.15,
            "passed": results["composite_signal_present"],
        },
        {
            "item": "composite_vs_individual_comparison_present",
            "weight": 0.20,
            "passed": results["composite_vs_individual_comparison_present"],
        },
        {"item": "limitations_discussed", "weight": 0.15, "passed": results["limitations_discussed"]},
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

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
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
