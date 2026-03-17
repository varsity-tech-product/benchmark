"""Evaluation script for X03: Fix missing short position in Bollinger Band strategy."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.data_source_check import verify_data_source
from common.debug_check import (
    check_execution_output,
    check_fix_applied,
    check_fix_verified,
    check_root_cause_explained,
)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate whether the missing short position bug was fixed.

    QR checks: Is Position = -1 present for upper-band crossover and
    the incorrect Position = 0 removed from that context?
    """
    results = {
        "bug_is_fixed": False,
        "code_runs_without_error": False,
        "fix_verified": False,
        "root_cause_explained": False,
        "short_positions_exist": False,
        "score": 0.0,
    }

    # --- 1. bug_is_fixed (0.35) ---
    fix_result = check_fix_applied(
        workspace_path,
        tool_logs,
        fix_patterns=[r"Position.*=\s*-\s*1"],
        bug_patterns=[r"BB_Upper.*Position.*=\s*0", r"Position.*=\s*0.*BB_Upper"],
    )
    results["bug_is_fixed"] = fix_result["fixed"]

    # Also do a broader check: scan workspace and tool logs for the fix
    # in context of upper-band logic
    if not results["bug_is_fixed"]:
        all_texts = []
        if workspace_path and os.path.isdir(workspace_path):
            for root, _, files in os.walk(workspace_path):
                for fname in files:
                    if fname.endswith(".py"):
                        fpath = os.path.join(root, fname)
                        try:
                            with open(fpath) as f:
                                all_texts.append(f.read())
                        except (IOError, UnicodeDecodeError):
                            pass
        for log in tool_logs or []:
            for v in log.args.values():
                all_texts.append(str(v))

        for text in all_texts:
            # Check for -1 assignment in any position logic
            if re.search(r"=\s*-\s*1", text) and re.search(r"[Uu]pper", text):
                results["bug_is_fixed"] = True
                break

    # --- 2. code_runs_without_error (0.15) ---
    exec_result = check_execution_output(
        tool_logs,
        success_keywords=["sharpe", "bollinger", "short_days", "position"],
    )
    results["code_runs_without_error"] = exec_result["output_valid"]

    # --- 3. fix_verified (0.20) ---
    # Check that short_days > 0 appears in tool output after fix
    all_output = []
    for log in tool_logs or []:
        all_output.append(str(log.result or "").lower())
        for v in log.args.values():
            all_output.append(str(v).lower())
    full_text = "\n".join(all_output)

    # Look for short_days with a positive value
    short_days_match = re.search(r'short_days["\s:=]+(\d+)', full_text)
    if short_days_match and int(short_days_match.group(1)) > 0:
        results["fix_verified"] = True

    # Also accept general before/after comparison evidence
    if not results["fix_verified"]:
        results["fix_verified"] = check_fix_verified(
            tool_logs,
            before_keywords=["short_days", "0"],
            after_keywords=["short_days", "-1"],
        )

    # --- 4. root_cause_explained (0.15) ---
    results["root_cause_explained"] = check_root_cause_explained(
        conversation,
        tool_logs,
        root_cause_keywords=[
            "flat",
            "not short",
            "no-op",
            "three-state",
            "-1",
            "missing short",
        ],
    )

    # --- 5. short_positions_exist (0.15) ---
    # Scan for short_days with value > 0, or SHORT label in output
    if results["fix_verified"]:
        results["short_positions_exist"] = True
    else:
        short_patterns = [
            r'short_days["\s:=]+[1-9]\d*',
            r"SHORT",
            r"short.*position.*[1-9]",
            r"position.*-1",
        ]
        for pattern in short_patterns:
            if re.search(pattern, full_text, re.IGNORECASE):
                results["short_positions_exist"] = True
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
            "item": "short_positions_exist",
            "weight": 0.15,
            "passed": results["short_positions_exist"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    # Data source verification — cap score if task data wasn't accessed
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
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
