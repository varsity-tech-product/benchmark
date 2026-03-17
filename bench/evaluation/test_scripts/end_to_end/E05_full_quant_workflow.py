"""Evaluation script for E05: Full Quant Workflow (D + S + B + I).

Checks: pipeline planning, data exploration, signal formalization,
signal evaluation, Python backtest metrics, validation, LEAN backtest,
trade log, behavioral scoring, signal consistency, comparison discussion.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.data_source_check import verify_data_source
from common.implementation_check import (
    collect_artifact_text,
    collect_lean_results,
    compute_behavioral_score,
    load_agent_trades,
)
from common.strategy_research_check import (
    collect_artifact_text as collect_research_text,
)
from common.strategy_research_check import (
    conversation_text,
    has_any,
    has_metric_evidence,
    has_metric_numbers,
    has_signal_definition,
)


def _check_pipeline_structure(conversation: list | None) -> bool:
    """Detect whether the assistant communicated a structured plan."""
    text = conversation_text(conversation, role="assistant")
    plan_patterns = [
        r"step\s*[123]",
        r"phase\s*[123]",
        r"first.*then.*finally",
        r"pipeline.*:.*\n.*\d\.",
        r"1\.\s.*\n.*2\.\s.*\n.*3\.\s",
    ]
    return any(re.search(p, text, re.IGNORECASE | re.DOTALL) for p in plan_patterns)


def _check_signal_consistency(workspace_path: str, tool_logs: list | None) -> bool:
    """Check that momentum/ROCP params appear in both Python and C# artifacts."""
    py_text = collect_research_text(workspace_path, tool_logs)
    cs_text = ""
    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if fname.endswith(".cs"):
                    try:
                        with open(os.path.join(root, fname)) as f:
                            cs_text += f.read().lower() + "\n"
                    except (IOError, UnicodeDecodeError):
                        continue
    for log in tool_logs or []:
        for v in log.args.values():
            text = str(v).lower()
            if "rocp" in text or "rateofchange" in text or "momentum" in text:
                cs_text += text + "\n"

    py_has_signal = has_any(
        py_text, ["momentum", "rocp", "pct_change", "roc(", "rate_of_change"]
    )
    cs_has_signal = has_any(cs_text, ["rocp", "rateofchange", "momentum", "pct_change"])
    return py_has_signal and cs_has_signal


