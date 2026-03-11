"""Evaluation script for E02: Research to Implementation (S + B + I).

Checks: pipeline planning, Python BB signal, Python metrics, LEAN backtest,
trade log, behavioral scoring, signal consistency, comparison discussion,
code modularity.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _strategy_research_check import (
    collect_artifact_text as collect_research_text,
    conversation_text,
    has_any,
    has_metric_numbers,
)
from _implementation_check import (
    collect_artifact_text,
    collect_lean_results,
    compute_behavioral_score,
    load_agent_trades,
)
from _data_source_check import verify_data_source


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
    """Check that BB parameters (20, 2) appear in both Python and C# artifacts."""
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
    # Also check tool logs for C# code written
    for log in tool_logs or []:
        for v in log.args.values():
            text = str(v).lower()
            if "bollingerbands" in text or "bb(" in text:
                cs_text += text + "\n"

    py_has_bb = has_any(py_text, ["bollinger", "rolling_std", "bb_", "lower_band", "bb(20"])
    cs_has_bb = has_any(cs_text, ["bollingerbands", "bb(", "bb_period", "lowerband", "middleband"])
    return py_has_bb and cs_has_bb


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    """Evaluate E02: Research to Implementation."""
    results = {
        "pipeline_structure_present": False,
        "signal_defined_in_python": False,
        "python_backtest_produces_metrics": False,
        "lean_backtest_completed": False,
        "lean_trade_log_produced": False,
        "behavioral_score": 0.0,
        "signal_consistency": False,
        "comparison_discussed": False,
        "code_is_modular": False,
        "score": 0.0,
    }

    artifact_text = collect_research_text(workspace_path, tool_logs)
    impl_text = collect_artifact_text(workspace_path, tool_logs)
    assistant_text = conversation_text(conversation, role="assistant")

    # ── 1. Pipeline structure (0.05) ──
    results["pipeline_structure_present"] = _check_pipeline_structure(conversation)

    # ── 2. Signal defined in Python (0.15) ──
    results["signal_defined_in_python"] = has_any(
        artifact_text,
        ["bollinger", "rolling_std", "bb_", "lower_band", "upper_band", "rolling(20"],
    )

    # ── 3. Python backtest produces metrics (0.15) ──
    results["python_backtest_produces_metrics"] = has_metric_numbers(
        artifact_text,
        [["sharpe"], ["return"], ["drawdown"]],
    )

    # ── 4. LEAN backtest completed (0.20) ──
    lean_results = collect_lean_results(workspace_path)
    if lean_results is not None:
        results["lean_backtest_completed"] = True
    elif has_any(impl_text, ["algorithm completed", "total trades", "backtest complete", "e02 complete"]):
        results["lean_backtest_completed"] = True

    # ── 5. LEAN trade log produced (0.05) ──
    agent_trades = load_agent_trades(workspace_path)
    if agent_trades:
        results["lean_trade_log_produced"] = True

    # ── 6. Behavioral score (0.20) ──
    behavioral = compute_behavioral_score("E02", workspace_path, resolution="daily")
    results["behavioral_score"] = round(behavioral.composite_score, 4)

    # ── 7. Signal consistency (0.05) ──
    results["signal_consistency"] = _check_signal_consistency(workspace_path, tool_logs)

    # ── 8. Comparison discussed (0.10) ──
    results["comparison_discussed"] = has_any(
        assistant_text,
        ["python vs lean", "comparison", "discrepancy", "both backends", "results differ", "results match"],
    )

    # ── 9. Code is modular (0.05) ──
    py_files = []
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if fname.endswith(".py"):
                try:
                    with open(os.path.join(workspace_path, fname)) as f:
                        py_files.append(f.read())
                except (IOError, UnicodeDecodeError):
                    pass

    if len(py_files) >= 2:
        results["code_is_modular"] = True
    elif len(py_files) == 1:
        func_defs = re.findall(r"^def \w+\(", py_files[0], re.MULTILINE)
        if len(func_defs) >= 3:
            results["code_is_modular"] = True

    # ── Scoring ──
    _checklist = [
        {"item": "pipeline_structure_present",       "weight": 0.05, "passed": results["pipeline_structure_present"]},
        {"item": "signal_defined_in_python",         "weight": 0.15, "passed": results["signal_defined_in_python"]},
        {"item": "python_backtest_produces_metrics",  "weight": 0.15, "passed": results["python_backtest_produces_metrics"]},
        {"item": "lean_backtest_completed",           "weight": 0.20, "passed": results["lean_backtest_completed"]},
        {"item": "lean_trade_log_produced",           "weight": 0.05, "passed": results["lean_trade_log_produced"]},
        {"item": "behavioral_score",                  "weight": 0.20, "score": behavioral.composite_score},
        {"item": "signal_consistency",                "weight": 0.05, "passed": results["signal_consistency"]},
        {"item": "comparison_discussed",              "weight": 0.10, "passed": results["comparison_discussed"]},
        {"item": "code_is_modular",                   "weight": 0.05, "passed": results["code_is_modular"]},
    ]
    score = sum(
        c["weight"] * c.get("score", 1.0 if c.get("passed") else 0.0)
        for c in _checklist
    )

    # ── Gates ──
    if not results["lean_backtest_completed"]:
        score = min(score, 0.30)
    if not results["python_backtest_produces_metrics"]:
        score = min(score, 0.40)

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
