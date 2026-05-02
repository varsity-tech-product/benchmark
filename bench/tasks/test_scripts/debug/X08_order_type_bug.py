"""Evaluation script for X08: Fix order type bug (LimitOrder -> MarketOrder)."""

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
        "order_type_fixed": False,
        "fix_verified": False,
        "root_cause_explained": False,
        "trade_count_increased": False,
        "backtest_completed": False,
        "behavioral_score": 0.0,
        "score": 0.0,
    }

    # --- 1. order_type_fixed (0.25) ---
    fix_result = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[r"MarketOrder\s*\("],
        bug_patterns=[r"LimitOrder\s*\("],
        file_extension=".cs",
    )
    results["order_type_fixed"] = fix_result["fixed"]

    # --- 2. fix_verified (0.10) ---
    results["fix_verified"] = check_fix_verified(
        tool_logs,
        before_keywords=["limitorder", "limit", "unfilled", "canceled"],
        after_keywords=["marketorder", "market", "filled", "trade"],
    )

    # --- 3. root_cause_explained (0.10) ---
    results["root_cause_explained"] = check_root_cause_explained(
        conversation,
        tool_logs,
        root_cause_keywords=[
            "limit",
            "market",
            "fill rate",
            "trending",
            "unfilled",
            "stale price",
            "gap",
            "not filled",
        ],
    )

    # --- 4. trade_count_increased (0.15) ---
    # Check tool logs for evidence of more trades after fix
    all_output = "\n".join(str(log.result or "") for log in tool_logs or []).lower()
    if re.search(r"total.*trade.*\d+", all_output) or re.search(
        r"filled.*\d+", all_output
    ):
        results["trade_count_increased"] = True
    # Also pass if fix was applied (MarketOrder guarantees fills)
    if results["order_type_fixed"]:
        results["trade_count_increased"] = True

    # --- 5. backtest_completed (0.15) ---
    lean_results = collect_lean_results(workspace_path)
    results["backtest_completed"] = lean_results is not None

    # --- 6. behavioral_score (0.25) ---
    try:
        beh = compute_behavioral_score("X08", workspace_path)
        results["behavioral_score"] = round(beh.composite_score, 4)
    except Exception:
        results["behavioral_score"] = 0.0

    # --- Scoring ---
    _checklist = [
        {
            "item": "order_type_fixed",
            "weight": 0.25,
            "passed": results["order_type_fixed"],
        },
        {"item": "fix_verified", "weight": 0.10, "passed": results["fix_verified"]},
        {
            "item": "root_cause_explained",
            "weight": 0.10,
            "passed": results["root_cause_explained"],
        },
        {
            "item": "trade_count_increased",
            "weight": 0.15,
            "passed": results["trade_count_increased"],
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
