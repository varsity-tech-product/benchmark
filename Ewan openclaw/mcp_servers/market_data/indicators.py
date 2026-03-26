"""
Technical indicator computation engine.

All calculations are pure math (pandas + pandas_ta_classic).
No LLM calls. Returns precise numerical values for every indicator.
"""

import numpy as np
import pandas as pd
import pandas_ta_classic as ta


def compute_moving_averages(df: pd.DataFrame) -> dict:
    """Compute MA5/10/20/60/120/250 from close prices."""
    periods = [5, 10, 20, 60, 120, 250]
    result = {}
    for p in periods:
        key = f"MA{p}"
        if len(df) >= p:
            result[key] = round(float(df["close"].rolling(p).mean().iloc[-1]), 2)
        else:
            result[key] = None
    return result


def compute_macd(df: pd.DataFrame) -> dict:
    """Compute MACD (DIF, DEA, histogram) and determine signal."""
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    if macd is None or macd.empty:
        return {"dif": None, "dea": None, "hist": None, "signal": "unknown"}

    cols = macd.columns.tolist()
    dif_col = [
        c
        for c in cols
        if "MACD_" in c and "h" not in c.lower() and "s" not in c.lower()
    ][0]
    hist_col = [c for c in cols if "MACDh_" in c or "MACD_hist" in c.lower()][0]
    sig_col = [c for c in cols if "MACDs_" in c or "MACD_signal" in c.lower()][0]

    dif = round(float(macd[dif_col].iloc[-1]), 4)
    dea = round(float(macd[sig_col].iloc[-1]), 4)
    hist = round(float(macd[hist_col].iloc[-1]), 4)

    if len(macd) >= 2:
        prev_hist = float(macd[hist_col].iloc[-2])
        if prev_hist <= 0 < hist:
            signal = "golden_cross"
        elif prev_hist >= 0 > hist:
            signal = "death_cross"
        elif hist > 0:
            signal = "bullish"
        else:
            signal = "bearish"
    else:
        signal = "unknown"

    return {"dif": dif, "dea": dea, "hist": hist, "signal": signal}


def compute_rsi(df: pd.DataFrame) -> dict:
    """Compute RSI for 6/12/24 periods with overbought/oversold labels."""
    result = {}
    for period in [6, 12, 24]:
        rsi_series = ta.rsi(df["close"], length=period)
        if rsi_series is not None and len(rsi_series) > 0:
            result[f"rsi{period}"] = round(float(rsi_series.iloc[-1]), 2)
        else:
            result[f"rsi{period}"] = None

    rsi12 = result.get("rsi12")
    if rsi12 is not None:
        if rsi12 > 80:
            result["status"] = "overbought"
        elif rsi12 > 70:
            result["status"] = "approaching_overbought"
        elif rsi12 < 20:
            result["status"] = "oversold"
        elif rsi12 < 30:
            result["status"] = "approaching_oversold"
        else:
            result["status"] = "neutral"
    else:
        result["status"] = "unknown"

    return result


def compute_kdj(df: pd.DataFrame) -> dict:
    """Compute KDJ indicator and determine signal."""
    stoch = ta.stoch(df["high"], df["low"], df["close"], k=9, d=3, smooth_k=3)
    if stoch is None or stoch.empty:
        return {"k": None, "d": None, "j": None, "signal": "unknown"}

    cols = stoch.columns.tolist()
    k_col = [c for c in cols if c.startswith("STOCHk_")][0]
    d_col = [c for c in cols if c.startswith("STOCHd_")][0]

    k = round(float(stoch[k_col].iloc[-1]), 2)
    d = round(float(stoch[d_col].iloc[-1]), 2)
    j = round(3 * k - 2 * d, 2)

    if j > 100:
        signal = "overbought"
    elif j < 0:
        signal = "oversold"
    elif len(stoch) >= 2:
        prev_k = float(stoch[k_col].iloc[-2])
        prev_d = float(stoch[d_col].iloc[-2])
        if prev_k <= prev_d and k > d:
            signal = "golden_cross"
        elif prev_k >= prev_d and k < d:
            signal = "death_cross"
        elif k > d:
            signal = "bullish"
        else:
            signal = "bearish"
    else:
        signal = "unknown"

    return {"k": k, "d": d, "j": j, "signal": signal}


def compute_bollinger(df: pd.DataFrame) -> dict:
    """Compute Bollinger Bands (20-period, 2 std)."""
    bbands = ta.bbands(df["close"], length=20, std=2)
    if bbands is None or bbands.empty:
        return {"upper": None, "mid": None, "lower": None}

    cols = bbands.columns.tolist()
    bbl_col = [c for c in cols if c.startswith("BBL_")][0]
    bbm_col = [c for c in cols if c.startswith("BBM_")][0]
    bbu_col = [c for c in cols if c.startswith("BBU_")][0]

    return {
        "upper": round(float(bbands[bbu_col].iloc[-1]), 2),
        "mid": round(float(bbands[bbm_col].iloc[-1]), 2),
        "lower": round(float(bbands[bbl_col].iloc[-1]), 2),
    }


