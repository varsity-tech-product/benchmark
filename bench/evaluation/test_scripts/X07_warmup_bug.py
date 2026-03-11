"""Evaluation script for X07: Fix missing warm-up period in LEAN EMA crossover."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source
from _debug_check import check_fix_applied, check_root_cause_explained, check_fix_verified
from _implementation_check import compute_behavioral_score, collect_lean_results, check_csharp_patterns


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "warmup_added": False,
        "warmup_guard_added": False,
        "fix_verified": False,
        "root_cause_explained": False,
        "backtest_completed": False,
        "behavioral_score": 0.0,
        "score": 0.0,
    }

    # --- 1. warmup_added (0.25) ---
    # Check for SetWarmUp in .cs files in workspace or student_code
    fix_result = check_fix_applied(
        workspace_path, tool_logs,
        fix_patterns=[r'SetWarmUp\s*\('],
        bug_patterns=[],  # no specific bug pattern needed
        file_extension=".cs",
    )
    results["warmup_added"] = fix_result["fixed"]

    # --- 2. warmup_guard_added (0.15) ---
    guard_result = check_fix_applied(
        workspace_path, tool_logs,
        fix_patterns=[r'IsWarmingUp'],
        bug_patterns=[],
        file_extension=".cs",
    )
    results["warmup_guard_added"] = guard_result["fixed"]

    # --- 3. fix_verified (0.10) ---
    results["fix_verified"] = check_fix_verified(
        tool_logs,
        before_keywords=["trade", "ema", "signal"],
        after_keywords=["setwarmup", "iswarmingup", "trade"],
    )

    # --- 4. root_cause_explained (0.10) ---
    results["root_cause_explained"] = check_root_cause_explained(
        conversation, tool_logs,
        root_cause_keywords=["warm-up", "warmup", "warm up", "partially initialized", "unreliable", "insufficient history", "not ready"],
    )

    # --- 5. backtest_completed (0.15) ---
    lean_results = collect_lean_results(workspace_path)
    results["backtest_completed"] = lean_results is not None

    # --- 6. behavioral_score (0.25) ---
    try:
        beh = compute_behavioral_score("X07", workspace_path)
        results["behavioral_score"] = round(beh.composite_score, 4)
    except Exception:
        results["behavioral_score"] = 0.0

    # --- Scoring ---
    _checklist = [
        {"item": "warmup_added", "weight": 0.25, "passed": results["warmup_added"]},
        {"item": "warmup_guard_added", "weight": 0.15, "passed": results["warmup_guard_added"]},
        {"item": "fix_verified", "weight": 0.10, "passed": results["fix_verified"]},
        {"item": "root_cause_explained", "weight": 0.10, "passed": results["root_cause_explained"]},
        {"item": "backtest_completed", "weight": 0.15, "passed": results["backtest_completed"]},
        {"item": "behavioral_score", "weight": 0.25, "passed": results["behavioral_score"] > 0.3},
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

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
