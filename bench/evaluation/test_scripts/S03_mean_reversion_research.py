"""Evaluation script for S03: Mean-reversion alpha research."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source
from _strategy_research_check import (
    collect_evidence_text,
    count_keyword_groups,
    conversation_text,
    has_any,
    has_metric_evidence,
    has_pnl_evidence,
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
        "reversion_characteristics_identified": False,
        "hypothesis_stated": False,
        "signal_formalized": False,
        "signal_evaluated": False,
        "rough_pnl_computed": False,
        "failure_mode_analysis_present": False,
        "score": 0.0,
    }

    combined = collect_evidence_text(workspace_path, tool_logs, conversation)
    assistant_text = conversation_text(conversation, role="assistant")

    reversion_terms = [
        "negative autocorrelation",
        "mean reversion",
        "reversion",
        "overextension",
        "z-score",
        "bollinger",
        "rsi",
        "bounce back",
    ]
    if has_any(combined, reversion_terms):
        results["reversion_characteristics_identified"] = True

    hypothesis_groups = [
        ["hypothesis", "should work because", "occurs because"],
        ["mean reversion", "reversion", "bounce back"],
        ["overreaction", "liquidity provision", "technical levels", "overshoot"],
    ]
    if count_keyword_groups(assistant_text, hypothesis_groups) >= 2:
        results["hypothesis_stated"] = True

    if has_signal_definition(combined) and has_any(
        combined, ["z-score", "bollinger", "rsi", "reversion signal"]
    ):
        results["signal_formalized"] = True

    if has_metric_evidence(combined):
        results["signal_evaluated"] = True

    if has_pnl_evidence(combined):
        results["rough_pnl_computed"] = True

    failure_terms = [
        "strong trend",
        "trending market",
        "fails when",
        "failure mode",
        "fat-tail",
        "blow up",
        "regime-dependent",
        "regime dependent",
    ]
    if has_any(assistant_text, failure_terms):
        results["failure_mode_analysis_present"] = True

    _checklist = [
        {
            "item": "reversion_characteristics_identified",
            "weight": 0.15,
            "passed": results["reversion_characteristics_identified"],
        },
        {"item": "hypothesis_stated", "weight": 0.15, "passed": results["hypothesis_stated"]},
        {"item": "signal_formalized", "weight": 0.20, "passed": results["signal_formalized"]},
        {"item": "signal_evaluated", "weight": 0.20, "passed": results["signal_evaluated"]},
        {"item": "rough_pnl_computed", "weight": 0.15, "passed": results["rough_pnl_computed"]},
        {
            "item": "failure_mode_analysis_present",
            "weight": 0.15,
            "passed": results["failure_mode_analysis_present"],
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
