"""Evaluation script for I05: Cross-asset pairs/stat-arb strategy."""

import json
import os
import sys
from collections import Counter

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
    """Evaluate I05 cross-asset pairs/stat-arb implementation.

    Checks: backtest completion, trade log produced, behavioral equivalence,
    pair selection logic, multi-leg positions, exposure management,
    correlation/zscore code patterns.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,
        "position_overlap": False,
        "performance_match": False,
        "trade_count_match": False,
        "code_patterns": False,
        "pair_selection_present": False,
        "candidate_pairs_used": False,
        "multi_leg_positions": False,
        "exposure_capped": False,
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
    behavioral = compute_behavioral_score("I05", workspace_path, resolution="daily")
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    results["position_overlap"] = behavioral.position_score >= 0.60
    results["performance_match"] = behavioral.performance_score >= 0.50
    results["trade_count_match"] = behavioral.trade_score >= 0.40

    # ── Code patterns (correlation / zscore) ──
    expected_patterns = ["correlation", "zscore"]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    has_corr = cs_matches.get("correlation", False) or has_regex(
        artifact_text,
        [r"\bcorrelat(?:ion|ed)\b", r"\bpearson\b", r"\bcov(?:ariance)?\b"],
    )
    has_zscore = cs_matches.get("zscore", False) or has_regex(
        artifact_text,
        [r"\bz.?score\b", r"\bspread\b.*\bstd\b", r"\bstandard.?deviation\b"],
    )
    results["code_patterns"] = has_corr and has_zscore

    # ── Candidate pairs file usage ──
    # Check if the agent loaded the provided candidate pairs file
    if has_any(
        artifact_text,
        [
            "I05_candidate_pairs",
            "candidate_pairs.json",
            "candidate_pairs",
        ],
    ):
        results["candidate_pairs_used"] = True

    # ── Pair selection present ──
    # Accept either (a) regex evidence of pair selection logic OR
    # (b) agent loading the candidate pairs file
    if results["candidate_pairs_used"] or has_regex(
        artifact_text,
        [
            r"pair.?select",
            r"cointegrat",
            r"spread\s*=",
            r"select.*pair",
            r"rank.*correlat",
            r"correlat.*rank",
        ],
    ):
        results["pair_selection_present"] = True

    # ── Multi-leg positions ──
    if has_regex(
        artifact_text,
        [
            r"(?:long|buy)\b.*\b(?:short|sell)",
            r"leg\s*[12ab]",
            r"hedge.?ratio",
            r"setholdings.*setholdings",
            r"marketorder.*marketorder",
        ],
    ):
        results["multi_leg_positions"] = True
    if agent_trades:
        entry_times = [t.get("entry_time", "") for t in agent_trades]
        time_counts = Counter(entry_times)
        if any(c >= 2 for c in time_counts.values()):
            results["multi_leg_positions"] = True

    # ── Exposure capped ──
    if has_regex(
        artifact_text,
        [
            r"max.?exposure",
            r"exposure.?limit",
            r"position.?limit",
            r"max.?position",
            r"risk.?limit",
            r"leverage.?cap",
            r"setholdings\s*\(.*0\.\d",
        ],
    ):
        results["exposure_capped"] = True

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
            "weight": 0.45,
            "score": behavioral.composite_score,
        },
        {"item": "code_patterns", "weight": 0.05, "passed": results["code_patterns"]},
        {
            "item": "candidate_pairs_used",
            "weight": 0.05,
            "passed": results["candidate_pairs_used"],
        },
        {
            "item": "pair_selection_present",
            "weight": 0.05,
            "passed": results["pair_selection_present"],
        },
        {
            "item": "multi_leg_positions",
            "weight": 0.05,
            "passed": results["multi_leg_positions"],
        },
        {
            "item": "exposure_capped",
            "weight": 0.05,
            "passed": results["exposure_capped"],
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
