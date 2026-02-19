# Crypto-Stock Correlation Analysis
# Merges Bitcoin (UTC timestamps) with AAPL (Eastern Time) data to compute
# daily correlation and combined portfolio metrics.

import os

import numpy as np
import pandas as pd

CRYPTO_PATH = "/data/BTC_UTC.csv"
STOCK_PATH = "/data/AAPL.csv"


def load_crypto_data():
    """Load BTC price data with UTC timestamps, or generate sample data."""
    if os.path.exists(CRYPTO_PATH):
        df = pd.read_csv(CRYPTO_PATH, parse_dates=["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        return df

    # Generate synthetic BTC data with UTC timestamps
    print(
        "[INFO] CSV not found at {}. Generating sample crypto data.".format(CRYPTO_PATH)
    )
    np.random.seed(100)
    # BTC trades 24/7; we generate daily closes at 00:00 UTC
    dates_utc = pd.date_range(
        start="2022-01-01 00:00:00", end="2024-12-31 00:00:00", freq="D", tz="UTC"
    )
    n = len(dates_utc)
    daily_returns = np.random.normal(loc=0.0003, scale=0.035, size=n)
    price = 45000.0 * np.cumprod(1 + daily_returns)

    df = pd.DataFrame(
        {
            "Date": dates_utc,
            "Close": price,
            "Volume": np.random.randint(10_000, 100_000, size=n).astype(float),
        }
    )
    df["Asset"] = "BTC"
    return df


def load_stock_data():
    """Load AAPL price data with Eastern Time timestamps, or generate sample data."""
    if os.path.exists(STOCK_PATH):
        df = pd.read_csv(STOCK_PATH, parse_dates=["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        # Assume stock data is in US Eastern Time (market close at 16:00 ET)
        if df["Date"].dt.tz is None:
            df["Date"] = df["Date"].dt.tz_localize("US/Eastern")
        return df

    # Generate synthetic AAPL data with Eastern Time timestamps
    print(
        "[INFO] CSV not found at {}. Generating sample stock data.".format(STOCK_PATH)
    )
    np.random.seed(42)
    # Stock market close at 16:00 ET on business days
    bdays = pd.bdate_range(start="2022-01-03", end="2024-12-31")
    dates_et = pd.DatetimeIndex(
        [pd.Timestamp(d.year, d.month, d.day, 16, 0, 0, tz="US/Eastern") for d in bdays]
    )
    n = len(dates_et)
    daily_returns = np.random.normal(loc=0.0004, scale=0.018, size=n)
    price = 175.0 * np.cumprod(1 + daily_returns)

    df = pd.DataFrame(
        {
            "Date": dates_et,
            "Close": price,
            "Volume": np.random.randint(20_000_000, 80_000_000, size=n).astype(float),
        }
    )
    df["Asset"] = "AAPL"
    return df


def merge_datasets(crypto_df, stock_df):
    """Merge crypto and stock data on date for correlation analysis.

    BUG: Merges on calendar date extracted from timestamps WITHOUT converting
    timezones first. The crypto data has UTC timestamps and the stock data has
    Eastern Time timestamps.

    When it's 00:00 UTC on Jan 2nd (BTC daily close), it's still 19:00 ET on
    Jan 1st. So extracting the date component directly gives different dates
    for what is effectively the same trading period. This causes a systematic
    1-day misalignment in the merged data.

    The correct approach is to convert both to the same timezone before
    extracting the date:
        crypto_df['merge_date'] = crypto_df['Date'].dt.tz_convert('US/Eastern').dt.date
        stock_df['merge_date'] = stock_df['Date'].dt.date
    """
    # BUG: Extract date directly without timezone conversion
    crypto_df["merge_date"] = crypto_df["Date"].dt.date
    stock_df["merge_date"] = stock_df["Date"].dt.date

    merged = pd.merge(
        crypto_df[["merge_date", "Close", "Asset"]].rename(
            columns={"Close": "BTC_Close"}
        ),
        stock_df[["merge_date", "Close", "Asset"]].rename(
            columns={"Close": "AAPL_Close"}
        ),
        on="merge_date",
        how="inner",
        suffixes=("_btc", "_aapl"),
    )
    merged = merged.sort_values("merge_date").reset_index(drop=True)
    return merged


def compute_correlation_analysis(merged):
    """Compute return correlation and portfolio metrics."""
    merged["BTC_Return"] = merged["BTC_Close"].pct_change()
    merged["AAPL_Return"] = merged["AAPL_Close"].pct_change()

    valid = merged.dropna(subset=["BTC_Return", "AAPL_Return"])

    # Rolling 30-day correlation
    valid = valid.copy()
    valid["Rolling_Corr_30"] = (
        valid["BTC_Return"].rolling(30).corr(valid["AAPL_Return"])
    )

    # Overall correlation
    overall_corr = valid["BTC_Return"].corr(valid["AAPL_Return"])

    # 50/50 portfolio
    valid["Portfolio_Return"] = 0.5 * valid["BTC_Return"] + 0.5 * valid["AAPL_Return"]

    return valid, overall_corr


def print_results(merged, overall_corr):
    """Print correlation analysis results."""
    valid = merged.dropna(subset=["BTC_Return", "AAPL_Return"])

    btc_ann_ret = valid["BTC_Return"].mean() * 365
    btc_ann_vol = valid["BTC_Return"].std() * np.sqrt(365)
    aapl_ann_ret = valid["AAPL_Return"].mean() * 252
    aapl_ann_vol = valid["AAPL_Return"].std() * np.sqrt(252)

    port_returns = 0.5 * valid["BTC_Return"] + 0.5 * valid["AAPL_Return"]
    port_ann_ret = port_returns.mean() * 252
    port_ann_vol = port_returns.std() * np.sqrt(252)
    port_sharpe = port_ann_ret / port_ann_vol if port_ann_vol != 0 else 0.0

    print("=" * 60)
    print("  Crypto-Stock Correlation Analysis")
    print("=" * 60)
    print(f"  Merged Observations: {len(valid)}")
    print(f"  Overall Correlation: {overall_corr:.4f}")
    print()
    print("  BTC Performance:")
    print(f"    Annualized Return: {btc_ann_ret:+.2%}")
    print(f"    Annualized Vol:    {btc_ann_vol:.2%}")
    print()
    print("  AAPL Performance:")
    print(f"    Annualized Return: {aapl_ann_ret:+.2%}")
    print(f"    Annualized Vol:    {aapl_ann_vol:.2%}")
    print()
    print("  50/50 Portfolio:")
    print(f"    Annualized Return: {port_ann_ret:+.2%}")
    print(f"    Annualized Vol:    {port_ann_vol:.2%}")
    print(f"    Sharpe Ratio:      {port_sharpe:.4f}")
    print("=" * 60)

    # Show sample merged rows
    print("\nSample merged data (first 5 rows):")
    for _, row in merged.head(5).iterrows():
        print(
            f"  {row['merge_date']}  BTC={row['BTC_Close']:.2f}  "
            f"AAPL={row['AAPL_Close']:.2f}"
        )

    # Show rolling correlation summary
    if "Rolling_Corr_30" in merged.columns:
        rc = merged["Rolling_Corr_30"].dropna()
        if len(rc) > 0:
            print("\nRolling 30-day Correlation:")
            print(f"  Mean:  {rc.mean():.4f}")
            print(f"  Min:   {rc.min():.4f}")
            print(f"  Max:   {rc.max():.4f}")


if __name__ == "__main__":
    crypto_df = load_crypto_data()
    stock_df = load_stock_data()
    merged = merge_datasets(crypto_df, stock_df)
    merged, overall_corr = compute_correlation_analysis(merged)
    print_results(merged, overall_corr)
