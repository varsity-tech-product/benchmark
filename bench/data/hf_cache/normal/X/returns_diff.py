# Daily Returns Computation for AAPL
# Computes daily returns of AAPL stock and summarizes return statistics.
# Used as a foundation for risk analysis and portfolio construction.

import os

import numpy as np
import pandas as pd

DATA_PATH = "/data/AAPL.csv"


def load_data():
    """Load AAPL price data from CSV, or generate sample data if not available."""
    if os.path.exists(DATA_PATH):
        df = pd.read_csv(DATA_PATH, parse_dates=["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        return df

    # Generate synthetic AAPL-like data if CSV not found
    print("[INFO] CSV not found at {}. Generating sample data.".format(DATA_PATH))
    np.random.seed(42)
    dates = pd.bdate_range(start="2020-01-02", end="2024-12-31")
    n = len(dates)

    daily_returns = np.random.normal(loc=0.0004, scale=0.018, size=n)
    price = 150.0 * np.cumprod(1 + daily_returns)

    df = pd.DataFrame(
        {
            "Date": dates[:n],
            "Open": price * (1 + np.random.uniform(-0.005, 0.005, n)),
            "High": price * (1 + np.abs(np.random.normal(0, 0.01, n))),
            "Low": price * (1 - np.abs(np.random.normal(0, 0.01, n))),
            "Close": price,
            "Volume": np.random.randint(20_000_000, 80_000_000, size=n),
        }
    )
    return df


def compute_daily_returns(df):
    """Compute daily returns from closing prices.

    BUG: Uses diff() instead of pct_change().
    diff() computes the absolute dollar change: Close[t] - Close[t-1]
    pct_change() computes the percentage return: (Close[t] - Close[t-1]) / Close[t-1]

    For a stock at $150, a $3 move is 2%. But diff() returns 3.0, not 0.02.
    This makes all downstream statistics (mean return, volatility, Sharpe) incorrect.
    """
    df["Daily_Return"] = df["Close"].diff()
    return df


def compute_statistics(df):
    """Compute descriptive statistics on daily returns."""
    returns = df["Daily_Return"].dropna()

    stats = {
        "count": len(returns),
        "mean": returns.mean(),
        "std": returns.std(),
        "min": returns.min(),
        "max": returns.max(),
        "skewness": returns.skew(),
        "kurtosis": returns.kurtosis(),
        "median": returns.median(),
        "q25": returns.quantile(0.25),
        "q75": returns.quantile(0.75),
    }

    # Annualized metrics
    stats["annualized_return"] = stats["mean"] * 252
    stats["annualized_vol"] = stats["std"] * np.sqrt(252)
    stats["sharpe_ratio"] = (
        stats["annualized_return"] / stats["annualized_vol"]
        if stats["annualized_vol"] != 0
        else 0.0
    )

    return stats


def compute_cumulative_returns(df):
    """Compute cumulative returns over time."""
    df["Cumulative_Return"] = (1 + df["Daily_Return"]).cumprod()
    return df


def print_results(df, stats):
    """Print return analysis summary."""
    print("=" * 60)
    print("  AAPL Daily Return Analysis")
    print("=" * 60)
    print(
        f"  Period:              {df['Date'].iloc[0].date()} to {df['Date'].iloc[-1].date()}"
    )
    print(f"  Trading Days:        {stats['count']}")
    print()
    print("  Return Statistics:")
    print(f"    Mean Daily Return:   {stats['mean']:.6f}")
    print(f"    Std Dev (Daily):     {stats['std']:.6f}")
    print(f"    Min Daily Return:    {stats['min']:.6f}")
    print(f"    Max Daily Return:    {stats['max']:.6f}")
    print(f"    Median:              {stats['median']:.6f}")
    print(f"    25th Percentile:     {stats['q25']:.6f}")
    print(f"    75th Percentile:     {stats['q75']:.6f}")
    print(f"    Skewness:            {stats['skewness']:.4f}")
    print(f"    Kurtosis:            {stats['kurtosis']:.4f}")
    print()
    print("  Annualized Metrics:")
    print(f"    Annualized Return:   {stats['annualized_return']:.6f}")
    print(f"    Annualized Vol:      {stats['annualized_vol']:.6f}")
    print(f"    Sharpe Ratio:        {stats['sharpe_ratio']:.4f}")
    print("=" * 60)

    # Show first few return values
    print("\nSample returns (first 10 trading days):")
    sample = df.dropna(subset=["Daily_Return"]).head(10)
    for _, row in sample.iterrows():
        print(
            f"  {row['Date'].date()}  Close={row['Close']:.2f}  "
            f"Return={row['Daily_Return']:.6f}"
        )


if __name__ == "__main__":
    df = load_data()
    df = compute_daily_returns(df)
    stats = compute_statistics(df)
    df = compute_cumulative_returns(df)
    print_results(df, stats)
