"""Evaluation script for X09: Fix alpha insight conflict in LEAN framework."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.debug_check import (
    check_fix_applied,
    check_fix_verified,
    check_root_cause_explained,
)
from common.evidence_helpers import apply_data_source_cap
from common.implementation_check import (
    collect_lean_results,
    compute_behavioral_score,
)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "conflict_resolved": False,
        "fix_verified": False,
        "root_cause_explained": False,
        "trades_produced": False,
        "backtest_completed": False,
        "behavioral_score": 0.0,
        "score": 0.0,
    }

    # --- 1. conflict_resolved (0.25) ---
    # Check if PCM was changed or confidence/magnitude was adjusted
    # Acceptable fixes: InsightWeighting, explicit weights, different confidence values,
    # regime gating, removing one alpha
    pcm_fix = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[
            r"InsightWeighting",
            r"weight\s*[:=]\s*0\.[1-9]",
            r"confidence\s*[:=]\s*0\.[89]",  # higher confidence for one alpha
            r"confidence\s*[:=]\s*0\.[1-4]",  # lower confidence for other
        ],
        bug_patterns=[],
        file_extension=".cs",
    )
    results["conflict_resolved"] = pcm_fix["fixed"]

    # Also check if they removed EqualWeighting or changed it
    if not results["conflict_resolved"]:
        all_text = []
        for log in tool_logs or []:
            for v in log.args.values():
                all_text.append(str(v))
            all_text.append(str(log.result or ""))
        full = "\n".join(all_text).lower()
        if "insightweighting" in full or "confidence" in full:
            results["conflict_resolved"] = True

    # --- 2. fix_verified (0.10) ---
    results["fix_verified"] = check_fix_verified(
        tool_logs,
        before_keywords=["0 trade", "no trade", "zero trade", "near-zero"],
        after_keywords=["trade", "filled", "order"],
    )

    # --- 3. root_cause_explained (0.10) ---
    results["root_cause_explained"] = check_root_cause_explained(
        conversation,
        tool_logs,
        root_cause_keywords=[
            "insight conflict",
            "cancel",
            "opposing",
            "conflicting",
            "neutralize",
            "offset",
            "equal weight",
        ],
    )

    # --- 4. trades_produced (0.15) ---
    # Check if more than 10 trades produced
    lean_results = collect_lean_results(workspace_path)
    if lean_results:
        stats = lean_results.get("statistics", lean_results.get("Statistics", {}))
        try:
            total_trades = int(
                stats.get(
                    "Total Trades",
                    stats.get(
                        "Total Orders",
                        stats.get("total_trades", stats.get("total_orders", 0)),
                    ),
                )
            )
            results["trades_produced"] = total_trades > 10
        except (ValueError, TypeError):
            pass

    # Also check tool logs for trade evidence
    if not results["trades_produced"]:
        all_output = "\n".join(str(log.result or "") for log in tool_logs or []).lower()
        if re.search(r"[Tt]rades?:?\s*[1-9]\d+", all_output):
            results["trades_produced"] = True

    # --- 5. backtest_completed (0.15) ---
    results["backtest_completed"] = lean_results is not None

    # --- 6. behavioral_score (0.25) ---
    try:
        beh = compute_behavioral_score("X09", workspace_path)
        results["behavioral_score"] = round(beh.composite_score, 4)
    except Exception:
        results["behavioral_score"] = 0.0

    # --- Scoring ---
    _checklist = [
        {
            "item": "conflict_resolved",
            "weight": 0.25,
            "passed": results["conflict_resolved"],
        },
        {"item": "fix_verified", "weight": 0.10, "passed": results["fix_verified"]},
        {
            "item": "root_cause_explained",
            "weight": 0.10,
            "passed": results["root_cause_explained"],
        },
        {
            "item": "trades_produced",
            "weight": 0.15,
            "passed": results["trades_produced"],
        },
        {
            "item": "backtest_completed",
            "weight": 0.15,
            "passed": results["backtest_completed"],
        },
        {
            "item": "behavioral_score",
            "weight": 0.25,
            "passed": results["behavioral_score"] > 0.3,
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
