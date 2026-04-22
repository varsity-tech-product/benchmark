"""Evaluation script for D08: Alternative Data Integration.

QR Programmatic: checks tool outputs and workspace for evidence that
data merge was performed, frequency alignment was handled, and signal
quality was assessed.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.evidence_helpers import (
    apply_data_source_cap,
    checklist_score,
    collect_evidence,
    has_keywords,
    has_number,
)


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

    combined = collect_evidence(workspace_path, tool_logs)

    # 1. Data merge performed (0.35)
    merge_kws = ["merge", "join", "concat", "align", "combine"]
    if has_keywords(combined, merge_kws):
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
    if has_keywords(combined, align_kws):
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
    if has_keywords(combined, signal_kws) and has_number(combined):
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
    score = checklist_score(_checklist)

    score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
