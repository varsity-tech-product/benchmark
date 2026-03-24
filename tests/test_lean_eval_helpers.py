#!/usr/bin/env python3
"""Regression tests for LEAN eval helper fallbacks."""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bench.evaluation.test_scripts import _implementation_check as wrapper_impl
from bench.evaluation.test_scripts._implementation_check import (
    load_agent_orders,
    load_agent_trades,
    load_reference_trades,
    match_trades,
)
from bench.evaluation.test_scripts.common import implementation_check as common_impl


def test_load_agent_trades_reads_result_json_fallback(tmp_path: Path):
    """result.json should be sufficient when trades.json is missing."""
    results_dir = tmp_path / "results"
    results_dir.mkdir()

    payload = {
        "totalPerformance": {
            "closedTrades": [
                {
                    "Symbol": "BTCUSDT",
                    "Direction": "Buy",
                    "Quantity": 1,
                    "EntryTime": "2024-01-01T00:00:00Z",
                    "EntryPrice": 100.0,
                    "ExitTime": "2024-01-02T00:00:00Z",
                    "ExitPrice": 110.0,
                    "ProfitLoss": 10.0,
                    "TotalFees": 0.0,
                }
            ]
        }
    }
    (results_dir / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    trades = load_agent_trades(str(tmp_path))
    assert len(trades) == 1
    assert trades[0]["symbol"] == "BTCUSDT"
    assert trades[0]["entry_price"] == 100.0
    assert trades[0]["exit_price"] == 110.0


def test_order_pairing_uses_clean_symbols_and_numeric_times():
    """Order-event fallbacks should preserve symbols, prices, and timestamps."""
    for task in ("I02", "I05"):
        orders = load_agent_orders(f"tests/results/{task}")
        trades = load_agent_trades(f"tests/results/{task}")

        assert orders, f"{task}: expected parsed order events"
        assert trades, f"{task}: expected fallback-paired trades"

        assert " " not in orders[0]["symbol"], f"{task}: symbol suffix should be stripped"
        assert isinstance(orders[0]["time"], (int, float)), f"{task}: order time should stay numeric"

        assert " " not in trades[0]["symbol"], f"{task}: paired trade symbol should be clean"
        assert isinstance(trades[0]["entry_time"], (int, float)), f"{task}: entry time should stay numeric"
        assert trades[0]["entry_price"] > 0, f"{task}: paired trades should use fillPrice"
        assert trades[0]["exit_price"] > 0, f"{task}: paired trades should use fillPrice"


def test_order_paired_trades_match_reference_counts_and_timing():
    """The repaired order-pairing fallback should line up with reference trades."""
    for task in ("I02", "I05"):
        ref_trades = load_reference_trades(task)
        agent_trades = load_agent_trades(f"tests/results/{task}")
        match = match_trades(ref_trades, agent_trades, resolution="daily")

        assert match.matched_count == len(ref_trades), (
            f"{task}: expected all reference trades to match after fallback repair"
        )
        assert match.entry_match_rate == 1.0, f"{task}: entry timing should fully match"
        assert match.direction_match_rate == 1.0, f"{task}: directions should fully match"
        assert match.exit_match_rate == 1.0, f"{task}: exit timing should fully match"


def test_native_closed_trades_keep_symbol_for_single_and_framework_tasks():
    """Native LEAN closedTrades should normalize ``symbols`` into clean tickers."""
    for task in ("I01", "I03", "I04", "I07"):
        trades = load_agent_trades(f"tests/results/{task}")
        assert trades, f"{task}: expected native trades to load"
        assert all(str(t.get("symbol", "")).strip() for t in trades), (
            f"{task}: native closedTrades should not lose symbol information"
        )
        assert all(" " not in t["symbol"] for t in trades[:10]), (
            f"{task}: native symbols should already be normalized"
        )


def test_wrapper_module_reexports_common_helper_behavior():
    """Legacy wrapper and common helper should return identical trade parsing."""
    for task in ("I01", "I02", "I05", "I07"):
        wrapper = wrapper_impl.load_agent_trades(f"tests/results/{task}")
        common = common_impl.load_agent_trades(f"tests/results/{task}")
        assert wrapper == common, f"{task}: wrapper helper drifted from common helper"
