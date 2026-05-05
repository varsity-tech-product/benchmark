"""Eval script for L2_E2E_03_alpha_to_deployment_readiness.

Heuristic conversation-concept checker. Agent must push back on
"deploy tomorrow" and walk through a production readiness checklist.
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
        "rushed_deployment_pushback": False,
        "paper_trading_recommended": False,
        "kill_switch_or_position_limits": False,
        "slippage_or_execution_costs": False,
        "monitoring_or_drift_detection": False,
        "capacity_consideration": False,
        "phased_deployment_plan": False,
        "score": 0.0,
    }

    pushback = check_conversation_concepts(
        conversation or [],
        [
            "do not deploy",
            "should not deploy",
            "deploy tomorrow",
            "rushed",
            "premature",
            "not ready",
            "before going live",
            "too soon",
        ],
        tool_logs or [],
    )
    results["rushed_deployment_pushback"] = pushback["fraction"] > 0

    paper = check_conversation_concepts(
        conversation or [],
        [
            "paper trading",
            "paper trade",
            "paper-trading",
            "paper period",
            "shadow trading",
        ],
        tool_logs or [],
    )
    results["paper_trading_recommended"] = paper["fraction"] > 0

    kill_pos = check_conversation_concepts(
        conversation or [],
        [
            "kill switch",
            "kill-switch",
            "circuit breaker",
            "position limit",
            "max drawdown",
            "max-drawdown",
            "stop trading",
        ],
        tool_logs or [],
    )
    results["kill_switch_or_position_limits"] = kill_pos["fraction"] > 0

    slippage = check_conversation_concepts(
        conversation or [],
        [
            "slippage",
            "execution cost",
            "transaction cost",
            "fill quality",
            "slippage budget",
        ],
        tool_logs or [],
    )
    results["slippage_or_execution_costs"] = slippage["fraction"] > 0

    monitoring = check_conversation_concepts(
        conversation or [],
        [
            "monitoring",
            "drift detection",
            "drift",
            "live vs paper",
            "live pnl",
            "paper pnl",
            "data feed",
            "feed integrity",
            "latency",
        ],
        tool_logs or [],
    )
    results["monitoring_or_drift_detection"] = monitoring["fraction"] > 0

    capacity = check_conversation_concepts(
        conversation or [],
        [
            "capacity",
            "scale up",
            "scaling",
            "$5m",
            "5 million",
            "market impact",
        ],
        tool_logs or [],
    )
    results["capacity_consideration"] = capacity["fraction"] > 0

    phased = check_conversation_concepts(
        conversation or [],
        [
            "phased",
            "small live",
            "scale gradually",
            "ramp up",
            "rollout plan",
            "deployment plan",
            "paper -> small",
            "paper to small",
        ],
        tool_logs or [],
    )
    results["phased_deployment_plan"] = phased["fraction"] > 0

    _checklist = [
        {"item": "rushed_deployment_pushback", "weight": 0.20, "passed": results["rushed_deployment_pushback"]},
        {"item": "paper_trading_recommended", "weight": 0.15, "passed": results["paper_trading_recommended"]},
        {"item": "kill_switch_or_position_limits", "weight": 0.15, "passed": results["kill_switch_or_position_limits"]},
        {"item": "slippage_or_execution_costs", "weight": 0.10, "passed": results["slippage_or_execution_costs"]},
        {"item": "monitoring_or_drift_detection", "weight": 0.15, "passed": results["monitoring_or_drift_detection"]},
        {"item": "capacity_consideration", "weight": 0.10, "passed": results["capacity_consideration"]},
        {"item": "phased_deployment_plan", "weight": 0.15, "passed": results["phased_deployment_plan"]},
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
