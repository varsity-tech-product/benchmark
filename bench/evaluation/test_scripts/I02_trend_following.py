"""Evaluation script for I02: Trend-following strategy on crypto futures universe."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _implementation_check import (
    collect_artifact_text,
    check_csharp_patterns,
    collect_lean_results,
    compute_trade_log_score,
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

    Checks: backtest completion, trade log matching against reference,
    universe coverage (>=80 symbols), code patterns, and universe summary.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "trade_count_match": False,
        "entry_timing_match": False,
        "direction_match": False,
        "exit_timing_match": False,
        "pnl_alignment": False,
        "return_proximity": False,
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

    # ── Trade matching against reference ──
    ref_trades = load_reference_trades("I02")
    if ref_trades and agent_trades:
        match_result = match_trades(ref_trades, agent_trades, time_tolerance_bars=1, resolution="daily")
        results["trade_count_match"] = match_result.count_within_tolerance(0.10)
        results["entry_timing_match"] = match_result.entry_match_rate >= 0.80
        results["direction_match"] = match_result.direction_match_rate >= 0.95
        results["exit_timing_match"] = match_result.exit_match_rate >= 0.70
        results["pnl_alignment"] = match_result.pnl_correlation > 0.85
        results["return_proximity"] = match_result.return_within_tolerance(0.20)

    # ── Code patterns ──
    expected_patterns = ["AddCryptoFuture", "SMA(", "SetWarmUp", "Dictionary"]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    results["code_patterns"] = sum(cs_matches.values()) >= 3

    # ── Universe coverage (>=80 symbols subscribed) ──
    symbol_count = 0
    if has_regex(artifact_text, [r"addcryptofuture"]):
        # Count distinct AddCryptoFuture calls in artifact text
        import re
        calls = re.findall(r"addcryptofuture\s*\(\s*[\"']?(\w+)", artifact_text)
        symbol_count = len(set(calls))
    if symbol_count >= 80:
        results["universe_coverage"] = True
    elif has_regex(artifact_text, [r"(?:subscribed|added|universe)\D{0,30}\d{2,}"]):
        # Fallback: check for log messages indicating >=80 symbols
        import re
        nums = re.findall(r"(?:subscribed|added|universe)\D{0,30}(\d+)", artifact_text)
        if any(int(n) >= 80 for n in nums):
            results["universe_coverage"] = True

    # ── Universe summary produced ──
    summary_path = os.path.join(workspace_path, "results", "universe_summary.json")
    if os.path.exists(summary_path):
        results["universe_summary_produced"] = True
    elif has_any(artifact_text, ["universe_summary", "universe summary"]):
        results["universe_summary_produced"] = True

    # ── Scoring ──
    _checklist = [
        {"item": "backtest_completed", "weight": 0.05, "passed": results["backtest_completed"]},
        {"item": "trade_log_produced", "weight": 0.05, "passed": results["trade_log_produced"]},
        {"item": "trade_count_match", "weight": 0.15, "passed": results["trade_count_match"]},
        {"item": "entry_timing_match", "weight": 0.15, "passed": results["entry_timing_match"]},
        {"item": "direction_match", "weight": 0.10, "passed": results["direction_match"]},
        {"item": "exit_timing_match", "weight": 0.10, "passed": results["exit_timing_match"]},
        {"item": "pnl_alignment", "weight": 0.10, "passed": results["pnl_alignment"]},
        {"item": "return_proximity", "weight": 0.05, "passed": results["return_proximity"]},
        {"item": "code_patterns", "weight": 0.05, "passed": results["code_patterns"]},
        {"item": "universe_coverage", "weight": 0.15, "passed": results["universe_coverage"]},
        {"item": "universe_summary_produced", "weight": 0.05, "passed": results["universe_summary_produced"]},
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

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
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
