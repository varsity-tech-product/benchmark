"""Evaluation script for D08: Alternative Data Integration.

QR Programmatic: checks tool outputs and workspace for evidence that
data merge was performed, frequency alignment was handled, and signal
quality was assessed.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import verify_data_source


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "data_merge_performed": False,
        "alignment_handled": False,
        "signal_quality_assessed": False,
        "score": 0.0,
    }

    combined = _collect_evidence(workspace_path, tool_logs)

    # 1. Data merge performed (0.35)
    merge_kws = ["merge", "join", "concat", "align", "combine"]
    if _has_keywords(combined, merge_kws):
        results["data_merge_performed"] = True

    # 2. Alignment handled (0.30)
    align_kws = [
        "resample",
        "shift",
        "lag",
        "asfreq",
        "reindex",
        "frequency",
        "date_range",
    ]
    if _has_keywords(combined, align_kws):
        results["alignment_handled"] = True

    # 3. Signal quality assessed (0.35)
    signal_kws = [
        "corr",
        "spearman",
        "ic",
        "information coefficient",
        "rank",
        "pearson",
        "correlation",
        "compute_statistics",
    ]
    if _has_keywords(combined, signal_kws) and _has_number(combined):
        results["signal_quality_assessed"] = True

    _checklist = [
        {
            "item": "data_merge_performed",
            "weight": 0.35,
            "passed": results["data_merge_performed"],
        },
        {
            "item": "alignment_handled",
            "weight": 0.30,
            "passed": results["alignment_handled"],
        },
        {
            "item": "signal_quality_assessed",
            "weight": 0.35,
            "passed": results["signal_quality_assessed"],
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


def _collect_evidence(workspace_path: str, tool_logs: list) -> str:
    parts = []
    for log in tool_logs or []:
        parts.append(log.name)
        parts.append(str(log.args))
        parts.append(str(log.result or ""))
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if fname.endswith((".txt", ".json", ".md", ".csv", ".log")):
                try:
                    with open(os.path.join(workspace_path, fname)) as f:
                        parts.append(f.read()[:2000])
                except (IOError, UnicodeDecodeError):
                    pass
    return " ".join(parts).lower()


def _has_keywords(text: str, keywords: list[str]) -> bool:
    return any(kw in text for kw in keywords)


def _has_number(text: str) -> bool:
    return bool(re.search(r"-?\d+\.?\d*", text))


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
