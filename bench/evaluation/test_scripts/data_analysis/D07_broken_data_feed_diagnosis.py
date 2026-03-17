"""Evaluation script for D07: Broken Data Feed Diagnosis.

QR Programmatic: checks tool outputs and workspace for evidence that
data anomalies were detected, multiple issue types were identified,
and a diagnostic artifact was produced.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.data_source_check import verify_data_source


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

    combined = _collect_evidence(workspace_path, tool_logs)
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
    if hits >= 2 and _has_number(combined):
        results["anomalies_detected"] = True

    # 2. Multiple issue types identified (0.30)
    issue_buckets = [
        ["missing", "gap", "nan", "null"],
        ["duplicate", "repeated"],
        ["outlier", "spike", "negative", "abnormal"],
        ["split", "timezone", "corporate action", "adjustment"],
    ]
    buckets_hit = sum(
        1 for bucket in issue_buckets if any(kw in combined for kw in bucket)
    )
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


def _has_number(text: str) -> bool:
    return bool(re.search(r"-?\d+\.?\d*", text))


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
