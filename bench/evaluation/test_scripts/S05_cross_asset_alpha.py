"""Evaluation script for S05: Cross-asset alpha research."""

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
        "cross_asset_analysis_present": False,
        "relationship_tested": False,
        "signal_formalized": False,
        "dollar_neutral_evaluation_present": False,
        "signal_evaluated": False,
        "relationship_risk_addressed": False,
        "score": 0.0,
    }

    combined = collect_evidence_text(workspace_path, tool_logs, conversation)
    assistant_text = conversation_text(conversation, role="assistant")

    has_btc = has_any(combined, ["btcusdt", "btc"])
    has_eth = has_any(combined, ["ethusdt", "eth"])
    if has_btc and has_eth and has_any(
        combined, ["spread", "ratio", "lead-lag", "correlation", "cointegration", "relative value"]
    ):
        results["cross_asset_analysis_present"] = True

    if has_any(combined, ["correlation", "cointegration", "lead-lag", "hedge ratio"]):
        results["relationship_tested"] = True

    if has_signal_definition(combined) and has_any(
        combined, ["spread", "ratio", "lead-lag", "relative momentum"]
    ):
        results["signal_formalized"] = True

    if has_any(
        combined,
        ["dollar-neutral", "dollar neutral", "long-short", "long short", "hedge ratio"],
    ):
        results["dollar_neutral_evaluation_present"] = True

    if has_metric_evidence(combined):
        results["signal_evaluated"] = True

    risk_terms = [
        "relationship breakdown",
        "correlation regime",
        "structural change",
        "eth-specific",
        "regime shift",
        "break down",
    ]
    if has_any(assistant_text, risk_terms):
        results["relationship_risk_addressed"] = True

    _checklist = [
        {
            "item": "cross_asset_analysis_present",
            "weight": 0.15,
            "passed": results["cross_asset_analysis_present"],
        },
        {"item": "relationship_tested", "weight": 0.20, "passed": results["relationship_tested"]},
        {"item": "signal_formalized", "weight": 0.15, "passed": results["signal_formalized"]},
        {
            "item": "dollar_neutral_evaluation_present",
            "weight": 0.15,
            "passed": results["dollar_neutral_evaluation_present"],
        },
        {"item": "signal_evaluated", "weight": 0.20, "passed": results["signal_evaluated"]},
        {
            "item": "relationship_risk_addressed",
            "weight": 0.15,
            "passed": results["relationship_risk_addressed"],
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
