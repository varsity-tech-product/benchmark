"""Evaluation script for X10: Fix stale universe / survivorship bias."""

import json
import os
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
        "point_in_time_universe": False,
        "listing_date_check": False,
        "volume_filter_fixed": False,
        "fix_verified": False,
        "root_cause_explained": False,
        "backtest_completed": False,
        "behavioral_score": 0.0,
        "score": 0.0,
    }

    # --- 1. point_in_time_universe (0.25) ---
    # Check for date-conditional AddCrypto or AddUniverse or listing date logic
    pit_fix = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[
            r"listing.*date",
            r"list.*date.*check",
            r"Time\s*>=?\s*",
            r"StartDate\s*>=?\s*",
            r"if\s*\(.*[Dd]ate.*\)",
        ],
        bug_patterns=[],
        file_extension=".cs",
    )
    results["point_in_time_universe"] = pit_fix["fixed"]

    # --- 2. listing_date_check (0.15) ---
    listing_fix = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[
            r"listing",
            r"[Ll]isted",
            r"[Ll]aunch",
        ],
        bug_patterns=[],
        file_extension=".cs",
    )
    results["listing_date_check"] = listing_fix["fixed"]

    # --- 3. volume_filter_fixed (0.10) ---
    vol_fix = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[
            r"[Tt]railing.*[Vv]olume",
            r"[Rr]olling.*[Vv]olume",
            r"SMA.*[Vv]olume",
            r"[Vv]olume.*[Ww]indow",
        ],
        bug_patterns=[],
        file_extension=".cs",
    )
    results["volume_filter_fixed"] = vol_fix["fixed"]

    # --- 4. fix_verified (0.10) ---
    results["fix_verified"] = check_fix_verified(
        tool_logs,
        before_keywords=["survivorship", "stale", "bias", "inflated"],
        after_keywords=["listing", "trailing", "fixed", "trade"],
    )

    # --- 5. root_cause_explained (0.10) ---
    results["root_cause_explained"] = check_root_cause_explained(
        conversation,
        tool_logs,
        root_cause_keywords=[
            "survivorship",
            "point-in-time",
            "look-ahead",
            "point in time",
            "survivorship bias",
            "stale universe",
            "future data",
            "listing date",
        ],
    )

    # --- 6. backtest_completed (0.05) ---
    lean_results = collect_lean_results(workspace_path)
    results["backtest_completed"] = lean_results is not None

    # --- 7. behavioral_score (0.25) ---
    try:
        beh = compute_behavioral_score("X10", workspace_path)
        results["behavioral_score"] = round(beh.composite_score, 4)
    except Exception:
        results["behavioral_score"] = 0.0

    # --- Scoring ---
    _checklist = [
        {
            "item": "point_in_time_universe",
            "weight": 0.25,
            "passed": results["point_in_time_universe"],
        },
        {
            "item": "listing_date_check",
            "weight": 0.15,
            "passed": results["listing_date_check"],
        },
        {
            "item": "volume_filter_fixed",
            "weight": 0.10,
            "passed": results["volume_filter_fixed"],
        },
        {"item": "fix_verified", "weight": 0.10, "passed": results["fix_verified"]},
        {
            "item": "root_cause_explained",
            "weight": 0.10,
            "passed": results["root_cause_explained"],
        },
        {
            "item": "backtest_completed",
            "weight": 0.05,
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
