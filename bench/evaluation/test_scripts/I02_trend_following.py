"""Evaluation script for I02: Trend-following strategy on crypto futures universe."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _implementation_check import (
    collect_artifact_text,
    check_csharp_patterns,
    collect_lean_results,
    compute_behavioral_score,
    compute_trade_log_score,
    compute_trial_efficiency,
    has_any,
    has_regex,
    load_agent_trades,
    load_reference_trades,
    match_trades,
)
from _data_source_check import verify_data_source


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate I02 trend-following implementation.

    Checks: backtest completion, trade log produced, behavioral equivalence,
    universe coverage (>=80 symbols), code patterns, and universe summary.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,
        "position_overlap": False,
        "performance_match": False,
        "trade_count_match": False,
        "code_patterns": False,
        "universe_coverage": False,
        "universe_summary_produced": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)

    # ── Backtest completion ──
    lean_results = collect_lean_results(workspace_path)
    if lean_results is not None:
        results["backtest_completed"] = True
    elif has_any(artifact_text, ["algorithm completed", "total trades", "backtest complete"]):
        results["backtest_completed"] = True

    # ── Trade log ──
    agent_trades = load_agent_trades(workspace_path)
    if agent_trades:
        results["trade_log_produced"] = True

    # ── Behavioral scoring ──
    behavioral = compute_behavioral_score("I02", workspace_path, resolution="daily")
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    results["position_overlap"] = behavioral.position_score >= 0.60
    results["performance_match"] = behavioral.performance_score >= 0.50
    results["trade_count_match"] = behavioral.trade_score >= 0.40

    # ── Code patterns ──
    expected_patterns = ["AddCryptoFuture", "SMA(", "SetWarmUp", "Dictionary"]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    results["code_patterns"] = sum(cs_matches.values()) >= 3

    # ── Universe coverage (>=80 symbols subscribed) ──
    symbol_count = 0
    if has_regex(artifact_text, [r"addcryptofuture"]):
        calls = re.findall(r"addcryptofuture\s*\(\s*[\"']?(\w+)", artifact_text)
        symbol_count = len(set(calls))
    if symbol_count >= 80:
        results["universe_coverage"] = True
    elif has_regex(artifact_text, [r"(?:subscribed|added|universe)\D{0,30}\d{2,}"]):
        nums = re.findall(r"(?:subscribed|added|universe)\D{0,30}(\d+)", artifact_text)
        if any(int(n) >= 80 for n in nums):
            results["universe_coverage"] = True

    # ── Universe summary produced ──
    summary_path = os.path.join(workspace_path, "results", "universe_summary.json")
    if os.path.exists(summary_path):
        results["universe_summary_produced"] = True
    elif has_any(artifact_text, ["universe_summary", "universe summary"]):
        results["universe_summary_produced"] = True

    # ── Trial efficiency ──
    efficiency_score = compute_trial_efficiency(workspace_path)

    # ── Scoring ──
    _checklist = [
        {"item": "backtest_completed",      "weight": 0.05, "passed": results["backtest_completed"]},
        {"item": "trade_log_produced",      "weight": 0.05, "passed": results["trade_log_produced"]},
        {"item": "behavioral_score",        "weight": 0.50, "score": behavioral.composite_score},
        {"item": "code_patterns",           "weight": 0.05, "passed": results["code_patterns"]},
        {"item": "universe_coverage",       "weight": 0.15, "passed": results["universe_coverage"]},
        {"item": "universe_summary_produced", "weight": 0.05, "passed": results["universe_summary_produced"]},
        {"item": "trial_efficiency",        "weight": 0.05, "score": efficiency_score},
    ]
    score = sum(
        c["weight"] * c.get("score", 1.0 if c.get("passed") else 0.0)
        for c in _checklist
    )

    # ── Gates ──
    if not results["backtest_completed"]:
        score = min(score, 0.10)
    if not results["trade_log_produced"]:
        score = min(score, 0.15)

    # ── Data source verification ──
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["behavioral_composite"] = round(behavioral.composite_score, 4)
    results["behavioral_layers"] = behavioral.layers_available
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
