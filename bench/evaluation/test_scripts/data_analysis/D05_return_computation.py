"""Evaluation script for D05: Return Computation.

QR Programmatic: checks tool outputs and workspace for evidence that
simple returns, log returns, and cumulative/annualized returns were computed.
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
        "simple_return_computed": False,
        "log_return_computed": False,
        "aggregation_demonstrated": False,
        "score": 0.0,
    }

    combined = collect_evidence(workspace_path, tool_logs)

    # 1. Simple returns computed (0.35)
    simple_kws = [
        "pct_change",
        "/ shift",
        "/shift",
        "simple return",
        "arithmetic return",
        "daily return",
    ]
    if has_keywords(combined, simple_kws) and has_number(combined):
        results["simple_return_computed"] = True

    # 2. Log returns computed (0.35)
    log_kws = ["np.log", "log(", "log return", "logarithmic", "ln("]
    if has_keywords(combined, log_kws) and has_number(combined):
        results["log_return_computed"] = True

    # 3. Aggregation demonstrated (0.30)
    agg_kws = [
        "cumsum",
        "cumprod",
        "cumulative",
        "annualized",
        "compound",
        "annual return",
        "total return",
        "compute_statistics",
        "descriptive",
    ]
    if has_keywords(combined, agg_kws) and has_number(combined):
        results["aggregation_demonstrated"] = True

    _checklist = [
        {
            "item": "simple_return_computed",
            "weight": 0.35,
            "passed": results["simple_return_computed"],
        },
        {
            "item": "log_return_computed",
            "weight": 0.35,
            "passed": results["log_return_computed"],
        },
        {
            "item": "aggregation_demonstrated",
            "weight": 0.30,
            "passed": results["aggregation_demonstrated"],
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
