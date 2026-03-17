"""Evaluation script for B05: Execution simulation and futures mechanics."""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.backtest_engine_check import (
    collect_artifact_text,
    has_any,
    has_metric_numbers,
    has_regex,
    python_code_text,
    python_source_records,
    workspace_has_csv_column_group,
)
from common.data_source_check import verify_data_source


def _tool_log_code_text(tool_logs: list) -> str:
    """Extract code from tool logs (heredoc, inline, file_write)."""
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

    # Also extract code from tool logs
    tool_code = _tool_log_code_text(tool_logs)
    all_code = code_text + "\n" + tool_code

    # Slippage: keyword evidence + code pattern showing price adjustment
    slippage_keywords = [
        "slippage",
        "slippage_bps",
        "slippage_bp",
        "slip_bps",
        "slip_bp",
        "fill_price",
        "execution_price",
        "adjusted_price",
        "market_impact",
        "price_impact",
        "impact_cost",
    ]
    slippage_patterns = [
        # Direct assignment: fill_price = price +/- slippage
        r"(?:fill_price|execution_price|adjusted_price|fill|entry_price|exit_price)\s*="
        r".*(?:price|close|mid|open)[^\n]{0,80}(?:\+|\-|\*)[^\n]{0,80}(?:slippage|impact|bps|bp|slip)",
        # Reverse order: price +/- slippage = fill_price
        r"(?:price|close|mid)[^\n]{0,60}(?:\+|\-|\*)[^\n]{0,60}(?:slippage|impact|slip|bps)",
        # Slippage computed from spread/volume/ATR
        r"(?:slippage|price_impact|market_impact|impact_cost|slip)(?:_cost)?\s*="
        r".*(?:qty|quantity|notional|price|spread|volume|atr)",
        # Multiplicative: price * (1 +/- slip)
        r"(?:price|close|mid|entry|exit)\s*\*\s*\(?\s*1\s*(?:\+|\-)\s*(?:slip|impact|bps)",
    ]
    results["slippage_model_present"] = has_any(
        all_code, slippage_keywords
    ) and has_regex(all_code, slippage_patterns)

    # Fee model: maker/taker differentiation
    fee_keywords = [
        "maker_fee",
        "taker_fee",
        "maker_rate",
        "taker_rate",
        "is_maker",
        "is_taker",
        "maker",
        "taker",
        "commission",
        "trading_fee",
        "fee_rate",
    ]
    fee_patterns = [
        r"maker[_ ](?:fee|rate|commission)",
        r"taker[_ ](?:fee|rate|commission)",
        r"(?:fee|commission)\s*=.*(?:maker|taker|is_maker|is_taker)",
        r"(?:maker|taker).{0,40}(?:fee|rate|commission)",
        # Also accept explicit fee rate assignments
        r"(?:fee|commission)_?rate\s*=\s*-?\d",
    ]
    results["fee_model_present"] = has_any(all_code, fee_keywords) and has_regex(
        all_code, fee_patterns
    )

    # Funding model: evidence of periodic funding payments
    funding_keywords = [
        "funding_rate",
        "funding_payment",
        "funding_pnl",
        "funding_cost",
        "carry",
        "funding",
    ]
    funding_context = [
        "8h",
        "8-hour",
        "8 hour",
        "eight hour",
        "funding",
        "perpetual",
        "perp",
    ]
    results["funding_model_present"] = has_any(all_code, funding_keywords) and has_any(
        artifact_text, funding_context
    )

    # Execution artifacts: CSV with fill/fee columns or text evidence
    results["execution_artifact_present"] = workspace_has_csv_column_group(
        workspace_path,
        [
            [
                "fill_price",
                "execution_price",
                "fill",
                "entry_price",
                "exit_price",
                "avg_price",
            ],
            ["fee", "fees", "commission", "trading_fee", "cost"],
        ],
    ) or has_any(
        artifact_text,
        [
            "trade_log",
            "fill_price",
            "execution_price",
            "fill_log",
            "fee",
            "funding_payment",
            "cost_breakdown",
        ],
    )

    # Gross vs net breakdown
    results["gross_vs_net_breakdown_present"] = has_metric_numbers(
        artifact_text,
        [
            ["gross", "gross_pnl", "gross_return", "gross pnl", "gross return"],
            ["net", "net_pnl", "net_return", "net pnl", "net return", "net of"],
        ],
    ) or (
        # Alternative: explicit cost impact breakdown
        has_any(
            artifact_text,
            [
                "fee impact",
                "slip impact",
                "funding impact",
                "fee_impact",
                "slip_impact",
                "funding_impact",
                "cost breakdown",
                "cost_breakdown",
            ],
        )
        and has_any(artifact_text, ["gross", "net"])
    )

    results["performance_summary_present"] = has_metric_numbers(
        artifact_text,
        [
            [
                "sharpe",
                "total return",
                "annualized return",
                "max drawdown",
                "sharpe_ratio",
                "total_return",
                "max_drawdown",
                "net profit",
            ],
            ["trades", "trade_count", "entries", "exits", "total_trades", "num_trades"],
        ],
    )

    _checklist = [
        {
            "item": "slippage_model_present",
            "weight": 0.20,
            "passed": results["slippage_model_present"],
        },
        {
            "item": "fee_model_present",
            "weight": 0.15,
            "passed": results["fee_model_present"],
        },
        {
            "item": "funding_model_present",
            "weight": 0.15,
            "passed": results["funding_model_present"],
        },
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

    # Hard cap: if none of the three core execution models present
    exec_model_count = sum(
        1
        for k in (
            "slippage_model_present",
            "fee_model_present",
            "funding_model_present",
        )
        if results[k]
    )
    if exec_model_count == 0:
        score = min(score, 0.25)
    elif exec_model_count == 1:
        score = min(score, 0.40)

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
