"""Canonical I-series benchmark date window."""
BENCH_START = "2022-01-01"
BENCH_END = "2025-12-31"

# Pre-period window for point-in-time pair/parameter selection
# (avoids look-ahead bias by using only data before the backtest window)
PAIR_SELECTION_START = "2020-01-01"
PAIR_SELECTION_END = "2021-12-31"
