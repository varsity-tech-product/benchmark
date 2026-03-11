"""Evaluation script for E04: Production Debugging (I + X).

Checks: three bug fixes (logic, warmup, position size), root cause
explanation, LEAN backtest completion, trade production, behavioral scoring.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _debug_check import (
    check_fix_applied,
    check_root_cause_explained,
)
from _implementation_check import (
    collect_artifact_text,
    collect_lean_results,
    compute_behavioral_score,
    load_agent_trades,
)
from _data_source_check import verify_data_source


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate E04: Production Debugging."""
    results = {
        "logic_bug_fixed": False,
        "warmup_added": False,
        "warmup_guard_added": False,
        "position_size_fixed": False,
        "root_causes_explained": False,
        "backtest_completed": False,
        "trades_produced": False,
        "behavioral_score": 0.0,
        "score": 0.0,
    }

    # ── 1. Logic bug fixed (0.20) ──
    logic_fix = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[r"emaFast.*>.*emaSlow", r"fastValue\s*>\s*slowValue"],
        bug_patterns=[r"emaFast.*<.*emaSlow", r"fastValue\s*<\s*slowValue"],
        file_extension=".cs",
    )
    results["logic_bug_fixed"] = logic_fix["fixed"] and not logic_fix["bug_still_present"]

    # ── 2. Warmup added (0.15) ──
    warmup_fix = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[r"SetWarmUp\s*\("],
        bug_patterns=[],
        file_extension=".cs",
    )
    results["warmup_added"] = warmup_fix["fixed"]

    # ── 3. Warmup guard added (0.10) ──
    guard_fix = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[r"IsWarmingUp"],
        bug_patterns=[],
        file_extension=".cs",
    )
    results["warmup_guard_added"] = guard_fix["fixed"]

    # ── 4. Position size fixed (0.10) ──
    size_fix = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[r"SetHoldings.*[01]\.\d"],
        bug_patterns=[r"SetHoldings.*2\.0"],
        file_extension=".cs",
    )
    results["position_size_fixed"] = size_fix["fixed"] and not size_fix["bug_still_present"]

    # ── 5. Root causes explained (0.10) ──
    results["root_causes_explained"] = check_root_cause_explained(
        conversation,
        tool_logs,
        root_cause_keywords=[
            "inverted",
            "wrong direction",
            "bearish instead",
            "signal direction",
            "warm-up",
            "warmup",
            "initialization",
            "seeded",
            "leverage",
            "position size",
            "200%",
            "2.0",
            "excessive",
        ],
    )

    # ── 6. Backtest completed (0.10) ──
    lean_results = collect_lean_results(workspace_path)
    if lean_results is not None:
        results["backtest_completed"] = True
    else:
        artifact_text = collect_artifact_text(workspace_path, tool_logs)
        if any(kw in artifact_text for kw in ["algorithm completed", "total trades", "e04 complete"]):
            results["backtest_completed"] = True

    # ── 7. Trades produced (0.10) ──
    agent_trades = load_agent_trades(workspace_path)
    if agent_trades:
        results["trades_produced"] = True

    # ── 8. Behavioral score (0.15) ──
    behavioral = compute_behavioral_score("E04", workspace_path, resolution="daily")
    results["behavioral_score"] = round(behavioral.composite_score, 4)

    # ── Count bugs fixed for gate ──
    bugs_fixed = sum([
        results["logic_bug_fixed"],
        results["warmup_added"] or results["warmup_guard_added"],
        results["position_size_fixed"],
    ])

    # ── Scoring ──
    _checklist = [
        {"item": "logic_bug_fixed",       "weight": 0.20, "passed": results["logic_bug_fixed"]},
        {"item": "warmup_added",           "weight": 0.15, "passed": results["warmup_added"]},
        {"item": "warmup_guard_added",     "weight": 0.10, "passed": results["warmup_guard_added"]},
        {"item": "position_size_fixed",    "weight": 0.10, "passed": results["position_size_fixed"]},
        {"item": "root_causes_explained",  "weight": 0.10, "passed": results["root_causes_explained"]},
        {"item": "backtest_completed",     "weight": 0.10, "passed": results["backtest_completed"]},
        {"item": "trades_produced",        "weight": 0.10, "passed": results["trades_produced"]},
        {"item": "behavioral_score",       "weight": 0.15, "score": behavioral.composite_score},
    ]
    score = sum(
        c["weight"] * c.get("score", 1.0 if c.get("passed") else 0.0)
        for c in _checklist
    )

    # ── Gates ──
    if bugs_fixed < 2:
        score = min(score, 0.35)
    if not results["backtest_completed"]:
        score = min(score, 0.40)

    # ── Data source verification ──
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["bugs_fixed_count"] = bugs_fixed
    results["behavioral_composite"] = round(behavioral.composite_score, 4)
    results["behavioral_layers"] = behavioral.layers_available
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
