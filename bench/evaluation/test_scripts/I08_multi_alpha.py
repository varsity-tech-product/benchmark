"""Evaluation script for I08: Multi-Alpha Portfolio Composition."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _data_source_check import apply_data_source_cap
from _implementation_check import (
    check_csharp_patterns,
    collect_artifact_text,
    collect_lean_results,
    compute_behavioral_score,
    compute_trial_efficiency,
    has_any,
    has_regex,
    load_agent_trades,
)


def _check_multi_alpha_architecture(workspace_path: str, artifact_text: str) -> dict:
    """Check for multiple AlphaModel classes and AddAlpha usage."""
    checks = {
        "has_add_alpha": False,
        "alpha_model_count": 0,
        "has_set_portfolio": False,
        "has_set_execution": False,
    }

    cs_text = ""
    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if fname.endswith(".cs"):
                    try:
                        with open(os.path.join(root, fname)) as f:
                            cs_text += f.read() + "\n"
                    except (IOError, UnicodeDecodeError):
                        continue

    combined = cs_text + "\n" + artifact_text
    combined_lower = combined.lower()

    # Count AddAlpha calls
    add_alpha_calls = re.findall(r"addalpha\s*\(", combined_lower)
    checks["has_add_alpha"] = len(add_alpha_calls) >= 2

    # Count AlphaModel subclasses
    alpha_classes = re.findall(r"class\s+\w+\s*:\s*AlphaModel", combined, re.IGNORECASE)
    checks["alpha_model_count"] = len(alpha_classes)

    checks["has_set_portfolio"] = bool(
        re.search(r"setportfolioconstruction\s*\(", combined_lower)
    )
    checks["has_set_execution"] = bool(re.search(r"setexecution\s*\(", combined_lower))

    return checks


def _check_portfolio_model_comparison(workspace_path: str, artifact_text: str) -> bool:
    """Check if two portfolio model configurations were compared."""
    # Check for comparison artifacts
    comparison_path = os.path.join(workspace_path, "results", "comparison.json")
    if os.path.exists(comparison_path):
        return True

    # Check for evidence of two runs with different portfolio models
    has_insight_weighting = has_regex(
        artifact_text,
        [
            r"insightweighting",
            r"insight.?weighting",
        ],
    )
    has_equal_weighting = has_regex(
        artifact_text,
        [
            r"equalweighting",
            r"equal.?weighting",
        ],
    )

    if has_insight_weighting and has_equal_weighting:
        return True

    # Check for GetParameter usage for portfolio_model
    if has_regex(artifact_text, [r"getparameter.*portfolio.?model"]):
        return True

    return False


def _check_universe_coverage(artifact_text: str) -> bool:
    """Check if >= 80 symbols were subscribed."""
    calls = re.findall(r"addcryptofuture\s*\(\s*[\"']?(\w+)", artifact_text)
    if len(set(calls)) >= 80:
        return True

    if has_regex(
        artifact_text, [r"(?:subscribed|added|universe|initialized)\D{0,30}\d{2,}"]
    ):
        nums = re.findall(
            r"(?:subscribed|added|universe|initialized)\D{0,30}(\d+)", artifact_text
        )
        if any(int(n) >= 80 for n in nums):
            return True

    return False


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate I08 Multi-Alpha Portfolio Composition implementation.

    Checks: backtest completion, trade log, behavioral equivalence,
    multi-alpha architecture, portfolio model comparison, universe coverage,
    code patterns.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,
        "position_overlap": False,
        "performance_match": False,
        "trade_count_match": False,
        "multi_alpha_architecture": False,
        "portfolio_model_comparison": False,
        "comparison_output": False,
        "universe_coverage": False,
        "code_patterns": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)

    # ── Backtest completion ──
    lean_results = collect_lean_results(workspace_path)
    if lean_results is not None:
        results["backtest_completed"] = True
    elif has_any(
        artifact_text, ["algorithm completed", "total trades", "backtest complete"]
    ):
        results["backtest_completed"] = True

    # ── Trade log ──
    agent_trades = load_agent_trades(workspace_path)
    if agent_trades:
        results["trade_log_produced"] = True

    # ── Behavioral scoring (uses equal_weighting run as primary) ──
    behavioral = compute_behavioral_score(
        "I08", workspace_path, resolution="daily", run_id="equal_weighting"
    )
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    results["position_overlap"] = behavioral.position_score >= 0.60
    results["performance_match"] = behavioral.performance_score >= 0.50
    results["trade_count_match"] = behavioral.trade_score >= 0.40

    # ── Multi-alpha architecture (>=3 AlphaModel classes + AddAlpha) ──
    arch = _check_multi_alpha_architecture(workspace_path, artifact_text)
    results["multi_alpha_architecture"] = (
        arch["has_add_alpha"]
        and arch["alpha_model_count"] >= 3
        and arch["has_set_portfolio"]
        and arch["has_set_execution"]
    )

    # ── Portfolio model comparison (2 runs, different models) ──
    results["portfolio_model_comparison"] = _check_portfolio_model_comparison(
        workspace_path, artifact_text
    )

    # ── Comparison output (structured comparison artifact) ──
    comparison_path = os.path.join(workspace_path, "results", "comparison.json")
    if os.path.exists(comparison_path):
        results["comparison_output"] = True
    elif has_regex(
        artifact_text,
        [
            r"(?:comparison|compare).*(?:sharpe|return|drawdown)",
            r"(?:sharpe|return|drawdown).*(?:comparison|compare)",
            r"insight.?weighting.*equal.?weighting",
        ],
    ):
        results["comparison_output"] = True

    # ── Universe coverage (>=80 symbols subscribed) ──
    results["universe_coverage"] = _check_universe_coverage(artifact_text)

    # ── Code patterns ──
    expected_patterns = ["AlphaModel", "AddAlpha", "Insight", "RSI("]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    results["code_patterns"] = sum(cs_matches.values()) >= 3

    # ── Trial efficiency ──
    efficiency_score = compute_trial_efficiency(workspace_path)

    # ── Scoring ──
    _checklist = [
        {
            "item": "backtest_completed",
            "weight": 0.05,
            "passed": results["backtest_completed"],
        },
        {
            "item": "trade_log_produced",
            "weight": 0.05,
            "passed": results["trade_log_produced"],
        },
        {
            "item": "behavioral_score",
            "weight": 0.30,
            "score": behavioral.composite_score,
        },
        {
            "item": "multi_alpha_architecture",
            "weight": 0.15,
            "passed": results["multi_alpha_architecture"],
        },
        {
            "item": "portfolio_model_comparison",
            "weight": 0.10,
            "passed": results["portfolio_model_comparison"],
        },
        {
            "item": "comparison_output",
            "weight": 0.10,
            "passed": results["comparison_output"],
        },
        {
            "item": "universe_coverage",
            "weight": 0.10,
            "passed": results["universe_coverage"],
        },
        {"item": "code_patterns", "weight": 0.10, "passed": results["code_patterns"]},
        {"item": "trial_efficiency", "weight": 0.05, "score": efficiency_score},
    ]
    score = sum(
        c["weight"] * c.get("score", 1.0 if c.get("passed") else 0.0)
        for c in _checklist
    )

    # ── Gates ──
    if not results["backtest_completed"]:
        score = min(score, 0.10)
    if not results["trade_log_produced"]:
        score = min(score, 0.15)

    # ── Data source verification ──
    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["behavioral_composite"] = round(behavioral.composite_score, 4)
    results["behavioral_layers"] = behavioral.layers_available
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