def _check_validation(artifact_text: str, assistant_text: str) -> bool:
    """Detect train/test or OOS validation patterns."""
    combined = artifact_text + "\n" + assistant_text
    patterns = [
        r"train.*test",
        r"in.sample.*out.of.sample",
        r"split_date",
        r"oos",
        r"walk.forward",
        r"validation period",
    ]
    return any(re.search(p, combined, re.IGNORECASE) for p in patterns)


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate E05: Full Quant Workflow."""
    results = {
        "pipeline_structure_present": False,
        "data_exploration_performed": False,
        "signal_formalized": False,
        "signal_evaluated": False,
        "python_backtest_metrics": False,
        "validation_performed": False,
        "lean_backtest_completed": False,
        "lean_trade_log_produced": False,
        "behavioral_score": 0.0,
        "signal_consistency": False,
        "comparison_discussed": False,
        "score": 0.0,
    }

    artifact_text = collect_research_text(workspace_path, tool_logs)
    impl_text = collect_artifact_text(workspace_path, tool_logs)
    assistant_text = conversation_text(conversation, role="assistant")

    # ── 1. Pipeline structure (0.05) ──
    results["pipeline_structure_present"] = _check_pipeline_structure(conversation)

    # ── 2. Data exploration (0.07) ──
    results["data_exploration_performed"] = has_any(
        artifact_text,
        [
            "describe",
            "distribution",
            "hist",
            "autocorr",
            "rolling_std",
            "value_counts",
            "skew",
            "volatility",
        ],
    )

    # ── 3. Signal formalized (0.10) ──
    has_def = has_signal_definition(artifact_text)
    has_momentum = has_any(
        artifact_text,
        ["momentum", "rocp", "pct_change", "rate_of_change", "trailing_return"],
    )
    results["signal_formalized"] = has_def or has_momentum

    # ── 4. Signal evaluated (0.08) ──
    results["signal_evaluated"] = has_metric_evidence(
        artifact_text
    ) or has_metric_numbers(
        artifact_text,
        [
            ["ic", "information_coefficient", "spearman"],
            ["quantile", "hit_rate", "win_rate"],
        ],
    )

    # ── 5. Python backtest metrics (0.10) ──
    results["python_backtest_metrics"] = has_metric_numbers(
        artifact_text,
        [["sharpe"], ["return"], ["drawdown"]],
    )

    # ── 6. Validation performed (0.10) ──
    results["validation_performed"] = _check_validation(artifact_text, assistant_text)

    # ── 7. LEAN backtest completed (0.15) ──
    lean_results = collect_lean_results(workspace_path)
    if lean_results is not None:
        results["lean_backtest_completed"] = True
    elif has_any(
        impl_text,
        ["algorithm completed", "total trades", "backtest complete", "e05 complete"],
    ):
        results["lean_backtest_completed"] = True

    # ── 8. LEAN trade log produced (0.05) ──
    agent_trades = load_agent_trades(workspace_path)
    if agent_trades:
        results["lean_trade_log_produced"] = True

    # ── 9. Behavioral score (0.15) ──
    behavioral = compute_behavioral_score("E05", workspace_path, resolution="daily")
    results["behavioral_score"] = round(behavioral.composite_score, 4)

    # ── 10. Signal consistency (0.08) ──
    results["signal_consistency"] = _check_signal_consistency(workspace_path, tool_logs)

    # ── 11. Comparison discussed (0.07) ──
    results["comparison_discussed"] = has_any(
        assistant_text,
        [
            "python vs lean",
            "comparison",
            "discrepancy",
            "both backends",
            "results differ",
            "results match",
        ],
    )

    # ── Scoring ──
    _checklist = [
        {
            "item": "pipeline_structure_present",
            "weight": 0.05,
            "passed": results["pipeline_structure_present"],
        },
        {
            "item": "data_exploration_performed",
            "weight": 0.07,
            "passed": results["data_exploration_performed"],
        },
        {
            "item": "signal_formalized",
            "weight": 0.10,
            "passed": results["signal_formalized"],
        },
        {
            "item": "signal_evaluated",
            "weight": 0.08,
            "passed": results["signal_evaluated"],
        },
        {
            "item": "python_backtest_metrics",
            "weight": 0.10,
            "passed": results["python_backtest_metrics"],
        },
        {
            "item": "validation_performed",
            "weight": 0.10,
            "passed": results["validation_performed"],
        },
        {
            "item": "lean_backtest_completed",
            "weight": 0.15,
            "passed": results["lean_backtest_completed"],
        },
        {
            "item": "lean_trade_log_produced",
            "weight": 0.05,
            "passed": results["lean_trade_log_produced"],
        },
        {
            "item": "behavioral_score",
            "weight": 0.15,
            "score": behavioral.composite_score,
        },
        {
            "item": "signal_consistency",
            "weight": 0.08,
            "passed": results["signal_consistency"],
        },
        {
            "item": "comparison_discussed",
            "weight": 0.07,
            "passed": results["comparison_discussed"],
        },
    ]
    score = sum(
        c["weight"] * c.get("score", 1.0 if c.get("passed") else 0.0)
        for c in _checklist
    )

    # ── Gates ──
    if not results["lean_backtest_completed"]:
        score = min(score, 0.35)
    if not results["python_backtest_metrics"]:
        score = min(score, 0.40)
    if not results["signal_formalized"]:
        score = min(score, 0.30)

    # ── Data source verification ──
    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["behavioral_composite"] = round(behavioral.composite_score, 4)
    results["behavioral_layers"] = behavioral.layers_available
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
