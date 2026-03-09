"""Evaluation script for I06: Multi-signal parameter sweep strategy."""

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

    Checks: backtest completion, trade log produced, behavioral equivalence,
    sweep completion (21 combos), top configs identified, funding data
    handling, signal weight patterns.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,
        "position_overlap": False,
        "performance_match": False,
        "trade_count_match": False,
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

    # ── Behavioral scoring ──
    behavioral = compute_behavioral_score("I06", workspace_path, resolution="daily")
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    results["position_overlap"] = behavioral.position_score >= 0.60
    results["performance_match"] = behavioral.performance_score >= 0.50
    results["trade_count_match"] = behavioral.trade_score >= 0.40

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
    ranking_path = os.path.join(workspace_path, "results", "top_configs.json")
    if os.path.exists(ranking_path):
        results["top_configs_identified"] = True

    # ── Funding handled ──
    results["funding_handled"] = has_funding

    # ── Scoring ──
    _checklist = [
        {"item": "backtest_completed",      "weight": 0.05, "passed": results["backtest_completed"]},
        {"item": "trade_log_produced",      "weight": 0.05, "passed": results["trade_log_produced"]},
        {"item": "behavioral_score",        "weight": 0.45, "score": behavioral.composite_score},
        {"item": "code_patterns",           "weight": 0.05, "passed": results["code_patterns"]},
        {"item": "sweep_completed",         "weight": 0.10, "passed": results["sweep_completed"]},
        {"item": "top_configs_identified",  "weight": 0.05, "passed": results["top_configs_identified"]},
        {"item": "funding_handled",         "weight": 0.05, "passed": results["funding_handled"]},
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
