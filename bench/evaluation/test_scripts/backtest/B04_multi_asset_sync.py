"""Evaluation script for B04: Multi-asset synchronized replay."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.backtest_engine_check import (
    collect_artifact_text,
    has_any,
    has_metric_numbers,
    has_regex,
    has_sequential_replay,
    python_code_text,
    python_source_records,
    tool_called,
    workspace_has_csv_column_group,
)
from common.evidence_helpers import apply_data_source_cap


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
        "multi_asset_data_loaded": False,
        "timestamp_alignment_present": False,
        "synchronized_replay_present": False,
        "per_asset_accounting_present": False,
        "ratio_strategy_present": False,
        "combined_metrics_present": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    py_records = python_source_records(workspace_path)
    code_text = python_code_text(py_records)

    # Also extract code from tool logs (for heredoc/inline Python)
    tool_code = _tool_log_code_text(tool_logs)
    all_code = code_text + "\n" + tool_code

    has_btc = has_any(artifact_text, ["btcusdt", "btc"])
    has_eth = has_any(artifact_text, ["ethusdt", "eth/", "eth_", "eth"])
    results["multi_asset_data_loaded"] = has_btc and has_eth

    results["timestamp_alignment_present"] = (
        has_regex(
            all_code,
            [
                r"merge\(",
                r"merge_asof\(",
                r"\.join\(",
                r"\.align\(",
                r"\.reindex\(",
                r"\bintersection\b",
                r"timestamp",
                r"set_index\([\"']timestamp[\"']\)",
            ],
        )
        and has_any(all_code, ["dropna", "sort_index", "timestamp", "datetime"])
    ) or tool_called(tool_logs, "align_timeseries")

    multi_asset_state_present = has_any(
        all_code,
        [
            "current_bars",
            "bar_tuple",
            "bar_pair",
            "aligned_bars",
            "bars_by_symbol",
            "asset_bars",
            "bar_dict",
            "snapshot",
            "market_state",
            "for timestamp in",
        ],
    ) or has_regex(
        all_code,
        [
            r"yield\s+\(",
            r"yield\s+\{",
            r"yield\s+\w*(?:snapshot|bars|state|dict)",
            r"on_bar\s*\([^)]*(?:bars|snapshot|state|dict)",
            r"\{[^}]{0,120}[\"'](?:btc|btcusdt)[\"'][^}]{0,120}[\"'](?:eth|ethusdt)[\"']",
            r"for\s+\w+\s+in\s+\w*timestamps",
            r"for\s+\w+\s+in\s+\w+\.index",
        ],
    )
    results["synchronized_replay_present"] = (
        has_sequential_replay(all_code) and multi_asset_state_present
    )

    results["per_asset_accounting_present"] = has_regex(
        all_code,
        [
            r"positions?\s*=\s*\{",
            r"pnl\s*=\s*\{",
            r"trades?\s*=\s*\{",
            r"positions?\[[\"'](?:btc|btcusdt|eth|ethusdt)[\"']\]",
        ],
    ) or workspace_has_csv_column_group(
        workspace_path,
        [
            ["asset", "symbol"],
            ["position", "qty"],
            ["pnl", "realized_pnl", "unrealized_pnl"],
        ],
    )

    results["ratio_strategy_present"] = has_any(
        artifact_text,
        [
            "ratio",
            "spread",
            "mean reversion",
            "z-score",
            "btc/eth",
            "hedge ratio",
        ],
    )

    results["combined_metrics_present"] = has_any(
        artifact_text, ["portfolio", "combined", "aggregate"]
    ) and has_metric_numbers(
        artifact_text,
        [
            ["sharpe", "total return", "annualized return", "max drawdown"],
            ["btcusdt", "ethusdt", "eth/", "eth_"],
        ],
    )

    _checklist = [
        {
            "item": "multi_asset_data_loaded",
            "weight": 0.15,
            "passed": results["multi_asset_data_loaded"],
        },
        {
            "item": "timestamp_alignment_present",
            "weight": 0.20,
            "passed": results["timestamp_alignment_present"],
        },
        {
            "item": "synchronized_replay_present",
            "weight": 0.15,
            "passed": results["synchronized_replay_present"],
        },
        {
            "item": "per_asset_accounting_present",
            "weight": 0.20,
            "passed": results["per_asset_accounting_present"],
        },
        {
            "item": "ratio_strategy_present",
            "weight": 0.15,
            "passed": results["ratio_strategy_present"],
        },
        {
            "item": "combined_metrics_present",
            "weight": 0.15,
            "passed": results["combined_metrics_present"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if (
        not results["timestamp_alignment_present"]
        or not results["per_asset_accounting_present"]
    ):
        score = min(score, 0.35)
    if (
        not results["synchronized_replay_present"]
        or not results["combined_metrics_present"]
    ):
        score = min(score, 0.55)

    if data_files:
        score = apply_data_source_cap(score, results, tool_logs, data_files)

    results["_checklist"] = _checklist
    results["score"] = round(score, 2)
    return results


if __name__ == "__main__":
    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
