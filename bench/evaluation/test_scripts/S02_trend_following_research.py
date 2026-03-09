"""Evaluation script for S02: Trend-following alpha research."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source
from _strategy_research_check import (
    collect_artifact_text,
    collect_performance_metric_records,
    collect_signal_evaluation_records,
    count_keyword_groups,
    conversation_text,
    has_any,
    has_metric_numbers,
    has_regex,
    has_signal_definition,
    signal_eval_has_pnl,
    signal_eval_has_quality_metrics,
    tool_called_with_method,
    workspace_has_csv_columns,
)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "exploratory_analysis_performed": False,
        "hypothesis_stated": False,
        "signal_formalized": False,
        "signal_evaluated": False,
        "rough_pnl_computed": False,
        "robustness_assessed": False,
        "signal_artifact_present": False,
        "structured_signal_eval_present": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    assistant_text = conversation_text(conversation, role="assistant")
    signal_eval_records = collect_signal_evaluation_records(workspace_path, tool_logs)
    performance_records = collect_performance_metric_records(workspace_path, tool_logs)

    results["signal_artifact_present"] = (
        workspace_has_csv_columns(workspace_path, ["signal", "close"])
        or has_signal_definition(artifact_text)
        or bool(signal_eval_records)
    )
    results["structured_signal_eval_present"] = any(
        signal_eval_has_quality_metrics(record) for record in signal_eval_records
    )

    exploration_groups = [
        ["autocorrelation", "acf", "serial correlation"],
        ["return persistence", "persistence", "momentum regime"],
        ["distribution", "skew", "kurtosis", "histogram"],
        ["descriptive", "summary statistics", "compute_statistics"],
        ["regime", "bull market", "bear market", "sideways"],
    ]
    exploration_code_patterns = [
        r"\.autocorr\(",
        r"\bacf\(",
        r"\.describe\(",
        r"\.skew\(",
        r"\.kurtosis\(",
        r"\bhist\(",
    ]
    if (
        count_keyword_groups(artifact_text, exploration_groups) >= 2
        or tool_called_with_method(tool_logs, "compute_statistics", ["DESCRIPTIVE", "CORRELATION", "ADF"])
        or has_regex(artifact_text, exploration_code_patterns)
    ):
        results["exploratory_analysis_performed"] = True

    hypothesis_terms = [
        "hypothesis",
        "should work because",
        "might work because",
        "trend-following might work",
        "underreact",
        "information diffusion",
        "trend persistence",
        "behavioral",
    ]
    if has_any(assistant_text, hypothesis_terms):
        results["hypothesis_stated"] = True

    if results["signal_artifact_present"] and has_any(
        artifact_text,
        [
            "momentum",
            "time-series momentum",
            "timeseries momentum",
            "breakout",
            "moving average",
            "slope",
            "trend signal",
            "pct_change(",
        ],
    ):
        results["signal_formalized"] = True

    if results["structured_signal_eval_present"] or has_metric_numbers(
        artifact_text,
        [
            ["ic_mean", "information coefficient", "spearman"],
            ["quantile", "long_short_spread", "quantile_mean_returns"],
            ["turnover", "hit_rate", "signal_autocorrelation"],
        ],
    ):
        results["signal_evaluated"] = True

    if any(signal_eval_has_pnl(record, min_observations=20) for record in signal_eval_records) or performance_records or has_metric_numbers(
        artifact_text,
        [
            ["sharpe", "annualized_sharpe"],
            ["total_return", "annualized return", "annualized_return"],
            ["max_drawdown", "drawdown"],
        ],
    ):
        results["rough_pnl_computed"] = True

    robustness_terms = [
        "robust",
        "subperiod",
        "out-of-sample",
        "regime",
        "bull market",
        "bear market",
        "sideways",
        "stability",
        "degrade",
    ]
    if results["exploratory_analysis_performed"] and has_any(assistant_text, robustness_terms):
        results["robustness_assessed"] = True

    _checklist = [
        {
            "item": "exploratory_analysis_performed",
            "weight": 0.15,
            "passed": results["exploratory_analysis_performed"],
        },
        {"item": "hypothesis_stated", "weight": 0.15, "passed": results["hypothesis_stated"]},
        {"item": "signal_formalized", "weight": 0.20, "passed": results["signal_formalized"]},
        {"item": "signal_evaluated", "weight": 0.20, "passed": results["signal_evaluated"]},
        {"item": "rough_pnl_computed", "weight": 0.15, "passed": results["rough_pnl_computed"]},
        {"item": "robustness_assessed", "weight": 0.15, "passed": results["robustness_assessed"]},
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if not results["signal_artifact_present"]:
        score = min(score, 0.30)
    elif not results["signal_evaluated"]:
        score = min(score, 0.45)

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
