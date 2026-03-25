"""Evaluation script for I03: Mean-reversion strategy with stop-loss."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.data_source_check import verify_data_source
from common.implementation_check import (
    check_csharp_patterns,
    collect_artifact_text,
    collect_lean_results,
    compute_behavioral_score,
    compute_trial_efficiency,
    has_any,
    has_regex,
    load_agent_trades,
)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate I03 mean-reversion implementation.

    Checks: backtest completion, trade log produced, behavioral equivalence,
    stop-loss logic, exit reason tagging, long+short presence, code patterns.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,
        "position_overlap": False,
        "performance_match": False,
        "trade_count_match": False,
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
    elif has_any(
        artifact_text, ["algorithm completed", "total trades", "backtest complete"]
    ):
        results["backtest_completed"] = True

    # ── Trade log ──
    agent_trades = load_agent_trades(workspace_path)
    if agent_trades:
        results["trade_log_produced"] = True

    # ── Behavioral scoring ──
    behavioral = compute_behavioral_score("I03", workspace_path, resolution="daily")
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    results["position_overlap"] = behavioral.position_score >= 0.60
    results["performance_match"] = behavioral.performance_score >= 0.50
    results["trade_count_match"] = behavioral.trade_score >= 0.40

    # ── Code patterns ──
    expected_patterns = ["RSI(", "UnrealizedProfit", "stop"]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    results["code_patterns"] = sum(cs_matches.values()) >= 2

    # ── Stop-loss present ──
    if has_any(
        artifact_text,
        ["stoploss", "stop_loss", "stop loss", "stoplimitorder", "stopmarketorder"],
    ):
        results["stop_loss_present"] = True
    elif cs_matches.get("stop", False):
        results["stop_loss_present"] = True

    # ── Exit reason tagged ──
    if agent_trades:
        tagged = sum(
            1 for t in agent_trades if t.get("exit_reason") or t.get("exitReason")
        )
        if tagged >= len(agent_trades) * 0.5:
            results["exit_reason_tagged"] = True
    if has_regex(artifact_text, [r"exit.?reason", r"close.?reason", r"exit.?tag"]):
        results["exit_reason_tagged"] = True

    # ── Long and short both present ──
    if agent_trades:
        directions = {t.get("direction", "").lower() for t in agent_trades}
        if "long" in directions and "short" in directions:
            results["long_short_both"] = True
    if has_regex(artifact_text, [r"\blong\b"]) and has_regex(
        artifact_text, [r"\bshort\b"]
    ):
        if has_any(artifact_text, ["setholdings", "marketorder", "buy", "sell"]):
            results["long_short_both"] = True

    # ── Trial efficiency ──
    efficiency_score = compute_trial_efficiency(workspace_path)

    # ── Scoring ──
    _checklist = [
        {
            "item": "backtest_completed",
            "weight": 0.05,
            "passed": results["backtest_completed"],
        },
        {
            "item": "trade_log_produced",
            "weight": 0.05,
            "passed": results["trade_log_produced"],
        },
        {
            "item": "behavioral_score",
            "weight": 0.60,
            "score": behavioral.composite_score,
        },
        {"item": "code_patterns", "weight": 0.05, "passed": results["code_patterns"]},
        {
            "item": "stop_loss_present",
            "weight": 0.08,
            "passed": results["stop_loss_present"],
        },
        {
            "item": "long_short_both",
            "weight": 0.07,
            "passed": results["long_short_both"],
        },
        {
            "item": "exit_reason_tagged",
            "weight": 0.05,
            "passed": results["exit_reason_tagged"],
        },
        {"item": "trial_efficiency", "weight": 0.05, "score": efficiency_score},
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
