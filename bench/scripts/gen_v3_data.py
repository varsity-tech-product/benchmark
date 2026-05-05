"""Generate the v3.0-only data files referenced by the new L1/L2 tasks.

These files don't ship in the legacy HuggingFace BDEX/A/X dataset because
they were introduced for v3.0 tasks. We synthesize them with deterministic
seeds so the benchmark is reproducible without external data fetches.

Outputs go to both:
  * bench/data/hf_cache/normal/BDEX/  (runtime data_search_dir)
  * bench/data/frozen/market/         (frozen mirror)
  * bench/data/frozen/student_code/   (for sample_code .py files)

Re-running this script overwrites existing files. Seeds are pinned per file.

Usage:
    python bench/scripts/gen_v3_data.py
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np
import pandas as pd

BENCH_ROOT = Path(__file__).resolve().parents[1]
BDEX = BENCH_ROOT / "data" / "hf_cache" / "normal" / "BDEX"
FROZEN_MARKET = BENCH_ROOT / "data" / "frozen" / "market"
FROZEN_STUDENT = BENCH_ROOT / "data" / "frozen" / "student_code"
BTC_REF = FROZEN_MARKET / "BTCUSDT_1d_2021_2024.csv"

CRYPTO_COLS = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_vol",
    "taker_buy_quote_vol",
]
EQUITY_COLS = ["Date", "Open", "High", "Low", "Close", "Volume"]


def _write_to_both(df: pd.DataFrame, fname: str) -> None:
    """Write the dataframe to both BDEX and frozen/market directories."""
    BDEX.mkdir(parents=True, exist_ok=True)
    FROZEN_MARKET.mkdir(parents=True, exist_ok=True)
    df.to_csv(BDEX / fname, index=False)
    df.to_csv(FROZEN_MARKET / fname, index=False)


def _ohlcv_from_close(close: np.ndarray, rng: np.random.Generator) -> dict:
    """Reconstruct plausible OHLC + volume series from a close series."""
    n = len(close)
    open_ = np.concatenate([[close[0]], close[:-1]]) * (1 + rng.normal(0, 0.001, n))
    intraday_vol = np.abs(rng.normal(0, 0.015, n))
    high = np.maximum(open_, close) * (1 + intraday_vol)
    low = np.minimum(open_, close) * (1 - intraday_vol)
    return {"open": open_, "high": high, "low": low, "close": close}


def gen_altcoin(symbol: str, seed: int, beta: float, idio_vol: float) -> None:
    """Daily OHLCV correlated with BTC. Crypto schema, ms timestamps."""
    btc = pd.read_csv(BTC_REF)
    n = len(btc)
    rng = np.random.default_rng(seed)
    btc_ret = btc["close"].pct_change().fillna(0).to_numpy()
    idio = rng.normal(0, idio_vol, n)
    alt_ret = beta * btc_ret + idio
    # Anchor altcoin start price to a plausible level
    start = {"SOLUSDT": 1.50, "AVAXUSDT": 3.00, "MATICUSDT": 0.018}[symbol]
    close = start * np.cumprod(1 + alt_ret)
    ohlc = _ohlcv_from_close(close, rng)
    volume = np.exp(rng.normal(np.log(close * 1e5), 0.3))
    quote_volume = volume * close
    trade_count = (volume / 100).astype(int).clip(min=1000)
    taker_buy_vol = volume * rng.uniform(0.45, 0.55, n)
    taker_buy_quote_vol = taker_buy_vol * close
    df = pd.DataFrame({
        "timestamp": btc["timestamp"],
        "open": ohlc["open"],
        "high": ohlc["high"],
        "low": ohlc["low"],
        "close": close,
        "volume": volume,
        "quote_volume": quote_volume,
        "trade_count": trade_count,
        "taker_buy_vol": taker_buy_vol,
        "taker_buy_quote_vol": taker_buy_quote_vol,
    }, columns=CRYPTO_COLS)
    _write_to_both(df, f"{symbol}_1d_2021_2024.csv")


def gen_equity_panel(
    fname: str,
    tickers: list[str],
    start_date: str,
    end_date: str,
    seed: int,
    crisis_factor: float = 0.0,
) -> pd.DatetimeIndex:
    """Wide-format daily Close panel: Date column plus one column per ticker.

    crisis_factor amplifies cross-sectional correlation in the down-quartile
    market days; used by the us_tech panel for the tail-dependence task.
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start_date, end_date, freq="B")
    n = len(dates)
    market = rng.normal(0.0005, 0.012, n)
    cols: dict = {"Date": dates.strftime("%Y-%m-%d")}
    for i, t in enumerate(tickers):
        beta = 0.8 + 0.4 * rng.random()
        idio = rng.normal(0, 0.018, n)
        if crisis_factor > 0:
            crisis_mask = market < np.quantile(market, 0.20)
            idio = np.where(
                crisis_mask, idio - crisis_factor * np.abs(market), idio
            )
        ret = beta * market + idio
        start = 50 + i * 5 + rng.uniform(-5, 5)
        close = start * np.cumprod(1 + ret)
        cols[t] = close
    df = pd.DataFrame(cols)
    _write_to_both(df, fname)
    return dates


