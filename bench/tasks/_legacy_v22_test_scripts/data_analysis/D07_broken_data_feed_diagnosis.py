"""Evaluation script for D07: Broken Data Feed Diagnosis.

QR Programmatic: checks tool outputs and workspace for evidence that
data anomalies were detected, multiple issue types were identified,
and a diagnostic artifact was produced.
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
        "anomalies_detected": False,
        "multiple_issue_types": False,
        "diagnostic_artifact": False,
        "score": 0.0,
    }

    combined = collect_evidence(workspace_path, tool_logs)
    workspace_files = (
        os.listdir(workspace_path) if os.path.isdir(workspace_path) else []
    )

    # 1. Anomalies detected (0.40)
    anomaly_kws = [
        "missing",
        "duplicate",
        "outlier",
        "spike",
        "negative",
        "nan",
        "anomaly",
        "gap",
    ]
    hits = sum(1 for kw in anomaly_kws if kw in combined)
    if hits >= 2 and has_number(combined):
        results["anomalies_detected"] = True

    # 2. Multiple issue types identified (0.30)
    issue_buckets = [
        ["missing", "gap", "nan", "null"],
        ["duplicate", "repeated"],
        ["outlier", "spike", "negative", "abnormal"],
        ["split", "timezone", "corporate action", "adjustment"],
    ]
    buckets_hit = sum(1 for bucket in issue_buckets if has_keywords(combined, bucket))
    if buckets_hit >= 2:
        results["multiple_issue_types"] = True

    # 3. Diagnostic artifact produced (0.30)
    has_report = any(
        f.endswith((".json", ".txt", ".md", ".csv")) for f in workspace_files
    )
    if has_report:
        results["diagnostic_artifact"] = True

    _checklist = [
        {
            "item": "anomalies_detected",
            "weight": 0.40,
            "passed": results["anomalies_detected"],
        },
        {
            "item": "multiple_issue_types",
            "weight": 0.30,
            "passed": results["multiple_issue_types"],
        },
        {
            "item": "diagnostic_artifact",
            "weight": 0.30,
            "passed": results["diagnostic_artifact"],
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
