"""Evaluation script for S05: Cross-asset alpha research."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.data_source_check import verify_data_source
from common.strategy_research_check import (
    collect_artifact_text,
    collect_performance_metric_records,
    collect_signal_evaluation_records,
    has_any,
    has_metric_numbers,
    has_regex,
    has_signal_definition,
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
        "cross_asset_analysis_present": False,
        "relationship_tested": False,
        "signal_formalized": False,
        "dollar_neutral_evaluation_present": False,
        "signal_evaluated": False,
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
                "cross_asset_signal",
                "alpha_signal",
                "spread",
                "hedge_ratio",
                "relative_value",
                "cointegration",
                "lead_lag",
            ],
        )
        or bool(signal_eval_records)
    )
    results["structured_signal_eval_present"] = any(
        signal_eval_has_quality_metrics(record) for record in signal_eval_records
    )

    relationship_patterns = [
        r"\bcoint\(",
        r"\.corr\(",
        r"\bhedge[_ ]ratio\b",
        r"\blead[\-_ ]lag\b",
        r"\brolling\([^)]*\)\.corr\(",
    ]
    if tool_called_with_method(
        tool_logs, "compute_statistics", ["CORRELATION", "COINTEGRATION"]
    ) or has_regex(artifact_text, relationship_patterns):
        results["relationship_tested"] = True

    has_btc = has_any(artifact_text, ["btcusdt", "btc"])
    has_eth = has_any(artifact_text, ["ethusdt", "eth/", "eth_"])
    if has_btc and has_eth and results["relationship_tested"]:
        results["cross_asset_analysis_present"] = True

    if results["signal_artifact_present"] and has_any(
        artifact_text,
        ["spread", "ratio", "lead-lag", "relative momentum", "hedge ratio"],
    ):
        results["signal_formalized"] = True

    dollar_neutral_terms = [
        "dollar-neutral",
        "dollar neutral",
        "long-short",
        "long short",
        "hedge ratio",
    ]
    if has_any(artifact_text, dollar_neutral_terms) and (
        results["structured_signal_eval_present"]
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
        results["dollar_neutral_evaluation_present"] = True

    if (
        results["structured_signal_eval_present"]
        or performance_records
        or has_metric_numbers(
            artifact_text,
            [
                ["ic_mean", "information coefficient", "spearman"],
                ["quantile", "long_short_spread", "quantile_mean_returns"],
                ["sharpe", "annualized_sharpe"],
            ],
        )
    ):
        results["signal_evaluated"] = True

    _checklist = [
        {
            "item": "cross_asset_analysis_present",
            "weight": 0.18,
            "passed": results["cross_asset_analysis_present"],
        },
        {
            "item": "relationship_tested",
            "weight": 0.23,
            "passed": results["relationship_tested"],
        },
        {
            "item": "signal_formalized",
            "weight": 0.18,
            "passed": results["signal_formalized"],
        },
        {
            "item": "dollar_neutral_evaluation_present",
            "weight": 0.18,
            "passed": results["dollar_neutral_evaluation_present"],
        },
        {
            "item": "signal_evaluated",
            "weight": 0.23,
            "passed": results["signal_evaluated"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if not results["relationship_tested"] or not results["signal_evaluated"]:
        score = min(score, 0.25)
    elif not results["dollar_neutral_evaluation_present"]:
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
