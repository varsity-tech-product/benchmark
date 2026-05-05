"""Evaluation script for X05: Fix timezone misalignment in crypto-stock merge."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.debug_check import (
    check_conversation_concepts,
    check_execution_output,
    check_fix_applied,
    check_fix_verified,
    check_root_cause_explained,
)
from common.evidence_helpers import apply_data_source_cap


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate whether the timezone misalignment bug was diagnosed and fixed.

    Checks:
      - bug_is_fixed (0.35): tz_convert or tz_localize applied; bare .dt.date
        without tz_convert no longer present.
      - code_runs_without_error (0.15): Execution output contains expected
        keywords (correlation, btc, aapl, merged).
      - fix_verified (0.20): Evidence that correlation changed after fix or
        aligned rows were shown.
      - root_cause_explained (0.15): Conversation mentions utc, eastern,
        timezone, tz_convert, date boundary, or time zone.
      - alignment_verified (0.15): Inspection of sample merged rows shown
        in tool output.
    """
    results = {
        "bug_is_fixed": False,
        "code_runs_without_error": False,
        "fix_verified": False,
        "root_cause_explained": False,
        "alignment_verified": False,
        "score": 0.0,
    }

    # --- bug_is_fixed (0.35) ---
    fix_info = check_fix_applied(
        workspace_path=workspace_path,
        tool_logs=tool_logs or [],
        fix_patterns=[
            r"tz_convert",
            r"tz_localize",
        ],
        bug_patterns=[
            # Bare .dt.date without a preceding tz_convert on the same line
            r"\.dt\.date(?!.*tz_convert)",
        ],
    )
    results["bug_is_fixed"] = fix_info["fixed"]

    # --- code_runs_without_error (0.15) ---
    exec_info = check_execution_output(
        tool_logs=tool_logs or [],
        success_keywords=["correlation", "btc", "aapl", "merged"],
    )
    results["code_runs_without_error"] = exec_info["output_valid"]

    # --- fix_verified (0.20) ---
    results["fix_verified"] = check_fix_verified(
        tool_logs=tool_logs or [],
        before_keywords=["correlation", "merge", "date"],
        after_keywords=["tz_convert", "correlation", "aligned"],
    )

    # --- root_cause_explained (0.15) ---
    results["root_cause_explained"] = check_root_cause_explained(
        conversation=conversation or [],
        tool_logs=tool_logs or [],
        root_cause_keywords=[
            "utc",
            "eastern",
            "timezone",
            "tz_convert",
            "date boundary",
            "time zone",
        ],
    )

    # --- alignment_verified (0.15) ---
    # Check if sample merged rows were inspected in tool output
    alignment_concepts = check_conversation_concepts(
        conversation=conversation or [],
        required_concepts=["head", "merge", "date", "align"],
        tool_logs=tool_logs or [],
    )
    # Pass if at least 3 of 4 alignment keywords found
    results["alignment_verified"] = alignment_concepts["fraction"] >= 0.75

    # --- Compute weighted score ---
    _checklist = [
        {"item": "bug_is_fixed", "weight": 0.35, "passed": results["bug_is_fixed"]},
        {
            "item": "code_runs_without_error",
            "weight": 0.15,
            "passed": results["code_runs_without_error"],
        },
        {"item": "fix_verified", "weight": 0.20, "passed": results["fix_verified"]},
        {
            "item": "root_cause_explained",
            "weight": 0.15,
            "passed": results["root_cause_explained"],
        },
        {
            "item": "alignment_verified",
            "weight": 0.15,
            "passed": results["alignment_verified"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # Data source verification — cap score if task data wasn't accessed
    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
