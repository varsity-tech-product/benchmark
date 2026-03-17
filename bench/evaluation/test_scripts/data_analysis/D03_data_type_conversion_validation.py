"""Evaluation script for D03: Data Type Conversion & Validation.

QR Programmatic: checks tool outputs and workspace for evidence that
dtypes were inspected, conversions were performed, and validation was applied.
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
        "dtype_inspection_done": False,
        "conversion_performed": False,
        "validation_present": False,
        "score": 0.0,
    }

    combined = _collect_evidence(workspace_path, tool_logs)

    # 1. Dtype inspection (0.30)
    dtype_kws = [
        "dtypes",
        "info()",
        "dtype",
        "float64",
        "int64",
        "object",
        "datetime64",
    ]
    if _has_keywords(combined, dtype_kws):
        results["dtype_inspection_done"] = True

    # 2. Conversion performed (0.40)
    conv_kws = [
        "to_datetime",
        "astype",
        "to_numeric",
        "pd.to_",
        "errors='coerce'",
        'errors="coerce"',
        "float(",
        "int(",
    ]
    if _has_keywords(combined, conv_kws):
        results["conversion_performed"] = True

    # 3. Validation present (0.30)
    val_kws = [
        "assert",
        "check",
        "between",
        "clip",
        "describe",
        "min()",
        "max()",
        "value_counts",
        "unique()",
        "range",
    ]
    if _has_keywords(combined, val_kws) and _has_number(combined):
        results["validation_present"] = True

    _checklist = [
        {
            "item": "dtype_inspection_done",
            "weight": 0.30,
            "passed": results["dtype_inspection_done"],
        },
        {
            "item": "conversion_performed",
            "weight": 0.40,
            "passed": results["conversion_performed"],
        },
        {
            "item": "validation_present",
            "weight": 0.30,
            "passed": results["validation_present"],
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
