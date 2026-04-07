"""Evaluation script for D03: Data Type Conversion & Validation.

QR Programmatic: checks tool outputs and workspace for evidence that
dtypes were inspected, conversions were performed, and validation was applied.
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
        "dtype_inspection_done": False,
        "conversion_performed": False,
        "validation_present": False,
        "score": 0.0,
    }

    combined = collect_evidence(workspace_path, tool_logs)

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
    if has_keywords(combined, dtype_kws):
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
    if has_keywords(combined, conv_kws):
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
    if has_keywords(combined, val_kws) and has_number(combined):
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
    score = checklist_score(_checklist)

    score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
