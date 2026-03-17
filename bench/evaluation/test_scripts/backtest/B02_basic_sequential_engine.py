"""Evaluation script for B02: Basic sequential backtest engine."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.backtest_engine_check import (
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
from common.data_source_check import verify_data_source


def _tool_log_code_text(tool_logs: list) -> str:
    """Extract Python code from tool logs (heredoc, inline, file_write)."""
    parts = []
    for log in tool_logs or []:
        name = log.name or ""
        if name == "shell_exec":
            cmd = str(log.args.get("command", ""))
            parts.append(cmd.lower())
            result = str(log.result or "")
            parts.append(result.lower())
        elif name == "file_write":
            content = str(log.args.get("content", ""))
            parts.append(content.lower())
    return "\n".join(parts)


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

    # Also extract code from tool logs (for heredoc/inline Python)
    tool_code = _tool_log_code_text(tool_logs)
    all_code = code_text + "\n" + tool_code

    # Data layer: check workspace files first, then fall back to tool log code
    results["data_layer_present"] = source_has_component(
        py_records,
        name_keywords=["data", "loader", "feed", "replay", "handler", "stream"],
        code_patterns=[
            r"read_csv\(",
            r"\bload_.*data\b",
            r"\breplay\b",
            r"\byield\b.*bar",
        ],
    ) or has_any(
        all_code,
        [
            "read_csv",
            "load_data",
            "data_handler",
            "data_loader",
            "datafeed",
            "data_feed",
            "bar_generator",
        ],
    )

    results["engine_layer_present"] = source_has_component(
        py_records,
        name_keywords=[
            "engine",
            "backtest",
            "backtester",
            "runner",
            "broker",
            "portfolio",
        ],
        code_patterns=[
            r"\bposition\b",
            r"\bpnl\b",
            r"\btrade(?:_log|s)?\b",
            r"\bcash\b",
        ],
    ) or has_regex(
        all_code,
        [
            r"class\s+\w*(?:engine|backtest|broker|portfolio)\w*",
            r"def\s+\w*(?:run_backtest|execute_trades|process_bar)\w*",
            r"\bposition\b.*\bpnl\b|\bpnl\b.*\bposition\b",
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
    ) or has_regex(
        all_code,
        [
            r"class\s+\w*(?:strategy|signal|crossover)\w*",
            r"def\s+\w*(?:on_bar|generate_signal|on_data)\w*",
            r"\bsma\b.*\bcrossover\b|\bcrossover\b.*\bsma\b",
            r"\bfast_?\w*\b.*\bslow_?\w*\b",
            r"\bmoving.{0,10}average\b",
        ],
    )

    results["sequential_replay_present"] = (
        has_sequential_replay(code_text)
        and has_any(code_text, ["bar", "bars", "on_bar", "position"])
    ) or (
        has_sequential_replay(all_code)
        and has_any(all_code, ["bar", "bars", "on_bar", "position"])
    )

    results["strategy_isolated_from_data_io"] = strategy_isolated_from_data_io(
        py_records
    )
    # If no workspace .py files but agent wrote strategy inline, give benefit of doubt
    # when strategy and engine are detected in tool logs
    if (
        not py_records
        and results["strategy_layer_present"]
        and results["engine_layer_present"]
    ):
        results["strategy_isolated_from_data_io"] = True

    results["performance_summary_present"] = has_metric_numbers(
        artifact_text,
        [
            [
                "total return",
                "annual return",
                "annualized return",
                "sharpe",
                "max drawdown",
                "total_return",
                "annual_return",
                "sharpe_ratio",
                "max_drawdown",
            ],
            [
                "trades",
                "trade count",
                "entries",
                "exits",
                "trade_count",
                "total_trades",
                "num_trades",
            ],
        ],
    )

    results["architecture_rationale_present"] = has_any(
        assistant_text,
        [
            "look-ahead",
            "lookahead",
            "look_ahead",
            "data integrity",
            "separation",
            "decoupl",
            "pluggable",
            "swap strategies",
            "three-layer",
            "three layer",
            "data handler",
            "prevent future",
            "future data",
            "no peek",
        ],
    )

    _checklist = [
        {
            "item": "data_layer_present",
            "weight": 0.15,
            "passed": results["data_layer_present"],
        },
        {
            "item": "engine_layer_present",
            "weight": 0.15,
            "passed": results["engine_layer_present"],
        },
        {
            "item": "strategy_layer_present",
            "weight": 0.15,
            "passed": results["strategy_layer_present"],
        },
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
        for key in (
            "data_layer_present",
            "engine_layer_present",
            "strategy_layer_present",
        )
        if results[key]
    )
    if component_count < 2:
        score = min(score, 0.25)
    elif component_count < 3:
        score = min(score, 0.45)
    if (
        not results["sequential_replay_present"]
        or not results["strategy_isolated_from_data_io"]
    ):
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
