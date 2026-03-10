"""Evaluation script for I09: Risk Management Models."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _implementation_check import (
    collect_artifact_text,
    check_csharp_patterns,
    collect_lean_results,
    compute_behavioral_score,
    has_any,
    has_regex,
    load_agent_trades,
    load_reference_trades,
    match_trades,
)
from _data_source_check import verify_data_source


def _check_framework_architecture(workspace_path: str, artifact_text: str) -> dict:
    """Check for Algorithm Framework wiring patterns."""
    checks = {
        "has_set_alpha": False,
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

    combined = (cs_text + "\n" + artifact_text).lower()

    checks["has_set_alpha"] = bool(re.search(r"setalpha\s*\(", combined))
    checks["has_set_portfolio"] = bool(re.search(r"setportfolioconstruction\s*\(", combined))
    checks["has_set_execution"] = bool(re.search(r"setexecution\s*\(", combined))

    return checks


def _check_risk_model_registration(workspace_path: str, artifact_text: str) -> bool:
    """Check for SetRiskManagement or AddRiskManagement usage."""
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
    return bool(re.search(r"(?:set|add)riskmanagement\s*\(", combined))


def _check_custom_risk_model(workspace_path: str, artifact_text: str) -> bool:
    """Check for a custom RiskManagementModel class."""
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

    has_class = bool(re.search(
        r"class\s+\w+\s*:\s*RiskManagementModel", combined, re.IGNORECASE
    ))
    has_manage_risk = bool(re.search(
        r"override\s+.*ManageRisk\s*\(", combined, re.IGNORECASE
    ))

    return has_class and has_manage_risk


def _check_three_run_comparison(workspace_path: str, artifact_text: str) -> bool:
    """Check for evidence of three risk configuration runs."""
    # Check for structured comparison output
    comparison_path = os.path.join(workspace_path, "results", "comparison.json")
    if os.path.exists(comparison_path):
        try:
            with open(comparison_path) as f:
                data = json.load(f)
            if isinstance(data, (list, dict)) and len(data) >= 3:
                return True
        except (json.JSONDecodeError, IOError):
            pass

    # Check for evidence in artifacts
    config_mentions = 0
    for keyword in ["norisk", "no_risk", "no risk", "none"]:
        if keyword in artifact_text:
            config_mentions += 1
            break
    for keyword in ["builtin", "built-in", "built_in"]:
        if keyword in artifact_text:
            config_mentions += 1
            break
    for keyword in ["custom"]:
        if keyword in artifact_text:
            config_mentions += 1
            break

    if config_mentions >= 3:
        return True

    # Check for GetParameter usage with risk_config
    if has_regex(artifact_text, [r"getparameter.*risk.?config"]):
        return True

    return False


def _check_drawdown_improvement(artifact_text: str) -> bool:
    """Check for evidence that risk management reduced drawdown."""
    return has_regex(artifact_text, [
        r"drawdown.*(?:reduc|improv|lower|less|decreas)",
        r"(?:reduc|improv|lower|less|decreas).*drawdown",
        r"risk.*(?:manag|control).*(?:help|improv|reduc)",
        r"max.?drawdown.*\d+.*%",
    ])


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate I09 Risk Management Models implementation.

    Checks: backtest completion, trade log, behavioral equivalence,
    framework architecture, risk model registration, custom risk model,
    three-run comparison, drawdown improvement, risk event logging.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,
        "position_overlap": False,
        "performance_match": False,
        "trade_count_match": False,
        "framework_architecture": False,
        "risk_model_registration": False,
        "custom_risk_model": False,
        "three_run_comparison": False,
        "drawdown_improvement": False,
        "risk_event_log": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)

    # ── Backtest completion ──
    lean_results = collect_lean_results(workspace_path)
    if lean_results is not None:
        results["backtest_completed"] = True
    elif has_any(artifact_text, ["algorithm completed", "total trades", "backtest complete"]):
        results["backtest_completed"] = True

    # ── Trade log ──
    agent_trades = load_agent_trades(workspace_path)
    if agent_trades:
        results["trade_log_produced"] = True

    # ── Behavioral scoring (uses "builtin" run for matching) ──
    behavioral = compute_behavioral_score("I09", workspace_path, resolution="hour", run_id="builtin")
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    results["position_overlap"] = behavioral.position_score >= 0.60
    results["performance_match"] = behavioral.performance_score >= 0.50
    results["trade_count_match"] = behavioral.trade_score >= 0.40

    # ── Framework architecture ──
    arch = _check_framework_architecture(workspace_path, artifact_text)
    results["framework_architecture"] = all(arch.values())

    # ── Risk model registration ──
    results["risk_model_registration"] = _check_risk_model_registration(
        workspace_path, artifact_text
    )

    # ── Custom risk model (class inherits RiskManagementModel) ──
    results["custom_risk_model"] = _check_custom_risk_model(workspace_path, artifact_text)

    # ── Three-run comparison ──
    results["three_run_comparison"] = _check_three_run_comparison(
        workspace_path, artifact_text
    )

    # ── Drawdown improvement ──
    results["drawdown_improvement"] = _check_drawdown_improvement(artifact_text)

    # ── Risk event log ──
    results["risk_event_log"] = has_regex(artifact_text, [
        r"risk.*(?:trigger|event|scale|limit|exposure)",
        r"(?:trailing|stop|drawdown).*(?:hit|trigger|exceed)",
        r"manageri?sk",
    ])

    # ── Scoring ──
    _checklist = [
        {"item": "backtest_completed",      "weight": 0.05, "passed": results["backtest_completed"]},
        {"item": "trade_log_produced",      "weight": 0.05, "passed": results["trade_log_produced"]},
        {"item": "behavioral_score",        "weight": 0.35, "score": behavioral.composite_score},
        {"item": "framework_architecture",  "weight": 0.10, "passed": results["framework_architecture"]},
        {"item": "risk_model_registration", "weight": 0.10, "passed": results["risk_model_registration"]},
        {"item": "custom_risk_model",       "weight": 0.10, "passed": results["custom_risk_model"]},
        {"item": "three_run_comparison",    "weight": 0.10, "passed": results["three_run_comparison"]},
        {"item": "drawdown_improvement",    "weight": 0.10, "passed": results["drawdown_improvement"]},
        {"item": "risk_event_log",          "weight": 0.05, "passed": results["risk_event_log"]},
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
