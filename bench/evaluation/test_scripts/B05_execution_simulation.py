"""Evaluation script for B05: Execution simulation and futures mechanics."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _backtest_engine_check import (
    collect_artifact_text,
    has_any,
    has_metric_numbers,
    has_regex,
    python_code_text,
    python_source_records,
    workspace_has_csv_column_group,
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
        "slippage_model_present": False,
        "fee_model_present": False,
        "funding_model_present": False,
        "execution_artifact_present": False,
        "gross_vs_net_breakdown_present": False,
        "performance_summary_present": False,
        "score": 0.0,
    }

    artifact_text = collect_artifact_text(workspace_path, tool_logs)
    py_records = python_source_records(workspace_path)
    code_text = python_code_text(py_records)

    results["slippage_model_present"] = has_any(
        code_text,
        [
            "slippage",
            "slippage_bps",
            "slippage_bp",
            "fill_price",
            "execution_price",
            "adjusted_price",
            "market_impact",
            "price_impact",
            "impact",
        ],
    ) and has_regex(
        code_text,
        [
            r"(?:fill_price|execution_price|adjusted_price)\s*=.*(?:price|close|mid)"
            r"[^\n]{0,80}(?:\+|\-|\*)[^\n]{0,80}(?:slippage|impact|bps|bp)",
            r"(?:price|close|mid)[^\n]{0,40}(?:\+|\-)[^\n]{0,40}(?:slippage|impact)",
            r"(?:slippage|price_impact|market_impact|impact_cost)(?:_cost)?\s*="
            r".*(?:qty|quantity|notional|price|spread|volume)",
        ],
    )

    results["fee_model_present"] = has_any(
        code_text,
        [
            "maker_fee",
            "taker_fee",
            "maker_rate",
            "taker_rate",
            "is_maker",
            "is_taker",
            "maker",
            "taker",
        ],
    ) and has_regex(
        code_text,
        [
            r"maker[_ ](?:fee|rate)",
            r"taker[_ ](?:fee|rate)",
            r"(?:fee|commission)\s*=.*(?:maker|taker|is_maker|is_taker)",
            r"(?:maker|taker).{0,40}(?:fee|rate|commission)",
        ],
    )

    results["funding_model_present"] = has_any(
        code_text,
        [
            "funding_rate",
            "funding_payment",
            "funding_pnl",
            "carry",
        ],
    ) and has_any(artifact_text, ["8h", "8-hour", "8 hour", "funding"])

    results["execution_artifact_present"] = workspace_has_csv_column_group(
        workspace_path,
        [["fill_price", "execution_price", "fill"], ["fee", "fees", "commission"]],
    ) or has_any(
        artifact_text,
        [
            "trade_log",
            "fill_price",
            "execution_price",
            "fee",
            "funding_payment",
        ],
    )

    results["gross_vs_net_breakdown_present"] = has_metric_numbers(
        artifact_text,
        [
            ["gross", "gross_pnl", "gross_return"],
            ["net", "net_pnl", "net_return"],
            ["funding", "funding_pnl", "carry"],
        ],
    )

    results["performance_summary_present"] = has_metric_numbers(
        artifact_text,
        [
            ["sharpe", "total return", "annualized return", "max drawdown"],
            ["trades", "trade_count", "entries", "exits"],
        ],
    )

    _checklist = [
        {"item": "slippage_model_present", "weight": 0.20, "passed": results["slippage_model_present"]},
        {"item": "fee_model_present", "weight": 0.15, "passed": results["fee_model_present"]},
        {"item": "funding_model_present", "weight": 0.15, "passed": results["funding_model_present"]},
        {
            "item": "execution_artifact_present",
            "weight": 0.15,
            "passed": results["execution_artifact_present"],
        },
        {
            "item": "gross_vs_net_breakdown_present",
            "weight": 0.20,
            "passed": results["gross_vs_net_breakdown_present"],
        },
        {
            "item": "performance_summary_present",
            "weight": 0.15,
            "passed": results["performance_summary_present"],
        },
    ]
    score = sum(c["weight"] for c in _checklist if c["passed"])

    if not (
        results["slippage_model_present"]
        and results["fee_model_present"]
        and results["funding_model_present"]
    ):
        score = min(score, 0.25)
    if not results["gross_vs_net_breakdown_present"]:
        score = min(score, 0.50)

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
