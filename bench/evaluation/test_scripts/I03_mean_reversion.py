"""Evaluation script for I03: Mean-reversion strategy with stop-loss."""

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
    """Evaluate I03 mean-reversion implementation.

    Checks: backtest completion, trade log matching against reference,
    stop-loss logic, exit reason tagging, long+short presence, code patterns.
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
        "stop_loss_present": False,
        "exit_reason_tagged": False,
        "long_short_both": False,
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
    ref_trades = load_reference_trades("I03")
    if ref_trades and agent_trades:
        match_result = match_trades(ref_trades, agent_trades, time_tolerance_bars=1, resolution="daily")
        results["trade_count_match"] = match_result.count_within_tolerance(0.10)
        results["entry_timing_match"] = match_result.entry_match_rate >= 0.80
        results["direction_match"] = match_result.direction_match_rate >= 0.95
        results["exit_timing_match"] = match_result.exit_match_rate >= 0.70
        results["pnl_alignment"] = match_result.pnl_correlation > 0.85
        results["return_proximity"] = match_result.return_within_tolerance(0.20)

    # ── Code patterns ──
    expected_patterns = ["RSI(", "UnrealizedProfit", "stop"]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    results["code_patterns"] = sum(cs_matches.values()) >= 2

    # ── Stop-loss present ──
    if has_any(artifact_text, ["stoploss", "stop_loss", "stop loss", "stoplimitorder", "stopmarketorder"]):
        results["stop_loss_present"] = True
    elif cs_matches.get("stop", False):
        results["stop_loss_present"] = True

    # ── Exit reason tagged ──
    if agent_trades:
        tagged = sum(1 for t in agent_trades if t.get("exit_reason") or t.get("exitReason"))
        if tagged >= len(agent_trades) * 0.5:
            results["exit_reason_tagged"] = True
    if has_regex(artifact_text, [r"exit.?reason", r"close.?reason", r"exit.?tag"]):
        results["exit_reason_tagged"] = True

    # ── Long and short both present ──
    if agent_trades:
        directions = {t.get("direction", "").lower() for t in agent_trades}
        if "long" in directions and "short" in directions:
            results["long_short_both"] = True
    if has_regex(artifact_text, [r"\blong\b"]) and has_regex(artifact_text, [r"\bshort\b"]):
        if has_any(artifact_text, ["setholdings", "marketorder", "buy", "sell"]):
            results["long_short_both"] = True

    # ── Scoring ──
    _checklist = [
        {"item": "backtest_completed", "weight": 0.05, "passed": results["backtest_completed"]},
        {"item": "trade_log_produced", "weight": 0.05, "passed": results["trade_log_produced"]},
        {"item": "trade_count_match", "weight": 0.20, "passed": results["trade_count_match"]},
        {"item": "entry_timing_match", "weight": 0.20, "passed": results["entry_timing_match"]},
        {"item": "direction_match", "weight": 0.15, "passed": results["direction_match"]},
        {"item": "exit_timing_match", "weight": 0.15, "passed": results["exit_timing_match"]},
        {"item": "pnl_alignment", "weight": 0.10, "passed": results["pnl_alignment"]},
        {"item": "return_proximity", "weight": 0.05, "passed": results["return_proximity"]},
        {"item": "code_patterns", "weight": 0.05, "passed": results["code_patterns"]},
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
