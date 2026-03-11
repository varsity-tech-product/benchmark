"""Evaluation script for S03: Mean-reversion alpha research."""

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
        "reversion_characteristics_identified": False,
        "signal_formalized": False,
        "signal_evaluated": False,
        "rough_pnl_computed": False,
        "signal_artifact_present": False,
        "structured_signal_eval_present": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    signal_eval_records = collect_signal_evaluation_records(workspace_path, tool_logs)
    performance_records = collect_performance_metric_records(workspace_path, tool_logs)

    results["signal_artifact_present"] = (
        workspace_has_csv_columns(workspace_path, ["signal", "close"])
        or has_signal_definition(artifact_text)
        or has_any(
            artifact_text,
            [
                "reversion_signal",
                "alpha_signal",
                "z_score",
                "zscore",
                "bollinger",
                "rsi",
                "mean_reversion",
                "overextended",
            ],
        )
        or bool(signal_eval_records)
    )
    results["structured_signal_eval_present"] = any(
        signal_eval_has_quality_metrics(record) for record in signal_eval_records
    )

    reversion_groups = [
        ["negative autocorrelation", "autocorr", "serial correlation"],
        ["mean reversion", "reversion", "bounce back"],
        ["overextension", "z-score", "bollinger", "rsi"],
    ]
    reversion_code_patterns = [
        r"\bzscore\b",
        r"\bbollinger\b",
        r"\brsi\b",
        r"\.autocorr\(",
    ]
    if (
        count_keyword_groups(artifact_text, reversion_groups) >= 2
        or tool_called_with_method(
            tool_logs, "compute_statistics", ["DESCRIPTIVE", "CORRELATION", "ADF"]
        )
        or has_regex(artifact_text, reversion_code_patterns)
    ):
        results["reversion_characteristics_identified"] = True

    if results["signal_artifact_present"] and has_any(
        artifact_text, ["z-score", "bollinger", "rsi", "reversion signal"]
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

    if (
        any(
            signal_eval_has_pnl(record, min_observations=20)
            for record in signal_eval_records
        )
        or performance_records
        or has_metric_numbers(
            artifact_text,
            [
                ["sharpe", "annualized_sharpe"],
                ["total_return", "annualized return", "annualized_return"],
                ["max_drawdown", "drawdown"],
            ],
        )
    ):
        results["rough_pnl_computed"] = True

    _checklist = [
        {
            "item": "reversion_characteristics_identified",
            "weight": 0.20,
            "passed": results["reversion_characteristics_identified"],
        },
        {
            "item": "signal_formalized",
            "weight": 0.30,
            "passed": results["signal_formalized"],
        },
        {
            "item": "signal_evaluated",
            "weight": 0.30,
            "passed": results["signal_evaluated"],
        },
        {
            "item": "rough_pnl_computed",
            "weight": 0.20,
            "passed": results["rough_pnl_computed"],
        },
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
