"""Evaluation script for S04: Volume and microstructure alpha research."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source
from _strategy_research_check import (
    collect_evidence_text,
    conversation_text,
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
        "non_price_features_used": False,
        "feature_engineering_present": False,
        "cross_timeframe_analysis_present": False,
        "signal_formalized": False,
        "signal_evaluated": False,
        "decay_or_tradability_discussed": False,
        "score": 0.0,
    }

    combined = collect_evidence_text(workspace_path, tool_logs, conversation)
    assistant_text = conversation_text(conversation, role="assistant")

    non_price_terms = [
        "quote_volume",
        "trade_count",
        "taker_buy",
        "taker buy",
        "imbalance",
        "volume ratio",
        "volume z-score",
    ]
    if has_any(combined, non_price_terms) and has_any(
        combined, ["feature", "signal", "ratio", "z-score", "normalized"]
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
    if has_any(combined, feature_terms):
        results["feature_engineering_present"] = True

    timeframe_groups = [
        ["5m", "5-minute"],
        ["1h", "hourly"],
        ["daily", "1d"],
    ]
    cross_tf_link_terms = [
        "cross-timeframe",
        "higher timeframe",
        "lower timeframe",
        "predict",
        "predicts",
        "lead",
        "propagate",
        "from 5m to 1h",
        "to daily return",
    ]
    timeframe_group_hits = sum(1 for group in timeframe_groups if has_any(combined, group))
    if timeframe_group_hits >= 2 and has_any(combined, cross_tf_link_terms):
        results["cross_timeframe_analysis_present"] = True

    if has_signal_definition(combined) and has_any(combined, non_price_terms):
        results["signal_formalized"] = True

    if has_metric_evidence(combined):
        results["signal_evaluated"] = True

    practical_terms = [
        "decay",
        "decays fast",
        "latency",
        "capacity",
        "tradeable",
        "tradable",
        "slippage",
        "fees",
    ]
    if has_any(assistant_text, practical_terms):
        results["decay_or_tradability_discussed"] = True

    _checklist = [
        {"item": "non_price_features_used", "weight": 0.15, "passed": results["non_price_features_used"]},
        {
            "item": "feature_engineering_present",
            "weight": 0.20,
            "passed": results["feature_engineering_present"],
        },
        {
            "item": "cross_timeframe_analysis_present",
            "weight": 0.15,
            "passed": results["cross_timeframe_analysis_present"],
        },
        {"item": "signal_formalized", "weight": 0.15, "passed": results["signal_formalized"]},
        {"item": "signal_evaluated", "weight": 0.20, "passed": results["signal_evaluated"]},
        {
            "item": "decay_or_tradability_discussed",
            "weight": 0.15,
            "passed": results["decay_or_tradability_discussed"],
        },
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
