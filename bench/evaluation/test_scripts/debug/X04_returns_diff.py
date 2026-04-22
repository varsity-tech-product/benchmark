"""Evaluation script for X04: Fix .diff() vs .pct_change() returns calculation bug."""

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
    """Evaluate whether the .diff() bug was fixed to .pct_change().

    QR checks: Is pct_change() present and .diff() removed from the
    returns computation?
    """
    results = {
        "bug_is_fixed": False,
        "code_runs_without_error": False,
        "fix_verified": False,
        "root_cause_explained": False,
        "realistic_magnitudes": False,
        "score": 0.0,
    }

    # --- 1. bug_is_fixed (0.35) ---
    fix_result = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[r"pct_change\(\)"],
        bug_patterns=[r"Daily_Return.*\.diff\(\)"],
    )
    results["bug_is_fixed"] = (
        fix_result["fixed"] and not fix_result["bug_still_present"]
    )

    # --- 2. code_runs_without_error (0.15) ---
    exec_result = check_execution_output(
        tool_logs,
        success_keywords=["return", "mean", "annualized", "sharpe", "volatility"],
    )
    results["code_runs_without_error"] = exec_result["output_valid"]

    # --- 3. fix_verified (0.20) ---
    # Before/after: dollar-scale stats changed to percentage-scale
    results["fix_verified"] = check_fix_verified(
        tool_logs,
        before_keywords=["diff", "mean", "return"],
        after_keywords=["pct_change", "mean", "return"],
    )

    # --- 4. root_cause_explained (0.15) ---
    results["root_cause_explained"] = check_root_cause_explained(
        conversation,
        tool_logs,
        root_cause_keywords=[
            "diff",
            "pct_change",
            "dollar",
            "percentage",
            "absolute",
            "relative",
        ],
    )

    # --- 5. realistic_magnitudes (0.15) ---
    # Check that mean daily return < 0.01 in output after fix
    all_output = []
    for log in tool_logs or []:
        all_output.append(str(log.result or "").lower())
        for v in log.args.values():
            all_output.append(str(v).lower())
    full_text = "\n".join(all_output)

    # Look for mean daily return values that are realistic (< 0.01)
    mean_patterns = [
        r"mean.*?(?:daily.*?)?return.*?[:=]\s*(-?0\.000\d+)",
        r"mean.*?[:=]\s*(-?0\.000\d+)",
        r"daily.*?return.*?mean.*?(-?0\.000\d+)",
    ]
    for pattern in mean_patterns:
        match = re.search(pattern, full_text)
        if match:
            try:
                val = abs(float(match.group(1)))
                if val < 0.01:
                    results["realistic_magnitudes"] = True
                    break
            except ValueError:
                pass

    # Also check for explicit statements about realistic values
    if not results["realistic_magnitudes"]:
        realistic_patterns = [
            r"mean.*(?:daily|return).*(?:realistic|correct|reasonable|makes sense)",
            r"(?:realistic|correct|reasonable).*mean.*(?:daily|return)",
            r"mean.*0\.000",  # Any output showing mean near zero
        ]
        for pattern in realistic_patterns:
            if re.search(pattern, full_text):
                results["realistic_magnitudes"] = True
                break

    # --- Scoring ---
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
            "item": "realistic_magnitudes",
            "weight": 0.15,
            "passed": results["realistic_magnitudes"],
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
