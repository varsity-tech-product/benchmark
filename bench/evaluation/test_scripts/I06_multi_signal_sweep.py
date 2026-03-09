"""Evaluation script for I06: Multi-signal parameter sweep strategy."""

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
    """Evaluate I06 multi-signal parameter sweep implementation.

    Checks: backtest completion, trade log matching against reference,
    sweep completion (21 combos), top configs identified, funding data
    handling, signal weight patterns.
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
        "sweep_completed": False,
        "top_configs_identified": False,
        "funding_handled": False,
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
    ref_trades = load_reference_trades("I06")
    if ref_trades and agent_trades:
        match_result = match_trades(ref_trades, agent_trades, time_tolerance_bars=1, resolution="daily")
        results["trade_count_match"] = match_result.count_within_tolerance(0.10)
        results["entry_timing_match"] = match_result.entry_match_rate >= 0.80
        results["direction_match"] = match_result.direction_match_rate >= 0.95
        results["exit_timing_match"] = match_result.exit_match_rate >= 0.70
        results["pnl_alignment"] = match_result.pnl_correlation > 0.85
        results["return_proximity"] = match_result.return_within_tolerance(0.20)

    # ── Code patterns (signal weight, funding) ──
    expected_patterns = ["signal", "weight", "funding"]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    has_signal_weight = (
        cs_matches.get("signal", False) and cs_matches.get("weight", False)
    ) or has_regex(artifact_text, [
        r"signal.?weight", r"weight.?signal", r"composite.?score",
        r"weighted.?sum", r"signal.?combin",
    ])
    has_funding = cs_matches.get("funding", False) or has_regex(
        artifact_text, [r"funding.?rate", r"funding.?fee", r"funding.?cost"]
    )
    results["code_patterns"] = has_signal_weight or has_funding

    # ── Sweep completed (21 combos) ──
    import re
    # Look for evidence of 21+ combo runs in logs or results
    sweep_path = os.path.join(workspace_path, "results", "sweep_results.json")
    if os.path.exists(sweep_path):
        try:
            with open(sweep_path) as f:
                sweep_data = json.load(f)
            if isinstance(sweep_data, list) and len(sweep_data) >= 21:
                results["sweep_completed"] = True
            elif isinstance(sweep_data, dict) and len(sweep_data) >= 21:
                results["sweep_completed"] = True
        except (json.JSONDecodeError, IOError):
            pass

    if not results["sweep_completed"]:
        # Check for sweep evidence in artifact text
        combo_matches = re.findall(r"combo|combination|config|parameter.?set", artifact_text)
        if len(combo_matches) >= 10:
            results["sweep_completed"] = True
        elif has_regex(artifact_text, [r"21\s*combo", r"21\s*config", r"sweep.*(?:complete|done|finish)"]):
            results["sweep_completed"] = True

    # ── Top configs identified ──
    if has_regex(artifact_text, [
        r"(?:top|best|optimal)\s*(?:\d+\s*)?config",
        r"(?:top|best|optimal)\s*(?:\d+\s*)?param",
        r"ranked.*config", r"sort.*(?:sharpe|return|pnl)",
    ]):
        results["top_configs_identified"] = True
    # Check for a ranking file
    ranking_path = os.path.join(workspace_path, "results", "top_configs.json")
    if os.path.exists(ranking_path):
        results["top_configs_identified"] = True

    # ── Funding handled ──
    results["funding_handled"] = has_funding

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
        {"item": "sweep_completed", "weight": 0.10, "passed": results["sweep_completed"]},
        {"item": "top_configs_identified", "weight": 0.05, "passed": results["top_configs_identified"]},
        {"item": "funding_handled", "weight": 0.05, "passed": results["funding_handled"]},
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
