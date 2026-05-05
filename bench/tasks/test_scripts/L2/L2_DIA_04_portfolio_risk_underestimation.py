"""Eval script for L2_DIA_04_portfolio_risk_underestimation.

Heuristic conversation-concept checker. Agent must demonstrate tail
dependence rising in crisis, show empirical VaR > parametric VaR, and
push back on 5x leverage with quantitative evidence.
"""

from __future__ import annotations

import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from common.debug_check import check_conversation_concepts  # noqa: E402
from common.evidence_helpers import apply_data_source_cap  # noqa: E402


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "tail_dependence_demonstrated": False,
        "pearson_limitation_explained": False,
        "empirical_vs_parametric_var_compared": False,
        "fat_tail_recognized": False,
        "leverage_pushback": False,
        "stress_test_recommended": False,
        "score": 0.0,
    }

    tail_dep = check_conversation_concepts(
        conversation or [],
        [
            "tail dependence",
            "tail correlation",
            "conditional correlation",
            "downside correlation",
            "crisis correlation",
            "rolling correlation",
            "down-quartile",
            "copula",
        ],
        tool_logs or [],
    )
    results["tail_dependence_demonstrated"] = tail_dep["fraction"] > 0

    pearson_lim = check_conversation_concepts(
        conversation or [],
        [
            "pearson",
            "linear correlation",
            "average dependence",
            "linear dependence",
            "underestimate",
        ],
        tool_logs or [],
    )
    results["pearson_limitation_explained"] = pearson_lim["fraction"] > 0

    var_comp = check_conversation_concepts(
        conversation or [],
        [
            "empirical var",
            "historical var",
            "parametric var",
            "multivariate normal",
            "var underestimate",
            "exceeded the parametric",
            "actual loss",
        ],
        tool_logs or [],
    )
    results["empirical_vs_parametric_var_compared"] = var_comp["fraction"] > 0

    fat_tail = check_conversation_concepts(
        conversation or [],
        [
            "fat tail",
            "heavy tail",
            "fatter than normal",
            "kurtosis",
            "leptokurtic",
            "non-normal",
            "non gaussian",
        ],
        tool_logs or [],
    )
    results["fat_tail_recognized"] = fat_tail["fraction"] > 0

    leverage = check_conversation_concepts(
        conversation or [],
        [
            "5x leverage",
            "do not lever",
            "dangerous",
            "wipeout",
            "ruin",
            "advise against",
            "would not recommend",
            "should not lever",
            "risk of ruin",
        ],
        tool_logs or [],
    )
    results["leverage_pushback"] = leverage["fraction"] > 0

    stress = check_conversation_concepts(
        conversation or [],
        [
            "stress test",
            "scenario analysis",
            "march 2020",
            "conditional on",
            "t-copula",
            "regime",
        ],
        tool_logs or [],
    )
    results["stress_test_recommended"] = stress["fraction"] > 0

    _checklist = [
        {"item": "tail_dependence_demonstrated", "weight": 0.25, "passed": results["tail_dependence_demonstrated"]},
        {"item": "pearson_limitation_explained", "weight": 0.10, "passed": results["pearson_limitation_explained"]},
        {"item": "empirical_vs_parametric_var_compared", "weight": 0.20, "passed": results["empirical_vs_parametric_var_compared"]},
        {"item": "fat_tail_recognized", "weight": 0.10, "passed": results["fat_tail_recognized"]},
        {"item": "leverage_pushback", "weight": 0.25, "passed": results["leverage_pushback"]},
        {"item": "stress_test_recommended", "weight": 0.10, "passed": results["stress_test_recommended"]},
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
