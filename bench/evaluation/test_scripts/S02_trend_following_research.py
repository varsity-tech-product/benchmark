"""Evaluation script for S02: Trend-following alpha research."""

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
        "exploratory_analysis_performed": False,
        "hypothesis_stated": False,
        "signal_formalized": False,
        "signal_evaluated": False,
        "rough_pnl_computed": False,
        "robustness_assessed": False,
        "score": 0.0,
    }

    combined = collect_evidence_text(workspace_path, tool_logs, conversation)
    assistant_text = conversation_text(conversation, role="assistant")

    exploration_groups = [
        ["autocorrelation", "acf", "serial correlation"],
        ["return persistence", "persistence", "momentum regime"],
        ["distribution", "skew", "kurtosis", "histogram"],
        ["descriptive", "summary statistics", "compute_statistics"],
        ["regime", "bull market", "bear market", "sideways"],
    ]
    if count_keyword_groups(combined, exploration_groups) >= 2:
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

    if has_signal_definition(combined) and has_any(
        combined, ["momentum", "breakout", "moving average", "slope", "trend signal"]
    ):
        results["signal_formalized"] = True

    if has_metric_evidence(combined):
        results["signal_evaluated"] = True

    if has_pnl_evidence(combined):
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
    if has_any(assistant_text, robustness_terms):
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