def gen_sp500_panel() -> None:
    rng_tickers = np.random.default_rng(0)
    base = [
        "AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA", "AMD", "AVGO", "QCOM",
        "ORCL", "ADBE", "CRM", "INTC", "CSCO", "JPM", "BAC", "GS", "MS", "WFC",
        "JNJ", "PFE", "MRK", "UNH", "ABBV", "XOM", "CVX", "PG", "KO", "PEP",
    ]
    dates = gen_equity_panel(
        "sp500_top30_2018_2024.csv", base, "2018-01-02", "2024-12-31", seed=1001
    )

    # Companion panels: market_cap and book_to_price in long format
    rng = np.random.default_rng(1002)
    rows_mc = []
    rows_bp = []
    for t in base:
        scale_mc = 10 ** rng.uniform(10, 12)  # $10B..$1T
        bp_mean = rng.uniform(0.05, 0.6)
        for d in dates:
            rows_mc.append((d.strftime("%Y-%m-%d"), t, scale_mc * (1 + rng.normal(0, 0.02))))
            rows_bp.append((d.strftime("%Y-%m-%d"), t, max(0.01, bp_mean + rng.normal(0, 0.01))))
    mc = pd.DataFrame(rows_mc, columns=["Date", "Ticker", "market_cap"])
    bp = pd.DataFrame(rows_bp, columns=["Date", "Ticker", "book_to_price"])
    _write_to_both(mc, "market_cap_2018_2024.csv")
    _write_to_both(bp, "book_to_price_2018_2024.csv")


def gen_us_tech_panel() -> None:
    tickers = ["AAPL", "MSFT", "GOOG", "AMZN", "META", "NVDA", "TSLA", "AMD", "ADBE", "CRM"]
    gen_equity_panel(
        "us_tech_top10_2018_2024.csv",
        tickers,
        "2018-01-02",
        "2024-12-31",
        seed=2001,
        crisis_factor=0.03,
    )


