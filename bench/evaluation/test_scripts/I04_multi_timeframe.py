"""Evaluation script for I04: Multi-timeframe strategy with consolidators."""

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
    """Evaluate I04 multi-timeframe implementation.

    Checks: backtest completion, trade log matching against reference,
    consolidator usage, dual-resolution indicators (EMA on 4h, RSI on 1h),
    code patterns.
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
        "consolidator_used": False,
        "dual_resolution_indicators": False,
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
    ref_trades = load_reference_trades("I04")
    if ref_trades and agent_trades:
        match_result = match_trades(ref_trades, agent_trades, time_tolerance_bars=1, resolution="hour")
        results["trade_count_match"] = match_result.count_within_tolerance(0.10)
        results["entry_timing_match"] = match_result.entry_match_rate >= 0.80
        results["direction_match"] = match_result.direction_match_rate >= 0.95
        results["exit_timing_match"] = match_result.exit_match_rate >= 0.70
        results["pnl_alignment"] = match_result.pnl_correlation > 0.85
        results["return_proximity"] = match_result.return_within_tolerance(0.20)

    # ── Code patterns ──
    expected_patterns = ["Consolidate(", "EMA(", "RSI("]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    results["code_patterns"] = sum(cs_matches.values()) >= 2

    # ── Consolidator used ──
    if cs_matches.get("Consolidate(", False):
        results["consolidator_used"] = True
    elif has_regex(artifact_text, [r"consolidat(?:e|or)", r"registerindicator"]):
        results["consolidator_used"] = True

    # ── Dual resolution indicators (EMA on 4h, RSI on 1h) ──
    has_ema = cs_matches.get("EMA(", False) or has_regex(artifact_text, [r"\bema\s*\("])
    has_rsi = cs_matches.get("RSI(", False) or has_regex(artifact_text, [r"\brsi\s*\("])
    has_4h = has_regex(artifact_text, [r"4\s*h(?:our)?", r"resolution\.hour.*4", r"timedelta.*hours\s*=\s*4"])
    has_1h = has_regex(artifact_text, [r"1\s*h(?:our)?", r"resolution\.hour", r"timedelta.*hours\s*=\s*1"])

    if has_ema and has_rsi and (has_4h or has_1h):
        results["dual_resolution_indicators"] = True

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
        {"item": "consolidator_used", "weight": 0.10, "passed": results["consolidator_used"]},
        {"item": "dual_resolution_indicators", "weight": 0.10, "passed": results["dual_resolution_indicators"]},
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
