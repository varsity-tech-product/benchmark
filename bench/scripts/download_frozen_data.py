#!/usr/bin/env python3
"""
download_frozen_data.py - Generates all 7 frozen market-data CSV files for QuantTutorBench.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pandas as pd

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR.parent / "frozen"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


def _trading_dates(start, end):
    return pd.bdate_range(start=start, end=end, freq="B")


def _generate_ohlcv(
    dates,
    start_price,
    annual_drift,
    annual_vol,
    avg_volume,
    seed,
    price_targets=None,
    split_date=None,
    split_ratio=1.0,
):
    rng = np.random.default_rng(seed)
    n = len(dates)

    if price_targets:
        target_dates = [pd.Timestamp(d) for d, _ in price_targets]
        target_prices = [p for _, p in price_targets]
        target_dates = [dates[0]] + target_dates
        target_prices = [start_price] + target_prices
        target_idx = np.array([(d - dates[0]).days for d in target_dates], dtype=float)
        all_idx = np.array([(d - dates[0]).days for d in dates], dtype=float)
        target_traj = np.interp(all_idx, target_idx, target_prices)
    else:
        target_traj = None

    dt = 1 / 252
    drift = annual_drift * dt
    vol = annual_vol * np.sqrt(dt)

    close = np.empty(n)
    close[0] = start_price

    for i in range(1, n):
        shock = rng.normal(0, 1)
        ret = drift + vol * shock
        if target_traj is not None:
            gap = np.log(target_traj[i] / close[i - 1])
            ret += 0.03 * gap
        close[i] = close[i - 1] * np.exp(ret)

    if split_date:
        split_ts = pd.Timestamp(split_date)
        mask = dates < split_ts
        close[mask] *= split_ratio

    intraday_range = vol * close * 0.6
    open_ = close + rng.normal(0, 1, n) * intraday_range * 0.3
    high = np.maximum(open_, close) + np.abs(rng.normal(0, 1, n)) * intraday_range * 0.5
    low = np.minimum(open_, close) - np.abs(rng.normal(0, 1, n)) * intraday_range * 0.5
    high = np.maximum(high, np.maximum(open_, close))
    low = np.minimum(low, np.minimum(open_, close))
    low = np.maximum(low, 0.01)
    volume = rng.lognormal(mean=np.log(avg_volume), sigma=0.35, size=n).astype(int)

    return pd.DataFrame(
        {
            "Date": dates.strftime("%Y-%m-%d"),
            "Open": np.round(open_, 2),
            "High": np.round(high, 2),
            "Low": np.round(low, 2),
            "Close": np.round(close, 2),
            "Volume": volume,
        }
    )


def generate_aapl():
    dates = _trading_dates("2018-01-02", "2024-12-31")
    targets = [
        ("2018-12-31", 39.0),
        ("2019-06-30", 50.0),
        ("2019-12-31", 73.0),
        ("2020-03-23", 57.0),
        ("2020-08-28", 125.0),
        ("2021-01-04", 132.0),
        ("2021-12-31", 177.0),
        ("2022-06-30", 137.0),
        ("2022-12-31", 130.0),
        ("2023-06-30", 193.0),
        ("2023-12-31", 192.0),
        ("2024-06-30", 210.0),
        ("2024-12-31", 190.0),
    ]
    return _generate_ohlcv(
        dates,
        42.0,
        0.15,
        0.30,
        80_000_000,
        100,
        price_targets=targets,
        split_date="2020-08-31",
        split_ratio=4.0,
    )


def generate_tsla():
    dates = _trading_dates("2020-01-02", "2024-12-31")
    targets = [
        ("2020-03-18", 72.0),
        ("2020-08-31", 450.0),
        ("2020-12-31", 250.0),
        ("2021-01-25", 290.0),
        ("2021-11-04", 400.0),
        ("2022-06-30", 220.0),
        ("2022-12-31", 120.0),
        ("2023-01-06", 108.0),
        ("2023-06-30", 260.0),
        ("2023-12-31", 250.0),
        ("2024-04-22", 150.0),
        ("2024-12-31", 250.0),
    ]
    return _generate_ohlcv(
        dates, 90.0, 0.20, 0.55, 120_000_000, 200, price_targets=targets
    )


def generate_spy():
    dates = _trading_dates("2018-01-02", "2024-12-31")
    targets = [
        ("2018-12-24", 240.0),
        ("2019-06-30", 295.0),
        ("2019-12-31", 321.0),
        ("2020-02-19", 339.0),
        ("2020-03-23", 220.0),
        ("2020-08-31", 350.0),
        ("2020-12-31", 374.0),
        ("2021-06-30", 430.0),
        ("2021-12-31", 475.0),
        ("2022-06-17", 373.0),
        ("2022-12-31", 383.0),
        ("2023-06-30", 445.0),
        ("2023-12-31", 475.0),
        ("2024-06-30", 545.0),
        ("2024-12-31", 600.0),
    ]
    return _generate_ohlcv(
        dates, 270.0, 0.10, 0.18, 75_000_000, 300, price_targets=targets
    )


def generate_aapl_dirty(aapl_clean):
    rng = np.random.default_rng(42)
    df = aapl_clean.copy()
    n = len(df)
    for col in ["Open", "High", "Low", "Close", "Volume"]:
        mask = rng.random(n) < 0.05
        df.loc[mask, col] = np.nan
    dup_idx = rng.choice(n, size=int(n * 0.02), replace=False)
    dups = df.iloc[dup_idx].copy()
    df = pd.concat([df, dups], ignore_index=True)
    pre_split = df["Date"] < "2020-08-31"
    pre_idx = df.index[pre_split]
    if len(pre_idx) > 0:
        bad_idx = rng.choice(pre_idx, size=int(len(pre_idx) * 0.03), replace=False)
        for col in ["Open", "High", "Low", "Close"]:
            df.loc[bad_idx, col] = df.loc[bad_idx, col] * 4
    neg_idx = rng.choice(n, size=int(n * 0.01), replace=False)
    df.loc[neg_idx, "Volume"] = -np.abs(df.loc[neg_idx, "Volume"])
    df = df.sort_values("Date", kind="mergesort").reset_index(drop=True)
    return df


def generate_pair():
    dates = _trading_dates("2018-01-02", "2024-12-31")
    n = len(dates)
    rng = np.random.default_rng(500)
    corr = 0.85
    z1 = rng.normal(0, 1, n)
    z2 = corr * z1 + np.sqrt(1 - corr**2) * rng.normal(0, 1, n)
    aapl_log_ret = 0.12 / 252 + (0.28 / np.sqrt(252)) * z1
    aapl_log_ret[0] = 0
    aapl_close = 42.0 * np.exp(np.cumsum(aapl_log_ret))
    blend = np.linspace(0, 1, n)
    aapl_close = aapl_close * np.exp(blend * np.log(190.0 / aapl_close[-1]))
    msft_log_ret = 0.14 / 252 + (0.26 / np.sqrt(252)) * z2
    msft_log_ret[0] = 0
    msft_close = 90.0 * np.exp(np.cumsum(msft_log_ret))
    msft_close = msft_close * np.exp(blend * np.log(375.0 / msft_close[-1]))
    return pd.DataFrame(
        {
            "Date": dates.strftime("%Y-%m-%d"),
            "AAPL_Close": np.round(aapl_close, 2),
            "MSFT_Close": np.round(msft_close, 2),
        }
    )


def generate_tick_data():
    rng = np.random.default_rng(600)
    n_ticks = 50_000
    day_start = pd.Timestamp("2024-01-15 09:30:00")
    total_seconds = 6.5 * 3600
    offsets = np.sort(rng.uniform(0, total_seconds, n_ticks))
    timestamps = [
        (day_start + pd.Timedelta(seconds=float(s))).strftime("%Y-%m-%d %H:%M:%S.%f")[
            :-3
        ]
        for s in offsets
    ]
    tick_vol = 0.0003
    log_rets = rng.normal(0, tick_vol, n_ticks)
    log_rets[0] = 0
    price = 185.0 * np.exp(np.cumsum(log_rets))
    price = np.round(price, 2)
    half_spread = rng.uniform(0.005, 0.015, n_ticks)
    bid = np.round(price - half_spread, 2)
    ask = np.round(price + half_spread, 2)
    volume = (rng.exponential(scale=200, size=n_ticks)).astype(int)
    volume = np.maximum(volume, 1)
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "price": price,
            "volume": volume,
            "bid": bid,
            "ask": ask,
        }
    )


def generate_multi_factor():
    rng = np.random.default_rng(42)
    months = pd.date_range("2024-01-31", periods=12, freq="ME")
    stock_names = [f"STOCK_{i+1:02d}" for i in range(50)]
    rows = []
    for stock in stock_names:
        base_price = rng.uniform(20, 500)
        base_pe = rng.uniform(8, 40)
        base_roe = rng.uniform(0.02, 0.35)
        close_prev = base_price
        for month_end in months:
            monthly_ret = rng.normal(0.005, 0.06)
            close = max(close_prev * (1 + monthly_ret), 1.0)
            pe = max(base_pe * (1 + rng.normal(0, 0.05)), 1.0)
            roe = base_roe * (1 + rng.normal(0, 0.03))
            rows.append(
                {
                    "Stock": stock,
                    "Date": month_end.strftime("%Y-%m-%d"),
                    "Close": round(close, 2),
                    "PE_Ratio": round(pe, 2),
                    "Monthly_Return": round(monthly_ret, 4),
                    "ROE": round(roe, 4),
                }
            )
            close_prev = close
    return pd.DataFrame(rows)


def main():
    print("=" * 60)
    print("QuantTutorBench - Frozen Data Generator")
    print("=" * 60)
    print(f"Output directory: {OUTPUT_DIR}")
    print()

    print("[1/7] Generating AAPL_2018_2024.csv ...")
    aapl = generate_aapl()
    aapl.to_csv(OUTPUT_DIR / "AAPL_2018_2024.csv", index=False)
    print(
        f"       {len(aapl)} rows, Close range [{aapl['Close'].min():.2f}, {aapl['Close'].max():.2f}]"
    )

    print("[2/7] Generating TSLA_2020_2024.csv ...")
    tsla = generate_tsla()
    tsla.to_csv(OUTPUT_DIR / "TSLA_2020_2024.csv", index=False)
    print(
        f"       {len(tsla)} rows, Close range [{tsla['Close'].min():.2f}, {tsla['Close'].max():.2f}]"
    )

    print("[3/7] Generating SPY_2018_2024.csv ...")
    spy = generate_spy()
    spy.to_csv(OUTPUT_DIR / "SPY_2018_2024.csv", index=False)
    print(
        f"       {len(spy)} rows, Close range [{spy['Close'].min():.2f}, {spy['Close'].max():.2f}]"
    )

    print("[4/7] Generating AAPL_dirty.csv ...")
    aapl_dirty = generate_aapl_dirty(aapl)
    aapl_dirty.to_csv(OUTPUT_DIR / "AAPL_dirty.csv", index=False)
    nan_count = aapl_dirty.isna().sum().sum()
    dup_count = aapl_dirty.duplicated(subset=["Date"], keep=False).sum()
    neg_vol = (aapl_dirty["Volume"] < 0).sum()
    print(
        f"       {len(aapl_dirty)} rows, NaN cells={nan_count}, dup-date rows={dup_count}, neg-vol rows={neg_vol}"
    )

    print("[5/7] Generating AAPL_MSFT_pair.csv ...")
    pair = generate_pair()
    pair.to_csv(OUTPUT_DIR / "AAPL_MSFT_pair.csv", index=False)
    actual_corr = pair["AAPL_Close"].corr(pair["MSFT_Close"])
    print(f"       {len(pair)} rows, correlation={actual_corr:.4f}")

    print("[6/7] Generating tick_data_sample.csv ...")
    ticks = generate_tick_data()
    ticks.to_csv(OUTPUT_DIR / "tick_data_sample.csv", index=False)
    print(
        f"       {len(ticks)} rows, price range [{ticks['price'].min():.2f}, {ticks['price'].max():.2f}]"
    )

    print("[7/7] Generating multi_factor.csv ...")
    mf = generate_multi_factor()
    mf.to_csv(OUTPUT_DIR / "multi_factor.csv", index=False)
    print(
        f"       {len(mf)} rows, {mf['Stock'].nunique()} stocks x {mf['Date'].nunique()} months"
    )

    print()
    print("=" * 60)
    print("All 7 files written successfully.")
    print("=" * 60)


if __name__ == "__main__":
    main()
