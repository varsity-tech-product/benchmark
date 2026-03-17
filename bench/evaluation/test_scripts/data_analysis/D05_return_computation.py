"""Evaluation script for D05: Return Computation.

QR Programmatic: checks tool outputs and workspace for evidence that
simple returns, log returns, and cumulative/annualized returns were computed.
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
        "simple_return_computed": False,
        "log_return_computed": False,
        "aggregation_demonstrated": False,
        "score": 0.0,
    }

    combined = _collect_evidence(workspace_path, tool_logs)

    # 1. Simple returns computed (0.35)
    simple_kws = [
        "pct_change",
        "/ shift",
        "/shift",
        "simple return",
        "arithmetic return",
        "daily return",
    ]
    if _has_keywords(combined, simple_kws) and _has_number(combined):
        results["simple_return_computed"] = True

    # 2. Log returns computed (0.35)
    log_kws = ["np.log", "log(", "log return", "logarithmic", "ln("]
    if _has_keywords(combined, log_kws) and _has_number(combined):
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
    if _has_keywords(combined, agg_kws) and _has_number(combined):
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
