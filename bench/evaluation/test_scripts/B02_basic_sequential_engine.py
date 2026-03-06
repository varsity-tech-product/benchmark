"""Evaluation script for B02: Basic sequential backtest engine."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _backtest_engine_check import (
    collect_artifact_text,
    conversation_text,
    has_any,
    has_metric_numbers,
    has_regex,
    has_sequential_replay,
    python_code_text,
    python_source_records,
    source_has_component,
    strategy_isolated_from_data_io,
)
from _data_source_check import verify_data_source


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    *,
    data_files: list[str] = None,
) -> dict:
    results = {
        "data_layer_present": False,
        "engine_layer_present": False,
        "strategy_layer_present": False,
        "sequential_replay_present": False,
        "strategy_isolated_from_data_io": False,
        "performance_summary_present": False,
        "architecture_rationale_present": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    assistant_text = conversation_text(conversation, role="assistant")
    py_records = python_source_records(workspace_path)
    code_text = python_code_text(py_records)

    results["data_layer_present"] = source_has_component(
        py_records,
        name_keywords=["data", "loader", "feed", "replay", "handler", "stream"],
        code_patterns=[
            r"read_csv\(",
            r"\bload_.*data\b",
            r"\breplay\b",
            r"\byield\b.*bar",
        ],
    )
    results["engine_layer_present"] = source_has_component(
        py_records,
        name_keywords=["engine", "backtest", "backtester", "runner", "broker", "portfolio"],
        code_patterns=[
            r"\bposition\b",
            r"\bpnl\b",
            r"\btrade(?:_log|s)?\b",
            r"\bcash\b",
        ],
    )
    results["strategy_layer_present"] = source_has_component(
        py_records,
        name_keywords=["strategy", "signal", "crossover", "on_bar"],
        code_patterns=[
            r"\bon_bar\s*\(",
            r"\bgenerate_signal\s*\(",
            r"\bfast\b.*\bslow\b",
            r"\bmoving average\b",
        ],
    )
    results["sequential_replay_present"] = has_sequential_replay(code_text) and has_any(
        code_text, ["bar", "bars", "on_bar", "position"]
    )
    results["strategy_isolated_from_data_io"] = strategy_isolated_from_data_io(py_records)
    results["performance_summary_present"] = has_metric_numbers(
        artifact_text,
        [
            ["total return", "annual return", "annualized return", "sharpe", "max drawdown"],
            ["trades", "trade count", "entries", "exits"],
        ],
    )
    results["architecture_rationale_present"] = has_any(
        assistant_text,
        [
            "look-ahead",
            "lookahead",
            "data integrity",
            "separation",
            "decoupl",
            "pluggable",
            "swap strategies",
        ],
    )

    _checklist = [
        {"item": "data_layer_present", "weight": 0.15, "passed": results["data_layer_present"]},
        {"item": "engine_layer_present", "weight": 0.15, "passed": results["engine_layer_present"]},
        {"item": "strategy_layer_present", "weight": 0.15, "passed": results["strategy_layer_present"]},
        {
            "item": "sequential_replay_present",
            "weight": 0.15,
            "passed": results["sequential_replay_present"],
        },
        {
            "item": "strategy_isolated_from_data_io",
            "weight": 0.15,
            "passed": results["strategy_isolated_from_data_io"],
        },
        {
            "item": "performance_summary_present",
            "weight": 0.15,
            "passed": results["performance_summary_present"],
        },
        {
            "item": "architecture_rationale_present",
            "weight": 0.10,
            "passed": results["architecture_rationale_present"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    component_count = sum(
        1
        for key in ("data_layer_present", "engine_layer_present", "strategy_layer_present")
        if results[key]
    )
    if component_count < 2:
        score = min(score, 0.25)
    elif component_count < 3:
        score = min(score, 0.45)
    if not results["sequential_replay_present"] or not results["strategy_isolated_from_data_io"]:
        score = min(score, 0.60)
    if not results["performance_summary_present"]:
        score = min(score, 0.70)

    if data_files:
        ds = verify_data_source(tool_logs or [], data_files)
        results["data_source_verified"] = ds["verified"]
        results["data_source_fraction"] = ds["fraction"]
        if not ds["verified"]:
            score *= max(0.25, ds["fraction"])

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