def _qqq_base_series(seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2018-01-02", "2024-12-31", freq="B")
    n = len(dates)
    ret = rng.normal(0.0006, 0.014, n)
    close = 150.0 * np.cumprod(1 + ret)
    ohlc = _ohlcv_from_close(close, rng)
    volume = (np.exp(rng.normal(17, 0.3, n))).astype(int)
    return pd.DataFrame({
        "Date": dates.strftime("%Y-%m-%d"),
        "Open": ohlc["open"],
        "High": ohlc["high"],
        "Low": ohlc["low"],
        "Close": close,
        "Volume": volume,
    }, columns=EQUITY_COLS)


def gen_qqq_pair() -> None:
    """Two QQQ files: a clean random-walk and a corrupted twin where the
    pre-2022 segment is incorrectly split-adjusted by a factor of 2.

    The corruption deflates pre-2022 prices, producing strong fake upward
    momentum into 2022 followed by a "crash" when the adjustment becomes
    inconsistent. A momentum strategy fit on 2019-2021 thus looks brilliant
    and then breaks afterwards — the symptom L2_DIA_03 is built around.
    """
    clean = _qqq_base_series(seed=3001)
    _write_to_both(clean, "QQQ_clean_2018_2024.csv")

    corrupt = clean.copy()
    pre_mask = corrupt["Date"] < "2022-01-01"
    for col in ["Open", "High", "Low", "Close"]:
        corrupt.loc[pre_mask, col] = corrupt.loc[pre_mask, col] / 2.0
    _write_to_both(corrupt, "QQQ_corrupted_2018_2024.csv")


def gen_strategy_ab_returns() -> None:
    """Daily-return time series for two strategies in Q1 2024.

    Realised quarterly compound return: A ~12%, B ~9%. Daily-return std is
    roughly 1.6% so the 3-pp difference falls well inside one-sigma noise —
    insufficient to declare A the winner. This is the pedagogical hook
    L2_E2E_04 builds on.
    """
    rng = np.random.default_rng(4001)
    dates = pd.bdate_range("2024-01-02", "2024-03-29", freq="B")
    n = len(dates)
    sigma = 0.016
    target_a = (1.12) ** (1 / n) - 1
    target_b = (1.09) ** (1 / n) - 1
    ret_a = rng.normal(target_a, sigma, n)
    ret_b = rng.normal(target_b, sigma, n)
    # Renormalize to hit the exact targets
    ret_a += target_a - ret_a.mean()
    ret_b += target_b - ret_b.mean()
    df_a = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "return": ret_a})
    df_b = pd.DataFrame({"date": dates.strftime("%Y-%m-%d"), "return": ret_b})
    _write_to_both(df_a, "strategy_A_returns_2024.csv")
    _write_to_both(df_b, "strategy_B_returns_2024.csv")


