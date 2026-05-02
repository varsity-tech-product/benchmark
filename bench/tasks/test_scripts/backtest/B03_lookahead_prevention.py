"""Evaluation script for B03: Look-ahead prevention and verification."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.evidence_helpers import apply_data_source_cap
from common.shared_utils import (
    collect_artifact_text,
    has_any,
    has_lookahead_verification,
    has_metric_numbers,
    has_regex,
    has_sequential_replay,
    python_code_text,
    python_source_records,
    strategy_isolated_from_data_io,
)


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
        "verification_test_exists": False,
        "restricted_strategy_interface": False,
        "sequential_replay_present": False,
        "incremental_rsi_present": False,
        "leakage_impact_demonstrated": False,
        "performance_summary_present": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    py_records = python_source_records(workspace_path)
    code_text = python_code_text(py_records)

    # Also extract code from tool logs (for heredoc/inline Python)
    tool_code = _tool_log_code_text(tool_logs)
    all_code = code_text + "\n" + tool_code

    results["verification_test_exists"] = has_lookahead_verification(
        py_records, artifact_text
    )
    results["restricted_strategy_interface"] = strategy_isolated_from_data_io(
        py_records
    )
    # If no workspace .py files but agent wrote strategy inline, give benefit of doubt
    if (
        not py_records
        and has_any(all_code, ["on_bar", "generate_signal", "strategy"])
        and has_any(all_code, ["bar", "position", "signal"])
    ):
        results["restricted_strategy_interface"] = True

    results["sequential_replay_present"] = (
        has_sequential_replay(code_text)
        and has_any(code_text, ["bar", "bars", "on_bar", "history"])
    ) or (
        has_sequential_replay(all_code)
        and has_any(all_code, ["bar", "bars", "on_bar", "history"])
    )
    results["incremental_rsi_present"] = has_any(all_code, ["rsi"]) and (
        has_any(
            all_code,
            [
                "history",
                "deque",
                "append",
                "available",
                "so_far",
                "prev_avg_gain",
                "prev_avg_loss",
                "window",
            ],
        )
        or has_regex(
            all_code,
            [
                r"iloc\[:\s*\w+\s*\+?\s*1?\]",
                r"loc\[:\s*\w+\]",
                r"close_series\s*=\s*history",
            ],
        )
    )
    comparison_keywords = [
        "leaking engine",
        "correct engine",
        "future spike",
        "spy strategy",
        "lookahead comparison",
        "leakage comparison",
        "biased backtest",
        "unbiased backtest",
        "leaky backtest",
        "leak-free backtest",
        "naive backtest",
        "clean backtest",
    ]
    comparison_patterns = [
        r"(?:biased|unbiased|naive|clean|correct|corrupted|leaky|leak[- ]free)\s+"
        r"(?:backtest|engine|results?)",
        r"(?:with|without)\s+(?:lookahead|look[- ]ahead|leak(?:age)?|future data)",
        r"(?:lookahead|look[- ]ahead|leak(?:age)?|future(?:[- ]data)?)\s*"
        r"(?:vs\.?|versus|compared to)",
        r"(?:biased|unbiased|naive|clean|correct|corrupted).{0,40}"
        r"(?:sharpe|total return|annualized return|trades|trade count|entries)",
    ]
    results["leakage_impact_demonstrated"] = (
        has_any(artifact_text, comparison_keywords)
        or has_regex(artifact_text, comparison_patterns)
    ) and has_metric_numbers(
        artifact_text,
        [
            ["sharpe", "total return", "annualized return"],
            ["trades", "trade count", "entries"],
        ],
    )
    results["performance_summary_present"] = has_metric_numbers(
        artifact_text,
        [
            ["sharpe", "total return", "annualized return", "max drawdown"],
            ["trades", "trade count", "entries"],
        ],
    )

    _checklist = [
        {
            "item": "verification_test_exists",
            "weight": 0.25,
            "passed": results["verification_test_exists"],
        },
        {
            "item": "restricted_strategy_interface",
            "weight": 0.15,
            "passed": results["restricted_strategy_interface"],
        },
        {
            "item": "sequential_replay_present",
            "weight": 0.15,
            "passed": results["sequential_replay_present"],
        },
        {
            "item": "incremental_rsi_present",
            "weight": 0.15,
            "passed": results["incremental_rsi_present"],
        },
        {
            "item": "leakage_impact_demonstrated",
            "weight": 0.15,
            "passed": results["leakage_impact_demonstrated"],
        },
        {
            "item": "performance_summary_present",
            "weight": 0.15,
            "passed": results["performance_summary_present"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if not results["verification_test_exists"]:
        score = min(score, 0.25)
    if (
        not results["restricted_strategy_interface"]
        or not results["sequential_replay_present"]
    ):
        score = min(score, 0.50)
    if not results["leakage_impact_demonstrated"]:
        score = min(score, 0.60)

    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
