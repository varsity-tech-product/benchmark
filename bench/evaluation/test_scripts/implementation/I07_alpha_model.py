"""Evaluation script for I07: Alpha Model Architecture (Framework Migration)."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.evidence_helpers import apply_data_source_cap
from common.implementation_check import (
    check_csharp_patterns,
    collect_artifact_text,
    collect_lean_results,
    compute_behavioral_score,
    compute_trial_efficiency,
    has_any,
    load_agent_trades,
)


def _check_framework_architecture(workspace_path: str, artifact_text: str) -> dict:
    """Check for Algorithm Framework wiring patterns."""
    checks = {
        "has_set_alpha": False,
        "has_set_portfolio": False,
        "has_set_execution": False,
    }

    # Check C# files
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

    combined = (cs_text + "\n" + artifact_text).lower()

    checks["has_set_alpha"] = bool(re.search(r"setalpha\s*\(", combined))
    checks["has_set_portfolio"] = bool(
        re.search(r"setportfolioconstruction\s*\(", combined)
    )
    checks["has_set_execution"] = bool(re.search(r"setexecution\s*\(", combined))

    return checks


def _check_alpha_model_class(workspace_path: str, artifact_text: str) -> dict:
    """Check for AlphaModel class definition with Update method."""
    checks = {
        "inherits_alpha_model": False,
        "has_update_method": False,
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

    checks["inherits_alpha_model"] = bool(
        re.search(r"class\s+\w+\s*:\s*AlphaModel", combined, re.IGNORECASE)
    )
    checks["has_update_method"] = bool(
        re.search(r"override\s+.*Update\s*\(", combined, re.IGNORECASE)
    )

    return checks


def _check_insight_emission(artifact_text: str) -> bool:
    """Check for Insight.Up/Down/Price emission with parameters."""
    return bool(
        re.search(r"insight\.(up|down|price)\s*\(", artifact_text, re.IGNORECASE)
    )


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate I07 Alpha Model Architecture implementation.

    Checks: backtest completion, trade log, behavioral equivalence,
    framework architecture, alpha model class, insight quality, code patterns.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,
        "position_overlap": False,
        "performance_match": False,
        "trade_count_match": False,
        "framework_architecture": False,
        "alpha_model_class": False,
        "insight_quality": False,
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

    # ── Behavioral scoring ──
    behavioral = compute_behavioral_score("I07", workspace_path, resolution="daily")
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    results["position_overlap"] = behavioral.position_score >= 0.60
    results["performance_match"] = behavioral.performance_score >= 0.50
    results["trade_count_match"] = behavioral.trade_score >= 0.40

    # ── Framework architecture (SetAlpha/SetPortfolioConstruction/SetExecution) ──
    arch = _check_framework_architecture(workspace_path, artifact_text)
    results["framework_architecture"] = all(arch.values())

    # ── Alpha model class (inherits AlphaModel, has Update) ──
    alpha = _check_alpha_model_class(workspace_path, artifact_text)
    results["alpha_model_class"] = all(alpha.values())

    # ── Insight quality (Insight.Up/Down with params) ──
    results["insight_quality"] = _check_insight_emission(artifact_text)

    # ── Code patterns ──
    expected_patterns = ["AlphaModel", "EMA(", "SetWarmUp", "Insight"]
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
            "weight": 0.40,
            "score": behavioral.composite_score,
        },
        {
            "item": "framework_architecture",
            "weight": 0.15,
            "passed": results["framework_architecture"],
        },
        {
            "item": "alpha_model_class",
            "weight": 0.10,
            "passed": results["alpha_model_class"],
        },
        {
            "item": "insight_quality",
            "weight": 0.10,
            "passed": results["insight_quality"],
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
