#!/usr/bin/env python3
"""Compute deterministic reference signals from raw market data + strategy rules.

Signals are computed in pure Python (pandas) from raw CSVs, free from
LEAN engine quirks. Each task gets:
  - I0X_reference_signals.json   (daily signal direction per symbol)
  - I0X_reference_positions.json (daily position from LEAN orders)
  - I0X_reference_summary.json   (performance metrics)

Usage:
    python generate_reference_signals.py --task I01
    python generate_reference_signals.py --task all
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# ── Paths ──
SCRIPT_DIR = Path(__file__).parent
BENCH_ROOT = SCRIPT_DIR.parent.parent  # bench/reference/script/ -> bench/
RAW_DATA_DIR = BENCH_ROOT / "data" / "raw" / "i-series"
REFERENCE_DIR = BENCH_ROOT / "data" / "reference"

# Import canonical date window
sys.path.insert(0, str(BENCH_ROOT))
from config.benchmark_dates import BENCH_END, BENCH_START  # noqa: E402

DEFAULT_START = BENCH_START
DEFAULT_END = BENCH_END


# ────────────────────────────────────────────────────────────────────
# Data loading
# ────────────────────────────────────────────────────────────────────


def _load_daily_csv(symbol: str) -> pd.DataFrame:
    """Load raw daily CSV, filling gaps from minute data if needed."""
    daily_path = RAW_DATA_DIR / "tier1_daily" / f"{symbol}_1d.csv"
    minute_path = RAW_DATA_DIR / "tier3_minute" / f"{symbol}_1m.csv"

    frames: list[pd.DataFrame] = []

    if daily_path.exists():
        df = pd.read_csv(daily_path)
        df = df.dropna(subset=["timestamp"])
        df["date"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.date
        frames.append(df[["date", "open", "high", "low", "close", "volume"]])

    # Fill gaps from minute data (aggregate to daily OHLCV)
    if minute_path.exists():
        mdf = pd.read_csv(minute_path)
        mdf = mdf.dropna(subset=["timestamp"])
        mdf["dt"] = pd.to_datetime(mdf["timestamp"], unit="ms", utc=True)
        mdf["date"] = mdf["dt"].dt.date
        agg = (
            mdf.groupby("date")
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .reset_index()
        )
        frames.append(agg)

    if not frames:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(subset=["date"], keep="first")
    combined = combined.sort_values("date").reset_index(drop=True)
    return combined


def _load_hourly_csv(symbol: str, hours: int = 1) -> pd.DataFrame:
    """Load raw hourly CSV (1h or 4h)."""
    suffix = f"{hours}h"
    path = RAW_DATA_DIR / "tier2_hourly" / f"{symbol}_{suffix}.csv"
    if not path.exists():
        # Try aggregating from minute data
        minute_path = RAW_DATA_DIR / "tier3_minute" / f"{symbol}_1m.csv"
        if not minute_path.exists():
            return pd.DataFrame()
        mdf = pd.read_csv(minute_path)
        mdf = mdf.dropna(subset=["timestamp"])
        mdf["dt"] = pd.to_datetime(mdf["timestamp"], unit="ms", utc=True)
        mdf = mdf.set_index("dt")
        rule = f"{hours}h"
        agg = (
            mdf.resample(rule)
            .agg(
                open=("open", "first"),
                high=("high", "max"),
                low=("low", "min"),
                close=("close", "last"),
                volume=("volume", "sum"),
            )
            .dropna(subset=["close"])
            .reset_index()
        )
        agg = agg.rename(columns={"dt": "datetime"})
        return agg

    df = pd.read_csv(path)
    df = df.dropna(subset=["timestamp"])
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    return df


def _filter_dates(
    df: pd.DataFrame, start: str, end: str, date_col: str = "date"
) -> pd.DataFrame:
    """Filter dataframe to [start, end] date range."""
    start_d = (
        pd.Timestamp(start).date()
        if date_col == "date"
        else pd.Timestamp(start, tz="UTC")
    )
    end_d = (
        pd.Timestamp(end).date() if date_col == "date" else pd.Timestamp(end, tz="UTC")
    )
    return df[(df[date_col] >= start_d) & (df[date_col] <= end_d)].copy()


# ────────────────────────────────────────────────────────────────────
# Signal generators (pure Python / pandas)
# ────────────────────────────────────────────────────────────────────


def _signals_i01(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I01: SMA(20) on daily close. signal = +1 if close > SMA else -1."""
    symbol = "BTCUSDT"
    df = _load_daily_csv(symbol)
    df = _filter_dates(df, start, end)
    if df.empty:
        return _empty_signal_result("I01", "daily", start, end, 20)

    df["sma20"] = df["close"].rolling(20).mean()
    warmup = 20

    signals = []
    for _, row in df.iterrows():
        if pd.isna(row["sma20"]):
            continue
        sig = 1 if row["close"] > row["sma20"] else -1
        signals.append({"date": str(row["date"]), "signal": sig})

    return {
        "task_id": "I01",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": {symbol: signals},
    }