def write_buggy_code() -> None:
    """Write the two buggy student-code samples into frozen/student_code/."""
    FROZEN_STUDENT.mkdir(parents=True, exist_ok=True)

    multi_bug = '''"""Long-only SMA-crossover backtest on BTCUSDT daily.

This file is intentionally buggy. Three interacting bugs hide each other
in the aggregate report — the headline numbers look modestly plausible.
Diagnose the bugs in L1_DBG_04.
"""

from __future__ import annotations

import sys
import numpy as np
import pandas as pd


def run(csv_path: str = "/workspace/data/BTCUSDT_1d_2021_2024.csv") -> dict:
    df = pd.read_csv(csv_path)
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
    df = df.sort_values("timestamp").reset_index(drop=True)

    df["sma_fast"] = df["close"].rolling(20).mean()
    df["sma_slow"] = df["close"].rolling(50).mean()
    df["signal"] = (df["sma_fast"] > df["sma_slow"]).astype(int)

    # B2: signal consumed at the same bar (no shift) — look-ahead.
    df["position"] = df["signal"]

    df["bar_return"] = df["close"].pct_change().fillna(0)
    df["strategy_return"] = df["position"] * df["bar_return"]

    # B1: 5bp fee model applied to BOTH entry AND exit on a position change.
    fee_bps = 5e-4
    pos_change = df["position"].diff().abs().fillna(0)
    fees = pos_change * fee_bps * 2.0
    df["net_return"] = df["strategy_return"] - fees

    total_return = float((1 + df["net_return"]).prod() - 1)
    daily_mean = float(df["net_return"].mean())
    daily_std = float(df["net_return"].std(ddof=0))
    # B3: annualization uses sqrt(365) instead of sqrt(252).
    sharpe = float(daily_mean / daily_std * np.sqrt(365))

    return {
        "total_return": total_return,
        "sharpe": sharpe,
        "n_trades": int(pos_change.sum() / 2),
    }


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "/workspace/data/BTCUSDT_1d_2021_2024.csv"
    print(run(path))
'''
    (FROZEN_STUDENT / "multi_bug_strategy.py").write_text(multi_bug)

    stats_misuse = '''"""Statistical-test misuse demo: 100 momentum signals scanned, t-test
applied to the best-performing one with no multiple-testing correction
and an i.i.d. assumption that ignores autocorrelation.

This file is intentionally buggy. Diagnose the three statistical bugs
in L1_DBG_05.
"""

from __future__ import annotations

import itertools
import sys
from typing import Iterable

import numpy as np
import pandas as pd
from scipy import stats


def momentum_returns(
    df: pd.DataFrame,
    lookback: int,
    holding: int,
) -> np.ndarray:
    """Long-only momentum: position = sign(rolling lookback return),
    held for `holding` days. Returns a daily strategy-return series.
    """
    px = df["Close"].to_numpy()
    n = len(px)
    bar_ret = np.diff(px) / px[:-1]
    bar_ret = np.concatenate([[0.0], bar_ret])
    rolling = pd.Series(bar_ret).rolling(lookback).sum().to_numpy()
    pos = (rolling > 0).astype(float)
    # B2: use today's pos against today's return (no shift) — adds noise but
    # is incidental to the headline statistical bugs.
    strat = pos * bar_ret
    if holding > 1:
        strat = pd.Series(strat).rolling(holding).mean().fillna(0).to_numpy()
    return strat


def search_signals(df: pd.DataFrame) -> tuple[tuple[int, int], np.ndarray, float]:
    """Sweep 100 (lookback, holding) combos and return the most significant
    one by naive one-sided t-test on daily strategy returns.
    """
    grid: Iterable[tuple[int, int]] = list(itertools.product(
        [5, 10, 15, 20, 30, 40, 50, 60, 80, 100],
        [1, 2, 3, 5, 7, 10, 14, 21, 30, 60],
    ))
    best_p = 1.0
    best_combo = (5, 1)
    best_returns = np.zeros(len(df))
    for lb, hd in grid:
        rets = momentum_returns(df, lb, hd)
        # B1 + B3: one-sided t-test, no multiple-testing correction, ignores
        # autocorrelation in `rets`.
        t_stat, p_two = stats.ttest_1samp(rets, 0.0)
        p_one = p_two / 2.0 if t_stat > 0 else 1.0 - p_two / 2.0
        if p_one < best_p:
            best_p = p_one
            best_combo = (lb, hd)
            best_returns = rets
    return best_combo, best_returns, best_p


def main(csv_path: str) -> None:
    df = pd.read_csv(csv_path)
    combo, rets, p = search_signals(df)
    print(f"best (lookback, holding) = {combo}")
    print(f"naive one-sided p-value  = {p:.4f}")
    print("we found a significant signal at p < 0.01" if p < 0.01 else "no signal")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/workspace/data/AAPL_2018_2024.csv")
'''
    (FROZEN_STUDENT / "stats_misuse.py").write_text(stats_misuse)


def main() -> None:
    if not BTC_REF.exists():
        raise FileNotFoundError(
            f"BTC reference not found at {BTC_REF}; cannot anchor altcoin generation"
        )

    print("Generating altcoins...")
    gen_altcoin("SOLUSDT", seed=101, beta=0.85, idio_vol=0.04)
    gen_altcoin("AVAXUSDT", seed=102, beta=0.95, idio_vol=0.045)
    gen_altcoin("MATICUSDT", seed=103, beta=1.05, idio_vol=0.05)

    print("Generating SP500 top-30 + factor inputs...")
    gen_sp500_panel()

    print("Generating US-tech top-10 panel (with crisis tail dependence)...")
    gen_us_tech_panel()

    print("Generating QQQ clean + corrupted pair...")
    gen_qqq_pair()

    print("Generating strategy A/B daily returns (Q1 2024)...")
    gen_strategy_ab_returns()

    print("Writing buggy student-code samples...")
    write_buggy_code()

    print("Done.")


if __name__ == "__main__":
    main()
