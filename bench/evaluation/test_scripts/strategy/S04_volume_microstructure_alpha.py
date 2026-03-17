"""Evaluation script for S04: Volume and microstructure alpha research."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.data_source_check import verify_data_source
from common.strategy_research_check import (
    collect_artifact_text,
    collect_signal_evaluation_records,
    count_keyword_groups,
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
        "non_price_features_used": False,
        "feature_engineering_present": False,
        "cross_timeframe_analysis_present": False,
        "signal_formalized": False,
        "signal_evaluated": False,
        "signal_artifact_present": False,
        "structured_signal_eval_present": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    signal_eval_records = collect_signal_evaluation_records(workspace_path, tool_logs)

    results["signal_artifact_present"] = (
        workspace_has_csv_columns(workspace_path, ["signal", "close"])
        or has_signal_definition(artifact_text)
        or has_any(
            artifact_text,
            [
                "microstructure_signal",
                "alpha_signal",
                "imbalance",
                "volume_ratio",
                "taker_buy",
                "order_flow",
                "vpin",
            ],
        )
        or bool(signal_eval_records)
    )
    results["structured_signal_eval_present"] = any(
        signal_eval_has_quality_metrics(record) for record in signal_eval_records
    )

    non_price_terms = [
        "quote_volume",
        "trade_count",
        "taker_buy",
        "taker buy",
        "imbalance",
        "volume ratio",
        "volume z-score",
    ]
    feature_assignment_patterns = [
        r"df\[[\"'][^\"']+(?:ratio|zscore|z_score|imbalance|normalized|spike)[^\"']*[\"']\]\s*=",
        r"df\[[\"'][^\"']+[\"']\]\s*=.*(?:quote_volume|trade_count|taker_buy)",
        r"\b(?:imbalance|volume_ratio|volume_zscore|normalized_trade_count|volume_spike)\s*=",
    ]
    if has_any(artifact_text, non_price_terms) and (
        has_regex(artifact_text, feature_assignment_patterns)
        or has_any(
            artifact_text, ["feature", "signal", "ratio", "z-score", "normalized"]
        )
    ):
        results["non_price_features_used"] = True

    feature_terms = [
        "feature engineering",
        "engineer",
        "ratio",
        "imbalance",
        "z-score",
        "normalized trade count",
        "volume spike",
    ]
    if has_regex(artifact_text, feature_assignment_patterns) or has_any(
        artifact_text, feature_terms
    ):
        results["feature_engineering_present"] = True

    timeframe_groups = [
        ["5m", "5-minute"],
        ["1h", "hourly"],
        ["daily", "1d"],
    ]
    cross_tf_link_groups = [
        ["cross-timeframe", "higher timeframe", "lower timeframe"],
        ["predict", "predicts", "lead", "propagate"],
        ["resample", "merge", "merge_asof", "groupby", "align", "shift"],
    ]
    timeframe_group_hits = sum(
        1 for group in timeframe_groups if has_any(artifact_text, group)
    )
    if (
        timeframe_group_hits >= 2
        and count_keyword_groups(artifact_text, cross_tf_link_groups) >= 2
    ) or tool_called_with_method(tool_logs, "compute_statistics", ["CORRELATION"]):
        results["cross_timeframe_analysis_present"] = True

    if results["signal_artifact_present"] and results["non_price_features_used"]:
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

    _checklist = [
        {
            "item": "non_price_features_used",
            "weight": 0.18,
            "passed": results["non_price_features_used"],
        },
        {
            "item": "feature_engineering_present",
            "weight": 0.23,
            "passed": results["feature_engineering_present"],
        },
        {
            "item": "cross_timeframe_analysis_present",
            "weight": 0.18,
            "passed": results["cross_timeframe_analysis_present"],
        },
        {
            "item": "signal_formalized",
            "weight": 0.18,
            "passed": results["signal_formalized"],
        },
        {
            "item": "signal_evaluated",
            "weight": 0.23,
            "passed": results["signal_evaluated"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if not results["signal_formalized"] or not results["signal_evaluated"]:
        score = min(score, 0.25)
    elif not results["cross_timeframe_analysis_present"]:
        score = min(score, 0.50)

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
