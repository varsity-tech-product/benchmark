"""Evaluation script for X02: Fix look-ahead bias in SMA crossover strategy."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.debug_check import (
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
    """Evaluate whether the look-ahead bias was fixed in code artifacts.

    QR checks: Is shift(1) present in position assignment and the bare
    Position = Signal (without shift) removed?
    """
    results = {
        "bug_is_fixed": False,
        "code_runs_without_error": False,
        "fix_verified": False,
        "root_cause_explained": False,
        "returns_decreased": False,
        "score": 0.0,
    }

    # --- 1. bug_is_fixed (0.40) ---
    fix_result = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[r"shift\s*\(\s*1\s*\)"],
        bug_patterns=[r"Position.*=.*Signal(?!.*shift)"],
    )
    results["bug_is_fixed"] = (
        fix_result["fixed"] and not fix_result["bug_still_present"]
    )

    # --- 2. code_runs_without_error (0.15) ---
    exec_result = check_execution_output(
        tool_logs,
        success_keywords=["sharpe", "return", "crossover", "sma"],
    )
    results["code_runs_without_error"] = exec_result["output_valid"]

    # --- 3. fix_verified (0.20) ---
    results["fix_verified"] = check_fix_verified(
        tool_logs,
        before_keywords=["sharpe", "return", "signal"],
        after_keywords=["shift", "sharpe", "return"],
    )

    # --- 4. root_cause_explained (0.15) ---
    results["root_cause_explained"] = check_root_cause_explained(
        conversation,
        tool_logs,
        root_cause_keywords=[
            "look-ahead",
            "future information",
            "shift",
            "causal",
            "look ahead",
            "lookahead",
        ],
    )

    # --- 5. returns_decreased (0.10) ---
    # Scan tool logs for evidence that returns or Sharpe decreased after fix
    all_output = []
    for log in tool_logs or []:
        all_output.append(str(log.result or "").lower())
        for v in log.args.values():
            all_output.append(str(v).lower())
    full_text = "\n".join(all_output)
    # Look for explicit mention of decrease or lower values after fix
    decreased_patterns = [
        r"return.*(?:decreas|lower|drop|reduc|fell|went down)",
        r"sharpe.*(?:decreas|lower|drop|reduc|fell|went down)",
        r"(?:decreas|lower|drop|reduc).*(?:return|sharpe)",
        r"(?:before|buggy).*(?:return|sharpe).*(?:after|fixed)",
    ]
    for pattern in decreased_patterns:
        if re.search(pattern, full_text):
            results["returns_decreased"] = True
            break

    # --- Scoring ---
    _checklist = [
        {"item": "bug_is_fixed", "weight": 0.40, "passed": results["bug_is_fixed"]},
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
            "item": "returns_decreased",
            "weight": 0.10,
            "passed": results["returns_decreased"],
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
