"""Evaluation script for S06: Multi-signal combination and alpha synthesis."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.evidence_helpers import apply_data_source_cap
from common.strategy_research_check import (
    collect_artifact_text,
    collect_signal_evaluation_records,
    count_keyword_groups,
    has_any,
    has_metric_numbers,
    has_regex,
    has_signal_definition,
    signal_eval_has_quality_metrics,
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
        "composite_signal_present": False,
        "composite_vs_individual_comparison_present": False,
        "signal_eval_artifact_count": 0,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    signal_eval_records = collect_signal_evaluation_records(workspace_path, tool_logs)
    signal_eval_records = [
        record
        for record in signal_eval_records
        if signal_eval_has_quality_metrics(record)
    ]
    signal_eval_file_count = sum(
        1
        for record in signal_eval_records
        if str(record.get("source", "")).endswith("_signal_evaluation.json")
    )
    results["signal_eval_artifact_count"] = signal_eval_file_count

    signal_groups = [
        ["trend", "momentum", "moving average", "breakout"],
        ["mean reversion", "reversion", "bollinger", "rsi", "z-score"],
        ["cross-asset", "btc/eth", "relative value", "lead-lag", "spread"],
        ["funding", "carry"],
    ]
    if (
        signal_eval_file_count >= 2
        and count_keyword_groups(artifact_text, signal_groups) >= 3
    ):
        results["multiple_signal_sources_present"] = True

    correlation_patterns = [
        r"\.corr\(",
        r"correlation matrix",
        r"pairwise correlation",
        r"heatmap",
    ]
    if has_regex(artifact_text, correlation_patterns):
        results["signal_correlation_analysis_present"] = True

    if has_signal_definition(artifact_text) and has_any(
        artifact_text,
        ["composite signal", "combined signal", "ensemble", "weighted sum"],
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
    if (
        signal_eval_file_count >= 2
        and has_any(artifact_text, comparison_terms)
        and has_metric_numbers(
            artifact_text,
            [
                ["sharpe", "annualized_sharpe"],
                ["ic_mean", "information coefficient", "ic ir"],
                ["total_return", "annualized return", "annualized_return"],
            ],
        )
    ):
        results["composite_vs_individual_comparison_present"] = True

    _checklist = [
        {
            "item": "multiple_signal_sources_present",
            "weight": 0.30,
            "passed": results["multiple_signal_sources_present"],
        },
        {
            "item": "signal_correlation_analysis_present",
            "weight": 0.20,
            "passed": results["signal_correlation_analysis_present"],
        },
        {
            "item": "composite_signal_present",
            "weight": 0.20,
            "passed": results["composite_signal_present"],
        },
        {
            "item": "composite_vs_individual_comparison_present",
            "weight": 0.30,
            "passed": results["composite_vs_individual_comparison_present"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if signal_eval_file_count < 2:
        score = min(score, 0.25)
    elif (
        not results["multiple_signal_sources_present"]
        or not results["composite_signal_present"]
    ):
        score = min(score, 0.35)

    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
