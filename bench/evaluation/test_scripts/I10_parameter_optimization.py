"""Evaluation script for I10: Parameter Optimization (Grid Search)."""

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


def _check_parameter_api_usage(workspace_path: str, artifact_text: str) -> bool:
    """Check for GetParameter() usage in C# code."""
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
    return bool(re.search(r"getparameter\s*\(", combined))


def _check_grid_completeness(workspace_path: str, artifact_text: str) -> tuple[bool, int]:
    """Check if >= 150 parameter combinations were tested.

    Returns (passed, combo_count).
    """
    # Check for grid results file
    grid_path = os.path.join(workspace_path, "results", "grid_results.json")
    if os.path.exists(grid_path):
        try:
            with open(grid_path) as f:
                data = json.load(f)
            if isinstance(data, list):
                return len(data) >= 150, len(data)
            if isinstance(data, dict):
                return len(data) >= 150, len(data)
        except (json.JSONDecodeError, IOError):
            pass

    # Check for sweep_results.json (alternative naming)
    sweep_path = os.path.join(workspace_path, "results", "sweep_results.json")
    if os.path.exists(sweep_path):
        try:
            with open(sweep_path) as f:
                data = json.load(f)
            if isinstance(data, list):
                return len(data) >= 150, len(data)
            if isinstance(data, dict):
                return len(data) >= 150, len(data)
        except (json.JSONDecodeError, IOError):
            pass

    # Check artifact text for evidence of many runs
    combo_counts = re.findall(r"(\d+)\s*(?:combo|combination|config|run|param)", artifact_text)
    if combo_counts:
        max_count = max(int(n) for n in combo_counts)
        return max_count >= 150, max_count

    return False, 0


def _check_results_structure(workspace_path: str, artifact_text: str) -> bool:
    """Check for structured results with params + metrics per combo."""
    # Check grid_results.json or sweep_results.json
    for fname in ["grid_results.json", "sweep_results.json", "optimization_results.json"]:
        results_path = os.path.join(workspace_path, "results", fname)
        if os.path.exists(results_path):
            try:
                with open(results_path) as f:
                    data = json.load(f)
                # Check structure: should have both params and metrics
                if isinstance(data, list) and len(data) > 0:
                    sample = data[0]
                    has_params = any(k in str(sample).lower() for k in ["fast", "slow", "threshold", "param"])
                    has_metrics = any(k in str(sample).lower() for k in ["sharpe", "return", "drawdown"])
                    if has_params and has_metrics:
                        return True
                elif isinstance(data, dict):
                    sample_str = str(data).lower()
                    has_params = any(k in sample_str for k in ["fast", "slow", "threshold"])
                    has_metrics = any(k in sample_str for k in ["sharpe", "return", "drawdown"])
                    if has_params and has_metrics:
                        return True
            except (json.JSONDecodeError, IOError):
                pass

    # Check artifact text
    return has_regex(artifact_text, [
        r"fast.*slow.*(?:sharpe|return|drawdown)",
        r"(?:sharpe|return|drawdown).*fast.*slow",
        r"param.*result.*(?:sharpe|return)",
    ])


def _check_top5_identification(workspace_path: str, artifact_text: str) -> bool:
    """Check if top-5 configurations were identified."""
    # Check for top configs file
    for fname in ["top_configs.json", "best_configs.json", "top5.json"]:
        path = os.path.join(workspace_path, "results", fname)
        if os.path.exists(path):
            return True

    return has_regex(artifact_text, [
        r"(?:top|best|optimal)\s*(?:\d+\s*)?(?:config|param|combo|result)",
        r"ranked.*(?:config|param|combo)",
        r"(?:sort|rank).*(?:sharpe|return|performance)",
    ])


def _check_bayesian_bonus(artifact_text: str) -> bool:
    """Check for Bayesian optimization (Optuna, etc.) — bonus points."""
    return has_regex(artifact_text, [
        r"optuna",
        r"bayesian.*optim",
        r"gaussian.*process",
        r"tpe.*sampler",
        r"hyperopt",
    ])


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate I10 Parameter Optimization implementation.

    Checks: backtest completion, trade log, behavioral equivalence,
    parameter API usage, grid completeness, results structure,
    top-5 identification, code patterns, bayesian bonus.
    """
    results = {
        "backtest_completed": False,
        "trade_log_produced": False,
        "signal_agreement": False,
        "position_overlap": False,
        "performance_match": False,
        "trade_count_match": False,
        "parameter_api_usage": False,
        "grid_completeness": False,
        "results_structure": False,
        "top5_identification": False,
        "code_patterns": False,
        "bayesian_bonus": False,
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

    # ── Behavioral scoring (best config) ──
    behavioral = compute_behavioral_score("I10", workspace_path, resolution="daily", run_id="f15_s20_t0005")
    results["signal_agreement"] = behavioral.signal_score >= 0.60
    results["position_overlap"] = behavioral.position_score >= 0.60
    results["performance_match"] = behavioral.performance_score >= 0.50
    results["trade_count_match"] = behavioral.trade_score >= 0.40

    # ── Parameter API usage (GetParameter in code) ──
    results["parameter_api_usage"] = _check_parameter_api_usage(
        workspace_path, artifact_text
    )

    # ── Grid completeness (>=150 combos tested) ──
    grid_passed, grid_count = _check_grid_completeness(workspace_path, artifact_text)
    results["grid_completeness"] = grid_passed

    # ── Results structure (params + Sharpe/return/drawdown per combo) ──
    results["results_structure"] = _check_results_structure(workspace_path, artifact_text)

    # ── Top 5 identification ──
    results["top5_identification"] = _check_top5_identification(
        workspace_path, artifact_text
    )

    # ── Code patterns ──
    expected_patterns = ["GetParameter", "EMA(", "AlphaModel", "SetWarmUp"]
    cs_matches = check_csharp_patterns(workspace_path, expected_patterns)
    results["code_patterns"] = sum(cs_matches.values()) >= 3

    # ── Bayesian bonus (optional Optuna convergence) ──
    results["bayesian_bonus"] = _check_bayesian_bonus(artifact_text)

    # ── Scoring ──
    _checklist = [
        {"item": "backtest_completed",    "weight": 0.05, "passed": results["backtest_completed"]},
        {"item": "trade_log_produced",    "weight": 0.05, "passed": results["trade_log_produced"]},
        {"item": "behavioral_score",      "weight": 0.25, "score": behavioral.composite_score},
        {"item": "parameter_api_usage",   "weight": 0.15, "passed": results["parameter_api_usage"]},
        {"item": "grid_completeness",     "weight": 0.15, "passed": results["grid_completeness"]},
        {"item": "results_structure",     "weight": 0.10, "passed": results["results_structure"]},
        {"item": "top5_identification",   "weight": 0.10, "passed": results["top5_identification"]},
        {"item": "code_patterns",         "weight": 0.05, "passed": results["code_patterns"]},
        {"item": "bayesian_bonus",        "weight": 0.10, "passed": results["bayesian_bonus"]},
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
    results["_grid_count"] = grid_count
    results["behavioral_composite"] = round(behavioral.composite_score, 4)
    results["behavioral_layers"] = behavioral.layers_available
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
