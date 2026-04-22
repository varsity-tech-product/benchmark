"""Evaluation script for I01: Implement SMA trend filter on LEAN (single symbol)."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.evidence_helpers import apply_data_source_cap
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
    """Evaluate I01 SMA implementation on LEAN.

    Checks: backtest completion, trade log produced, behavioral equivalence
    (signal agreement + position overlap + performance + trade similarity),
    C# code patterns (LEAN API usage), and SMA indicator usage.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,
        "position_overlap": False,
        "performance_match": False,
        "trade_count_match": False,
        "code_patterns": False,
        "sma_indicator_used": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)

    # ── Backtest completion ──
    lean_results = collect_lean_results(workspace_path)
    if lean_results is not None:
        results["backtest_completed"] = True
    elif has_any(
        artifact_text,
        ["algorithm completed", "total trades", "backtest complete", "i01 complete"],
    ):
        results["backtest_completed"] = True

    # ── Trade log ──
    agent_trades = load_agent_trades(workspace_path)
    if agent_trades:
        results["trade_log_produced"] = True

    # ── Behavioral scoring ──
    behavioral = compute_behavioral_score("I01", workspace_path, resolution="daily")
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    results["position_overlap"] = behavioral.position_score >= 0.60
    results["performance_match"] = behavioral.performance_score >= 0.50
    results["trade_count_match"] = behavioral.trade_score >= 0.40

    # ── Code patterns (LEAN API usage) ──
    expected_patterns = ["AddCrypto", "SMA(", "SetWarmUp", "IsWarmingUp"]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    results["code_patterns"] = sum(cs_matches.values()) >= 3

    # ── SMA indicator specifically used ──
    if cs_matches.get("SMA(", False):
        results["sma_indicator_used"] = True
    elif has_regex(artifact_text, [r"sma\s*\(", r"simplemovingaverage"]):
        results["sma_indicator_used"] = True

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
            "weight": 0.65,
            "score": behavioral.composite_score,
        },
        {"item": "code_patterns", "weight": 0.10, "passed": results["code_patterns"]},
        {
            "item": "sma_indicator_used",
            "weight": 0.10,
            "passed": results["sma_indicator_used"],
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
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["behavioral_composite"] = round(behavioral.composite_score, 4)
    results["behavioral_layers"] = behavioral.layers_available
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
