"""Eval script for L2_E2E_04_strategy_ab_testing.

Heuristic conversation-concept checker. Agent must walk through paired
comparison + bootstrap CI, address minimum detectable effect, and
resist the "allocate everything to A" decision based on insufficient
evidence.
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
        "paired_comparison_explained": False,
        "bootstrap_or_resampling_proposed": False,
        "min_detectable_effect_addressed": False,
        "transaction_cost_or_turnover_normalized": False,
        "risk_adjusted_metrics_referenced": False,
        "winner_declaration_resisted": False,
        "p_hacking_warning": False,
        "score": 0.0,
    }

    paired = check_conversation_concepts(
        conversation or [],
        [
            "paired comparison",
            "paired test",
            "same date",
            "same period",
            "daily return difference",
            "return difference",
            "pairwise",
        ],
        tool_logs or [],
    )
    results["paired_comparison_explained"] = paired["fraction"] > 0

    bootstrap = check_conversation_concepts(
        conversation or [],
        [
            "bootstrap",
            "paired bootstrap",
            "resample",
            "confidence interval",
            "ci on the mean",
            "ci on the difference",
        ],
        tool_logs or [],
    )
    results["bootstrap_or_resampling_proposed"] = bootstrap["fraction"] > 0

    mde = check_conversation_concepts(
        conversation or [],
        [
            "minimum detectable",
            "mde",
            "sample size",
            "statistical power",
            "n observations",
            "underpowered",
            "not enough data",
        ],
        tool_logs or [],
    )
    results["min_detectable_effect_addressed"] = mde["fraction"] > 0

    cost = check_conversation_concepts(
        conversation or [],
        [
            "transaction cost",
            "turnover",
            "fee",
            "cost-adjusted",
            "net of fees",
            "after costs",
            "tcost",
        ],
        tool_logs or [],
    )
    results["transaction_cost_or_turnover_normalized"] = cost["fraction"] > 0

    risk_adj = check_conversation_concepts(
        conversation or [],
        [
            "sharpe",
            "drawdown",
            "max drawdown",
            "tail metric",
            "volatility",
            "risk-adjusted",
        ],
        tool_logs or [],
    )
    results["risk_adjusted_metrics_referenced"] = risk_adj["fraction"] > 0

    resist = check_conversation_concepts(
        conversation or [],
        [
            "insufficient evidence",
            "do not allocate",
            "premature",
            "noise",
            "not statistically meaningful",
            "no significant difference",
            "keep both",
            "continue parallel",
            "more data",
            "more months",
        ],
        tool_logs or [],
    )
    results["winner_declaration_resisted"] = resist["fraction"] > 0

    phacking = check_conversation_concepts(
        conversation or [],
        [
            "p-hacking",
            "p hacking",
            "repeated comparison",
            "peeking",
            "multiple looks",
            "multiple testing",
            "after each month",
        ],
        tool_logs or [],
    )
    results["p_hacking_warning"] = phacking["fraction"] > 0

    _checklist = [
        {"item": "paired_comparison_explained", "weight": 0.20, "passed": results["paired_comparison_explained"]},
        {"item": "bootstrap_or_resampling_proposed", "weight": 0.20, "passed": results["bootstrap_or_resampling_proposed"]},
        {"item": "min_detectable_effect_addressed", "weight": 0.15, "passed": results["min_detectable_effect_addressed"]},
        {"item": "transaction_cost_or_turnover_normalized", "weight": 0.10, "passed": results["transaction_cost_or_turnover_normalized"]},
        {"item": "risk_adjusted_metrics_referenced", "weight": 0.10, "passed": results["risk_adjusted_metrics_referenced"]},
        {"item": "winner_declaration_resisted", "weight": 0.20, "passed": results["winner_declaration_resisted"]},
        {"item": "p_hacking_warning", "weight": 0.05, "passed": results["p_hacking_warning"]},
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