def compute_support_resistance(
    df: pd.DataFrame, bollinger: dict | None = None, lookback: int = 60
) -> dict:
    """
    Compute support and resistance levels from multiple sources:
    1. Recent highs/lows (N-day extremes)
    2. Bollinger band boundaries (reuse pre-computed if provided)
    3. Volume-weighted price distribution (chip density zone)
    """
    recent = df.tail(lookback)
    price = float(df["close"].iloc[-1])

    high_60d = round(float(recent["high"].max()), 2)
    low_60d = round(float(recent["low"].min()), 2)
    high_20d = round(float(df.tail(20)["high"].max()), 2)
    low_20d = round(float(df.tail(20)["low"].min()), 2)

    bb = bollinger if bollinger is not None else compute_bollinger(df)

    chip_lower, chip_upper = _compute_chip_density(recent)

    resistance_candidates = set()
    for val in [high_20d, high_60d, bb.get("upper")]:
        if val is not None and val > price:
            resistance_candidates.add(val)
    if chip_upper is not None and chip_upper > price:
        resistance_candidates.add(chip_upper)

    support_candidates = set()
    for val in [low_20d, low_60d, bb.get("lower")]:
        if val is not None and val < price:
            support_candidates.add(val)
    if chip_lower is not None and chip_lower < price:
        support_candidates.add(chip_lower)

    return {
        "resistance": sorted(resistance_candidates)[:3],
        "support": sorted(support_candidates, reverse=True)[:3],
    }


def _compute_chip_density(df: pd.DataFrame) -> tuple[float | None, float | None]:
    """
    Compute chip density zone: the price range containing 70% of
    volume-weighted trading.

    Returns:
        (lower_bound, upper_bound) of the chip density zone.
    """
    if "volume" not in df.columns or df["volume"].sum() == 0:
        return None, None

    typical = (df["high"] + df["low"] + df["close"]) / 3
    total_vol = df["volume"].sum()

    price_min = float(typical.min())
    price_max = float(typical.max())
    if price_max == price_min:
        return None, None

    n_bins = 50
    bins = np.linspace(price_min, price_max, n_bins + 1)
    bin_indices = np.digitize(typical.values, bins) - 1
    bin_indices = np.clip(bin_indices, 0, n_bins - 1)

    vol_per_bin = np.zeros(n_bins)
    for i, vol in enumerate(df["volume"].values):
        vol_per_bin[bin_indices[i]] += vol

    sorted_indices = np.argsort(vol_per_bin)[::-1]
    cumulative = 0.0
    selected_bins = []
    for idx in sorted_indices:
        cumulative += vol_per_bin[idx]
        selected_bins.append(idx)
        if cumulative >= total_vol * 0.7:
            break

    if not selected_bins:
        return None, None

    lower_bin = min(selected_bins)
    upper_bin = max(selected_bins)
    chip_lower = round(float(bins[lower_bin]), 2)
    chip_upper = round(float(bins[upper_bin + 1]), 2)

    return chip_lower, chip_upper


def compute_volume_metrics(df: pd.DataFrame) -> dict:
    """Compute volume ratio and turnover rate for the latest bar."""
    if len(df) < 6:
        return {"volume_ratio": None, "turnover_rate": None}

    today_vol = float(df["volume"].iloc[-1])
    avg_5d_vol = float(df["volume"].iloc[-6:-1].mean())

    volume_ratio = round(today_vol / avg_5d_vol, 2) if avg_5d_vol > 0 else None

    turnover_rate = None
    if "turnover_rate" in df.columns:
        turnover_rate = round(float(df["turnover_rate"].iloc[-1]), 2)

    return {"volume_ratio": volume_ratio, "turnover_rate": turnover_rate}


def compute_all_indicators(df: pd.DataFrame) -> dict:
    """
    Master function: compute ALL technical indicators for a single stock.
    Input: OHLCV DataFrame with columns [open, high, low, close, volume].
    Output: dict with all indicator groups.
    """
    price = round(float(df["close"].iloc[-1]), 2)

    ma = compute_moving_averages(df)
    macd = compute_macd(df)
    rsi = compute_rsi(df)
    kdj = compute_kdj(df)
    bollinger = compute_bollinger(df)
    sr = compute_support_resistance(df, bollinger=bollinger)
    vol = compute_volume_metrics(df)

    return {
        "price": price,
        "ma": ma,
        "macd": macd,
        "rsi": rsi,
        "kdj": kdj,
        "bollinger": bollinger,
        "support": sr["support"],
        "resistance": sr["resistance"],
        "volume_ratio": vol["volume_ratio"],
        "turnover_rate": vol["turnover_rate"],
    }