def _signals_i02(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I02: Per-symbol dual SMA(10)/SMA(30). signal = +1 if fast > slow else -1."""
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    all_signals: dict[str, list] = {}
    warmup = 30

    for sym in symbols:
        df = _load_daily_csv(sym)
        df = _filter_dates(df, start, end)
        if df.empty:
            continue

        df["sma10"] = df["close"].rolling(10).mean()
        df["sma30"] = df["close"].rolling(30).mean()

        signals = []
        for _, row in df.iterrows():
            if pd.isna(row["sma10"]) or pd.isna(row["sma30"]):
                continue
            sig = 1 if row["sma10"] > row["sma30"] else -1
            signals.append({"date": str(row["date"]), "signal": sig})

        all_signals[sym] = signals

    return {
        "task_id": "I02",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": all_signals,
    }


def _signals_i03(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I03: RSI(14). signal = +1 if RSI<30, -1 if RSI>70, 0 otherwise."""
    symbol = "BTCUSDT"
    df = _load_daily_csv(symbol)
    df = _filter_dates(df, start, end)
    if df.empty:
        return _empty_signal_result("I03", "daily", start, end, 14)

    # Compute RSI
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    warmup = 14

    signals = []
    for _, row in df.iterrows():
        if pd.isna(row["rsi"]):
            continue
        if row["rsi"] < 30:
            sig = 1
        elif row["rsi"] > 70:
            sig = -1
        else:
            sig = 0
        signals.append({"date": str(row["date"]), "signal": sig})

    return {
        "task_id": "I03",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": {symbol: signals},
    }


def _signals_i04(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I04: EMA(20) on 4h + RSI(14) on 1h. Composite signal."""
    symbol = "BTCUSDT"

    # 4h EMA(20) for trend
    df_4h = _load_hourly_csv(symbol, hours=4)
    if df_4h.empty:
        return _empty_signal_result("I04", "hour", start, end, 20)

    df_4h["ema20"] = df_4h["close"].ewm(span=20, adjust=False).mean()
    df_4h["trend"] = (df_4h["close"] > df_4h["ema20"]).astype(int) * 2 - 1  # +1/-1

    # 1h RSI(14) for momentum
    df_1h = _load_hourly_csv(symbol, hours=1)
    if df_1h.empty:
        return _empty_signal_result("I04", "hour", start, end, 20)

    delta = df_1h["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df_1h["rsi"] = 100 - (100 / (1 + rs))

    # Map RSI to signal: <30 → +1, >70 → -1, else 0
    def rsi_signal(rsi):
        if pd.isna(rsi):
            return 0
        if rsi < 30:
            return 1
        if rsi > 70:
            return -1
        return 0

    df_1h["rsi_sig"] = df_1h["rsi"].apply(rsi_signal)

    # Combine on daily basis: resample to daily, take last value
    start_ts = pd.Timestamp(start, tz="UTC")
    end_ts = pd.Timestamp(end, tz="UTC")

    df_4h_f = df_4h[
        (df_4h["datetime"] >= start_ts) & (df_4h["datetime"] <= end_ts)
    ].copy()
    df_1h_f = df_1h[
        (df_1h["datetime"] >= start_ts) & (df_1h["datetime"] <= end_ts)
    ].copy()

    # Resample to daily
    if df_4h_f.empty or df_1h_f.empty:
        return _empty_signal_result("I04", "hour", start, end, 20)

    df_4h_f = df_4h_f.set_index("datetime")
    daily_trend = df_4h_f["trend"].resample("1D").last().dropna()

    df_1h_f = df_1h_f.set_index("datetime")
    daily_rsi = df_1h_f["rsi_sig"].resample("1D").last().dropna()

    # Merge
    combined = pd.DataFrame({"trend": daily_trend, "rsi_sig": daily_rsi}).dropna()
    # Composite: agree → full signal, disagree → trend dominates
    signals = []
    for dt, row in combined.iterrows():
        date_str = str(dt.date())
        if row["trend"] == row["rsi_sig"]:
            sig = int(row["trend"])
        elif row["rsi_sig"] == 0:
            sig = int(row["trend"])
        else:
            sig = int(row["trend"])  # trend dominates
        if sig != 0:
            signals.append({"date": date_str, "signal": sig})

    return {
        "task_id": "I04",
        "seed": None,
        "resolution": "hour",
        "start_date": start,
        "end_date": end,
        "warmup_periods": 20,
        "signals": {symbol: signals},
    }


def _signals_i05(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I05: Pair z-score. signal = +1/-1 if |z|>2, 0 if |z|<0.5."""
    sym_a, sym_b = "BTCUSDT", "ETHUSDT"
    df_a = _load_daily_csv(sym_a)
    df_b = _load_daily_csv(sym_b)

    if df_a.empty or df_b.empty:
        return _empty_signal_result("I05", "daily", start, end, 20)

    # Merge on date
    merged = pd.merge(
        df_a[["date", "close"]].rename(columns={"close": "close_a"}),
        df_b[["date", "close"]].rename(columns={"close": "close_b"}),
        on="date",
    )
    merged = _filter_dates(merged, start, end)
    if merged.empty:
        return _empty_signal_result("I05", "daily", start, end, 20)

    # Log spread
    merged["spread"] = np.log(merged["close_a"]) - np.log(merged["close_b"])
    merged["spread_mean"] = merged["spread"].rolling(20).mean()
    merged["spread_std"] = merged["spread"].rolling(20).std()
    merged["zscore"] = (merged["spread"] - merged["spread_mean"]) / merged[
        "spread_std"
    ].replace(0, np.nan)

    signals_a = []
    signals_b = []
    for _, row in merged.iterrows():
        if pd.isna(row["zscore"]):
            continue
        z = row["zscore"]
        date_str = str(row["date"])
        if z > 2:
            # Spread too wide: short A, long B
            signals_a.append({"date": date_str, "signal": -1})
            signals_b.append({"date": date_str, "signal": 1})
        elif z < -2:
            # Spread too narrow: long A, short B
            signals_a.append({"date": date_str, "signal": 1})
            signals_b.append({"date": date_str, "signal": -1})
        elif abs(z) < 0.5:
            signals_a.append({"date": date_str, "signal": 0})
            signals_b.append({"date": date_str, "signal": 0})
        else:
            # Keep previous signal (hold zone) — use last known signal
            prev_a = signals_a[-1]["signal"] if signals_a else 0
            prev_b = signals_b[-1]["signal"] if signals_b else 0
            signals_a.append({"date": date_str, "signal": prev_a})
            signals_b.append({"date": date_str, "signal": prev_b})

    return {
        "task_id": "I05",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": 20,
        "signals": {sym_a: signals_a, sym_b: signals_b},
    }


def _signals_i06(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I06: Composite (0.4*trend + 0.3*reversion + 0.3*carry)."""
    symbol = "BTCUSDT"
    df = _load_daily_csv(symbol)
    df = _filter_dates(df, start, end)
    if df.empty:
        return _empty_signal_result("I06", "daily", start, end, 30)

    # Trend: SMA(20) crossover
    df["sma20"] = df["close"].rolling(20).mean()
    df["trend_sig"] = 0.0
    df.loc[df["close"] > df["sma20"], "trend_sig"] = 1.0
    df.loc[df["close"] <= df["sma20"], "trend_sig"] = -1.0

    # Mean reversion: RSI(14)
    delta = df["close"].diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["rsi"] = 100 - (100 / (1 + rs))
    df["reversion_sig"] = 0.0
    df.loc[df["rsi"] < 30, "reversion_sig"] = 1.0
    df.loc[df["rsi"] > 70, "reversion_sig"] = -1.0

    # Carry/funding proxy: use momentum (close - close[7]) as carry stand-in
    # (actual funding data is separate, but for signal purposes momentum works)
    df["momentum"] = df["close"].pct_change(7)
    df["carry_sig"] = 0.0
    df.loc[df["momentum"] > 0.02, "carry_sig"] = 1.0
    df.loc[df["momentum"] < -0.02, "carry_sig"] = -1.0

    # Composite: 0.4*trend + 0.3*reversion + 0.3*carry
    df["composite"] = (
        0.4 * df["trend_sig"] + 0.3 * df["reversion_sig"] + 0.3 * df["carry_sig"]
    )
    warmup = 30

    signals = []
    for _, row in df.iterrows():
        if pd.isna(row["composite"]) or pd.isna(row["sma20"]):
            continue
        # Threshold: composite > 0.1 → +1, < -0.1 → -1, else 0
        if row["composite"] > 0.1:
            sig = 1
        elif row["composite"] < -0.1:
            sig = -1
        else:
            sig = 0
        signals.append({"date": str(row["date"]), "signal": sig})

    return {
        "task_id": "I06",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": {symbol: signals},
    }


def _signals_i07(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I07: EMA(10)/EMA(30) crossover on ~20 tier2 symbols, daily.

    signal = +1 if EMA(10) > EMA(30) else -1.
    """
    # Load universe to get tier2 symbols (first 20)
    universe_path = BENCH_ROOT / "data" / "universe.json"
    if universe_path.exists():
        with open(universe_path) as f:
            universe = json.load(f)
        symbols = universe["tiers"]["tier2"]["symbols"][:20]
    else:
        symbols = [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "SOLUSDT",
            "XRPUSDT",
            "ADAUSDT",
            "DOGEUSDT",
            "AVAXUSDT",
            "DOTUSDT",
            "MATICUSDT",
        ]

    all_signals: dict[str, list] = {}
    warmup = 30

    for sym in symbols:
        df = _load_daily_csv(sym)
        df = _filter_dates(df, start, end)
        if df.empty:
            continue

        df["ema10"] = df["close"].ewm(span=10, adjust=False).mean()
        df["ema30"] = df["close"].ewm(span=30, adjust=False).mean()

        signals = []
        for _, row in df.iterrows():
            if pd.isna(row["ema10"]) or pd.isna(row["ema30"]):
                continue
            sig = 1 if row["ema10"] > row["ema30"] else -1
            signals.append({"date": str(row["date"]), "signal": sig})

        all_signals[sym] = signals

    return {
        "task_id": "I07",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": all_signals,
    }


def _signals_i08(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I08: Composite 3-signal (SMA trend + RSI + ROC) on tier1 symbols, daily.

    Trend: SMA(20)/SMA(50) → +1/-1
    MeanReversion: RSI(14) <30 → +1, >70 → -1, else 0
    Momentum: 20-day ROC >5% → +1, <-5% → -1, else 0
    Composite: majority vote (sum thresholded at 0)
    """
    # Load universe to get tier1 symbols (first 100)
    universe_path = BENCH_ROOT / "data" / "universe.json"
    if universe_path.exists():
        with open(universe_path) as f:
            universe = json.load(f)
        symbols = universe["tiers"]["tier1"]["symbols"][:100]
    else:
        symbols = [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "SOLUSDT",
            "XRPUSDT",
            "ADAUSDT",
            "DOGEUSDT",
            "AVAXUSDT",
            "DOTUSDT",
            "MATICUSDT",
        ]

    all_signals: dict[str, list] = {}
    warmup = 50

    for sym in symbols:
        df = _load_daily_csv(sym)
        df = _filter_dates(df, start, end)
        if df.empty or len(df) < warmup:
            continue

        # Trend: SMA(20)/SMA(50)
        df["sma20"] = df["close"].rolling(20).mean()
        df["sma50"] = df["close"].rolling(50).mean()
        df["trend_sig"] = 0
        df.loc[df["sma20"] > df["sma50"], "trend_sig"] = 1
        df.loc[df["sma20"] <= df["sma50"], "trend_sig"] = -1

        # Mean reversion: RSI(14)
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))
        df["reversion_sig"] = 0
        df.loc[df["rsi"] < 30, "reversion_sig"] = 1
        df.loc[df["rsi"] > 70, "reversion_sig"] = -1

        # Momentum: 20-day ROC
        df["roc"] = df["close"].pct_change(20) * 100
        df["momentum_sig"] = 0
        df.loc[df["roc"] > 5, "momentum_sig"] = 1
        df.loc[df["roc"] < -5, "momentum_sig"] = -1

        # Composite: sum of three signals
        df["composite"] = df["trend_sig"] + df["reversion_sig"] + df["momentum_sig"]

        signals = []
        for _, row in df.iterrows():
            if pd.isna(row["sma50"]) or pd.isna(row["rsi"]) or pd.isna(row["roc"]):
                continue
            c = row["composite"]
            if c > 0:
                sig = 1
            elif c < 0:
                sig = -1
            else:
                sig = 0
            signals.append({"date": str(row["date"]), "signal": sig})

        if signals:
            all_signals[sym] = signals

    return {
        "task_id": "I08",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": all_signals,
    }


def _signals_i09(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I09: EMA(24)/EMA(72) on hourly data, resampled to daily, ~20 tier2 symbols.

    signal = +1 if EMA(24) > EMA(72) else -1, evaluated on last hourly bar of the day.
    """
    # Load universe to get tier2 symbols (first 20)
    universe_path = BENCH_ROOT / "data" / "universe.json"
    if universe_path.exists():
        with open(universe_path) as f:
            universe = json.load(f)
        symbols = universe["tiers"]["tier2"]["symbols"][:20]
    else:
        symbols = [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "SOLUSDT",
            "XRPUSDT",
            "ADAUSDT",
            "DOGEUSDT",
            "AVAXUSDT",
            "DOTUSDT",
            "MATICUSDT",
        ]

    all_signals: dict[str, list] = {}
    warmup = 72

    for sym in symbols:
        df = _load_hourly_csv(sym, hours=1)
        if df.empty:
            continue

        # Compute EMA on hourly close
        df["ema24"] = df["close"].ewm(span=24, adjust=False).mean()
        df["ema72"] = df["close"].ewm(span=72, adjust=False).mean()

        # Filter to date range
        start_ts = pd.Timestamp(start, tz="UTC")
        end_ts = pd.Timestamp(end, tz="UTC")
        df_f = df[(df["datetime"] >= start_ts) & (df["datetime"] <= end_ts)].copy()

        if df_f.empty:
            continue

        # Resample to daily (last value of day)
        df_f = df_f.set_index("datetime")
        daily_ema24 = df_f["ema24"].resample("1D").last().dropna()
        daily_ema72 = df_f["ema72"].resample("1D").last().dropna()
        combined = pd.DataFrame({"ema24": daily_ema24, "ema72": daily_ema72}).dropna()

        signals = []
        for dt, row in combined.iterrows():
            sig = 1 if row["ema24"] > row["ema72"] else -1
            signals.append({"date": str(dt.date()), "signal": sig})

        if signals:
            all_signals[sym] = signals

    return {
        "task_id": "I09",
        "seed": None,
        "resolution": "hour",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": all_signals,
    }


def _signals_i10(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """I10: EMA(10)/EMA(30) with threshold=0.01 on ~20 tier2 symbols, daily.

    signal = +1 if EMA(10) > EMA(30) and spread > threshold,
             -1 if EMA(10) < EMA(30) and spread > threshold,
             0 otherwise (below threshold → no signal).
    """
    # Load universe to get tier2 symbols (first 20)
    universe_path = BENCH_ROOT / "data" / "universe.json"
    if universe_path.exists():
        with open(universe_path) as f:
            universe = json.load(f)
        symbols = universe["tiers"]["tier2"]["symbols"][:20]
    else:
        symbols = [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "SOLUSDT",
            "XRPUSDT",
            "ADAUSDT",
            "DOGEUSDT",
            "AVAXUSDT",
            "DOTUSDT",
            "MATICUSDT",
        ]

    threshold = 0.01
    all_signals: dict[str, list] = {}
    warmup = 30

    for sym in symbols:
        df = _load_daily_csv(sym)
        df = _filter_dates(df, start, end)
        if df.empty:
            continue

        df["ema10"] = df["close"].ewm(span=10, adjust=False).mean()
        df["ema30"] = df["close"].ewm(span=30, adjust=False).mean()

        signals = []
        for _, row in df.iterrows():
            if pd.isna(row["ema10"]) or pd.isna(row["ema30"]) or row["ema30"] == 0:
                continue
            spread = abs(row["ema10"] - row["ema30"]) / row["ema30"]
            if spread <= threshold:
                signals.append({"date": str(row["date"]), "signal": 0})
            elif row["ema10"] > row["ema30"]:
                signals.append({"date": str(row["date"]), "signal": 1})
            else:
                signals.append({"date": str(row["date"]), "signal": -1})

        all_signals[sym] = signals

    return {
        "task_id": "I10",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": all_signals,
    }


def _signals_e02(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """E02: BB(20,2) on BTCUSDT daily.

    signal = +1 if close < lower band, 0 if close > middle band, carry otherwise.
    warmup=20.
    """
    symbol = "BTCUSDT"
    df = _load_daily_csv(symbol)
    df = _filter_dates(df, start, end)
    if df.empty:
        return _empty_signal_result("E02", "daily", start, end, 20)

    df["sma20"] = df["close"].rolling(20).mean()
    df["std20"] = df["close"].rolling(20).std()
    df["upper_band"] = df["sma20"] + 2.0 * df["std20"]
    df["lower_band"] = df["sma20"] - 2.0 * df["std20"]
    df["middle_band"] = df["sma20"]
    warmup = 20

    signals = []
    last_sig = 0
    for _, row in df.iterrows():
        if pd.isna(row["sma20"]) or pd.isna(row["std20"]):
            continue
        if row["close"] < row["lower_band"]:
            sig = 1
        elif row["close"] > row["middle_band"]:
            sig = 0
        else:
            sig = last_sig  # carry previous signal
        signals.append({"date": str(row["date"]), "signal": sig})
        last_sig = sig

    return {
        "task_id": "E02",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": {symbol: signals},
    }


def _signals_e04(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """E04: EMA(20/50) crossover on BTCUSDT daily.

    signal = +1 if fast > slow, -1 otherwise. warmup=50.
    """
    symbol = "BTCUSDT"
    df = _load_daily_csv(symbol)
    df = _filter_dates(df, start, end)
    if df.empty:
        return _empty_signal_result("E04", "daily", start, end, 50)

    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    warmup = 50

    signals = []
    for _, row in df.iterrows():
        if pd.isna(row["ema20"]) or pd.isna(row["ema50"]):
            continue
        sig = 1 if row["ema20"] > row["ema50"] else -1
        signals.append({"date": str(row["date"]), "signal": sig})

    return {
        "task_id": "E04",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": {symbol: signals},
    }


def _signals_e05(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """E05: ROCP(20) on ~20 tier2 symbols, daily.

    Rank daily by ROCP, top-5 get +1, rest get 0. warmup=20.
    """
    universe_path = BENCH_ROOT / "data" / "universe.json"
    if universe_path.exists():
        with open(universe_path) as f:
            universe = json.load(f)
        symbols = universe["tiers"]["tier2"]["symbols"][:20]
    else:
        symbols = [
            "BTCUSDT",
            "ETHUSDT",
            "BNBUSDT",
            "SOLUSDT",
            "XRPUSDT",
            "ADAUSDT",
            "DOGEUSDT",
            "AVAXUSDT",
            "DOTUSDT",
            "MATICUSDT",
        ]

    warmup = 20
    top_n = 5

    # Load daily data for all symbols
    sym_data: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = _load_daily_csv(sym)
        df = _filter_dates(df, start, end)
        if df.empty or len(df) < warmup:
            continue
        df["rocp"] = df["close"].pct_change(warmup)
        sym_data[sym] = df

    if not sym_data:
        return _empty_signal_result("E05", "daily", start, end, warmup)

    # Collect all unique dates
    all_dates: set = set()
    for sym, df in sym_data.items():
        for _, row in df.iterrows():
            if not pd.isna(row["rocp"]):
                all_dates.add(row["date"])

    all_signals: dict[str, list] = {sym: [] for sym in sym_data}

    for date in sorted(all_dates):
        # Collect ROCP values for this date
        rankings = []
        for sym, df in sym_data.items():
            row = df[df["date"] == date]
            if row.empty or pd.isna(row.iloc[0]["rocp"]):
                continue
            rankings.append((sym, float(row.iloc[0]["rocp"])))

        # Rank and select top-N
        rankings.sort(key=lambda x: x[1], reverse=True)
        top_symbols = {sym for sym, _ in rankings[:top_n]}

        for sym in sym_data:
            row = sym_data[sym][sym_data[sym]["date"] == date]
            if row.empty or pd.isna(row.iloc[0]["rocp"]):
                continue
            sig = 1 if sym in top_symbols else 0
            all_signals[sym].append({"date": str(date), "signal": sig})

    # Remove empty symbol entries
    all_signals = {sym: sigs for sym, sigs in all_signals.items() if sigs}

    return {
        "task_id": "E05",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": all_signals,
    }


def _compute_candidate_pairs(
    start: str = DEFAULT_START, end: str = DEFAULT_END
) -> dict:
    """Pre-compute pairwise correlations for all Tier 2 symbols.

    Returns a JSON-serializable dict with the top-10 pairs ranked by
    average 60-day rolling correlation of log returns.
    """
    # Load universe to get Tier 2 symbols
    universe_path = BENCH_ROOT / "data" / "universe.json"
    with open(universe_path) as f:
        universe = json.load(f)
    symbols = universe["tiers"]["tier2"]["symbols"]

    # Load daily close prices for each symbol
    closes: dict[str, pd.Series] = {}
    for sym in symbols:
        df = _load_daily_csv(sym)
        df = _filter_dates(df, start, end)
        if df.empty or len(df) < 60:
            continue
        closes[sym] = df.set_index("date")["close"]

    available = sorted(closes.keys())
    if len(available) < 2:
        return {"task_id": "I05", "universe": "tier2", "candidate_pairs": []}

    # Compute log returns
    log_rets = {sym: np.log(closes[sym]).diff().dropna() for sym in available}

    # Align all series on common dates
    all_dates = None
    for sym in available:
        idx = set(log_rets[sym].index)
        all_dates = idx if all_dates is None else all_dates & idx
    all_dates = sorted(all_dates)

    aligned = {sym: log_rets[sym].reindex(all_dates) for sym in available}

    # Compute pairwise 60-day rolling correlation, then average
    from itertools import combinations

    pair_scores: list[tuple[str, str, float]] = []
    for sym_a, sym_b in combinations(available, 2):
        rolling_corr = aligned[sym_a].rolling(60).corr(aligned[sym_b])
        avg_corr = rolling_corr.dropna().mean()
        if not np.isnan(avg_corr):
            pair_scores.append((sym_a, sym_b, float(avg_corr)))

    # Rank by absolute correlation (descending), filter > 0.7
    pair_scores.sort(key=lambda x: abs(x[2]), reverse=True)
    top_pairs = [
        {"pair": [a, b], "avg_correlation": round(c, 4), "rank": i + 1}
        for i, (a, b, c) in enumerate(pair_scores)
        if abs(c) > 0.7
    ][:10]

    return {
        "task_id": "I05",
        "universe": "tier2",
        "selection_method": "60-day rolling correlation of log returns, ranked",
        "period": f"{start} to {end}",
        "candidate_pairs": top_pairs,
    }


def _empty_signal_result(
    task_id: str, resolution: str, start: str, end: str, warmup: int
) -> dict:
    return {
        "task_id": task_id,
        "seed": None,
        "resolution": resolution,
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": {},
    }


# ────────────────────────────────────────────────────────────────────
# Summary extraction from existing reference trades
# ────────────────────────────────────────────────────────────────────

# Primary run for each multi-run task (used for summary + default behavioral eval)
MULTI_RUN_PRIMARY = {
    "I06": "t04_r03_c03",  # Default weights (trend=0.4, reversion=0.3, carry=0.3)
    "I08": "equal_weighting",  # EW produces trades; IW may produce 0
    "I09": "builtin",  # Middle-ground risk config
    "I10": "f15_s20_t0005",  # Use grid search best config as primary
}


def _build_summary(task_id: str) -> dict:
    """Build standardized summary from existing reference trades JSON.

    For multi-run tasks (I08, I09), uses the primary run's trades file.
    """
    primary_run = MULTI_RUN_PRIMARY.get(task_id)
    if primary_run:
        ref_path = REFERENCE_DIR / f"{task_id}_reference_trades_{primary_run}.json"
    else:
        ref_path = REFERENCE_DIR / f"{task_id}_reference_trades.json"
    if not ref_path.exists():
        return {"task_id": task_id, "metrics": {}}

    with open(ref_path) as f:
        data = json.load(f)

    metrics_raw = data.get("metrics", {})
    trades = data.get("trades", [])

    def _pct_to_float(s):
        if s is None:
            return 0.0
        if isinstance(s, (int, float)):
            return float(s)
        cleaned = str(s).replace("%", "").strip()
        if not cleaned or cleaned == "None":
            return 0.0
        return float(cleaned)

    wins = sum(1 for t in trades if float(t.get("net_pnl", t.get("gross_pnl", 0))) > 0)
    total = len(trades)

    return {
        "task_id": task_id,
        "metrics": {
            "total_return_pct": _pct_to_float(metrics_raw.get("total_return", "0")),
            "sharpe_ratio": float(metrics_raw.get("sharpe_ratio", "0") or "0"),
            "max_drawdown_pct": _pct_to_float(metrics_raw.get("max_drawdown", "0")),
            "total_trades": total,
            "win_rate": round(wins / total, 4) if total > 0 else 0.0,
        },
    }


# ────────────────────────────────────────────────────────────────────
# X-series signal generators (debug tasks — fixed algorithm logic)
# ────────────────────────────────────────────────────────────────────


def _signals_x07(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """X07: EMA(20)/EMA(50) crossover on BTCUSDT daily.

    signal = +1 if price > EMA(20) AND EMA(20) > EMA(50)
             -1 if EMA(20) < EMA(50)
              0 otherwise (EMA(20) > EMA(50) but price below fast EMA)
    """
    symbol = "BTCUSDT"
    df = _load_daily_csv(symbol)
    df = _filter_dates(df, start, end)
    if df.empty:
        return _empty_signal_result("X07", "daily", start, end, 50)

    df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
    df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
    warmup = 50

    signals = []
    for _, row in df.iterrows():
        if pd.isna(row["ema20"]) or pd.isna(row["ema50"]):
            continue
        if row["close"] > row["ema20"] and row["ema20"] > row["ema50"]:
            sig = 1
        elif row["ema20"] < row["ema50"]:
            sig = -1
        else:
            sig = 0
        signals.append({"date": str(row["date"]), "signal": sig})

    return {
        "task_id": "X07",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": {symbol: signals},
    }


def _signals_x08(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """X08: ROCP(20) momentum on BTCUSDT daily.

    signal = +1 if ROCP > 5%, -1 if ROCP < -5%, 0 otherwise.
    """
    symbol = "BTCUSDT"
    df = _load_daily_csv(symbol)
    df = _filter_dates(df, start, end)
    if df.empty:
        return _empty_signal_result("X08", "daily", start, end, 20)

    df["rocp"] = df["close"].pct_change(20)
    warmup = 20

    signals = []
    for _, row in df.iterrows():
        if pd.isna(row["rocp"]):
            continue
        if row["rocp"] > 0.05:
            sig = 1
        elif row["rocp"] < -0.05:
            sig = -1
        else:
            sig = 0
        signals.append({"date": str(row["date"]), "signal": sig})

    return {
        "task_id": "X08",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": {symbol: signals},
    }


def _signals_x09(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """X09: Trend + Reversion alpha conflict on first 10 symbols, daily.

    Trend: EMA(10)/EMA(30) → direction, weight=0.65, confidence=0.8
    Reversion: RSI(14) extremes → direction, weight=0.25, confidence=0.4
    Net signal: sign of (trend_dir * 0.52 + reversion_dir * 0.10)
    """
    # X09 loads from flat universe.json (first 10 symbols)
    flat_paths = [
        BENCH_ROOT / "data" / "lean" / "universe.json",
        BENCH_ROOT / "data" / "lean_universe.json",
    ]
    symbols = None
    for p in flat_paths:
        if p.exists():
            with open(p) as f:
                all_syms = json.load(f)
            symbols = all_syms[:10]
            break
    if symbols is None:
        symbols = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
        ]

    all_signals: dict[str, list] = {}
    warmup = 30

    for sym in symbols:
        df = _load_daily_csv(sym)
        df = _filter_dates(df, start, end)
        if df.empty or len(df) < warmup:
            continue

        # Trend: EMA(10)/EMA(30)
        df["ema10"] = df["close"].ewm(span=10, adjust=False).mean()
        df["ema30"] = df["close"].ewm(span=30, adjust=False).mean()

        # Reversion: RSI(14)
        delta = df["close"].diff()
        gain = delta.clip(lower=0)
        loss = -delta.clip(upper=0)
        avg_gain = gain.rolling(14).mean()
        avg_loss = loss.rolling(14).mean()
        rs = avg_gain / avg_loss.replace(0, np.nan)
        df["rsi"] = 100 - (100 / (1 + rs))

        signals = []
        for _, row in df.iterrows():
            if pd.isna(row["ema10"]) or pd.isna(row["ema30"]) or pd.isna(row["rsi"]):
                continue

            # Trend direction (always fires)
            trend_dir = 1 if row["ema10"] > row["ema30"] else -1

            # Reversion direction (only fires at RSI extremes)
            if row["rsi"] > 70:
                rev_dir = -1
            elif row["rsi"] < 30:
                rev_dir = 1
            else:
                rev_dir = 0

            # Weighted composite: trend*0.65*0.8 + rev*0.25*0.4
            composite = trend_dir * 0.52 + rev_dir * 0.10

            if composite > 0:
                sig = 1
            elif composite < 0:
                sig = -1
            else:
                sig = 0
            signals.append({"date": str(row["date"]), "signal": sig})

        if signals:
            all_signals[sym] = signals

    return {
        "task_id": "X09",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": all_signals,
    }


# Listing dates for symbols listed after 2022-01-01 (used by X10)
_X10_LATE_LISTINGS = {
    "APTUSDT": "2022-10-19",
    "ARBUSDT": "2023-03-23",
    "OPUSDT": "2022-05-31",
    "0GUSDT": "2024-06-27",
    "1000000BOBUSDT": "2024-11-15",
    "1000000MOGUSDT": "2024-08-29",
    "1000BONKUSDT": "2023-11-22",
    "1000CATUSDT": "2024-12-16",
    "1000CHEEMSUSDT": "2024-11-29",
    "1000PEPEUSDT": "2023-05-05",
    "1000RATSUSDT": "2024-03-05",
    "1000SATSUSDT": "2023-12-13",
    "1000WHYUSDT": "2024-08-20",
}


def _signals_x10(start: str = DEFAULT_START, end: str = DEFAULT_END) -> dict:
    """X10: Momentum ranking (ROCP 20) on first 30 symbols after listing filter.

    Long top-5 → +1, short bottom-5 → -1, rest → 0.
    """
    start_date = pd.Timestamp(start).date()

    # X10 loads from flat universe.json (first 30, minus late-listed)
    flat_paths = [
        BENCH_ROOT / "data" / "lean" / "universe.json",
        BENCH_ROOT / "data" / "lean_universe.json",
    ]
    candidates = None
    for p in flat_paths:
        if p.exists():
            with open(p) as f:
                all_syms = json.load(f)
            candidates = all_syms[:30]
            break
    if candidates is None:
        candidates = [
            "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
            "ADAUSDT", "DOGEUSDT", "LINKUSDT", "AVAXUSDT", "DOTUSDT",
        ]

    # Filter out late-listed symbols
    symbols = []
    for sym in candidates:
        listing = _X10_LATE_LISTINGS.get(sym)
        if listing and pd.Timestamp(listing).date() > start_date:
            continue
        symbols.append(sym)

    warmup = 25
    top_n = 5
    bottom_n = 5

    # Load daily data
    sym_data: dict[str, pd.DataFrame] = {}
    for sym in symbols:
        df = _load_daily_csv(sym)
        df = _filter_dates(df, start, end)
        if df.empty or len(df) < warmup:
            continue
        df["rocp"] = df["close"].pct_change(20)
        sym_data[sym] = df

    if not sym_data:
        return _empty_signal_result("X10", "daily", start, end, warmup)

    # Collect all dates where at least top_n + bottom_n symbols have ROCP
    all_dates: set = set()
    for df in sym_data.values():
        for _, row in df.iterrows():
            if not pd.isna(row["rocp"]):
                all_dates.add(row["date"])

    all_signals: dict[str, list] = {sym: [] for sym in sym_data}

    for date in sorted(all_dates):
        # Collect ROCP values for this date
        rankings = []
        for sym, df in sym_data.items():
            row = df[df["date"] == date]
            if row.empty or pd.isna(row.iloc[0]["rocp"]):
                continue
            rankings.append((sym, float(row.iloc[0]["rocp"])))

        if len(rankings) < top_n + bottom_n:
            continue

        # Rank by momentum
        rankings.sort(key=lambda x: x[1], reverse=True)
        long_syms = {sym for sym, _ in rankings[:top_n]}
        short_syms = {sym for sym, _ in rankings[-bottom_n:]}

        for sym in sym_data:
            row = sym_data[sym][sym_data[sym]["date"] == date]
            if row.empty or pd.isna(row.iloc[0]["rocp"]):
                continue
            if sym in long_syms:
                sig = 1
            elif sym in short_syms:
                sig = -1
            else:
                sig = 0
            all_signals[sym].append({"date": str(date), "signal": sig})

    all_signals = {sym: sigs for sym, sigs in all_signals.items() if sigs}

    return {
        "task_id": "X10",
        "seed": None,
        "resolution": "daily",
        "start_date": start,
        "end_date": end,
        "warmup_periods": warmup,
        "signals": all_signals,
    }


# ────────────────────────────────────────────────────────────────────
# Dispatch and main
# ────────────────────────────────────────────────────────────────────

SIGNAL_GENERATORS = {
    "I01": _signals_i01,
    "I02": _signals_i02,
    "I03": _signals_i03,
    "I04": _signals_i04,
    "I05": _signals_i05,
    "I06": _signals_i06,
    "I07": _signals_i07,
    "I08": _signals_i08,
    "I09": _signals_i09,
    "I10": _signals_i10,
    "E02": _signals_e02,
    "E04": _signals_e04,
    "E05": _signals_e05,
    "X07": _signals_x07,
    "X08": _signals_x08,
    "X09": _signals_x09,
    "X10": _signals_x10,
}


def generate(task_id: str, start: str = DEFAULT_START, end: str = DEFAULT_END) -> None:
    """Generate all reference files for a task."""
    task_id = task_id.upper()
    REFERENCE_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Signals
    gen = SIGNAL_GENERATORS.get(task_id)
    if gen is None:
        print(f"ERROR: No signal generator for {task_id}")
        return

    signals = gen(start, end)
    sig_path = REFERENCE_DIR / f"{task_id}_reference_signals.json"
    with open(sig_path, "w") as f:
        json.dump(signals, f, indent=2)
    total_sigs = sum(len(v) for v in signals.get("signals", {}).values())
    print(f"  {task_id}: saved {total_sigs} signals to {sig_path.name}")

    # 2. Candidate pairs file (I05 only)
    if task_id == "I05":
        pairs = _compute_candidate_pairs(start, end)
        pairs_path = REFERENCE_DIR / "I05_candidate_pairs.json"
        with open(pairs_path, "w") as f:
            json.dump(pairs, f, indent=2)
        print(
            f"  {task_id}: saved {len(pairs['candidate_pairs'])} candidate pairs to {pairs_path.name}"
        )

    # 3. Summary (positions are reconstructed from trades at eval time)
    summary = _build_summary(task_id)
    sum_path = REFERENCE_DIR / f"{task_id}_reference_summary.json"
    with open(sum_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  {task_id}: saved summary to {sum_path.name}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate reference signals from raw data"
    )
    parser.add_argument("--task", required=True, help="Task ID (I01-I10) or 'all'")
    parser.add_argument("--start-date", default=DEFAULT_START)
    parser.add_argument("--end-date", default=DEFAULT_END)
    args = parser.parse_args()

    if args.task.lower() == "all":
        tasks = list(SIGNAL_GENERATORS.keys())
    else:
        tasks = [args.task.upper()]

    for task_id in tasks:
        print(f"Generating reference data for {task_id}...")
        generate(task_id, args.start_date, args.end_date)

    print("Done.")


if __name__ == "__main__":
    main()
