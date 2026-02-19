# Simple Moving Average Crossover with Look-Ahead Bias
# Implements an SMA crossover strategy on AAPL data.
# Generates signals and computes strategy returns.

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
    price = 300.0 * np.cumprod(1 + daily_returns)

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


def compute_indicators(df):
    """Compute 10-day and 30-day simple moving averages."""
    df["SMA_10"] = df["Close"].rolling(window=10).mean()
    df["SMA_30"] = df["Close"].rolling(window=30).mean()
    return df


def generate_signals(df):
    """Generate trading signals based on MA crossover.

    BUG: Look-ahead bias. The signal on day t is computed using day t's close
    price (via the MA values), and then applied to day t's return.

    In a real trading system, you can only observe the close price at the END
    of day t. So a signal generated from day t's close can only be acted on
    starting day t+1. The correct approach is to shift the position by 1 day:
        df['Position'] = signal.shift(1)

    Without the shift, the strategy "knows" today's close before it happens,
    inflating performance.
    """
    df["Signal"] = 0.0
    df.loc[df["SMA_10"] > df["SMA_30"], "Signal"] = 1.0  # Long
    df.loc[df["SMA_10"] <= df["SMA_30"], "Signal"] = -1.0  # Short

    # BUG: Position uses today's signal for today's trade (no shift)
    # Correct version: df["Position"] = df["Signal"].shift(1)
    df["Position"] = df["Signal"]

    return df


def compute_strategy_returns(df):
    """Compute daily strategy returns and cumulative performance."""
    df["Daily_Return"] = df["Close"].pct_change()
    df["Strategy_Return"] = df["Position"] * df["Daily_Return"]

    df["Cumulative_Strategy"] = (1 + df["Strategy_Return"]).cumprod()
    df["Cumulative_BuyHold"] = (1 + df["Daily_Return"]).cumprod()
    return df


def compute_metrics(returns):
    """Compute annualized performance metrics."""
    mean_ret = returns.mean() * 252
    vol = returns.std() * np.sqrt(252)
    sharpe = mean_ret / vol if vol != 0 else 0.0

    # Maximum drawdown
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_dd = drawdown.min()

    return {
        "annualized_return": mean_ret,
        "annualized_vol": vol,
        "sharpe_ratio": sharpe,
        "max_drawdown": max_dd,
    }


def print_results(df):
    """Print strategy performance."""
    valid = df.dropna(subset=["Strategy_Return", "Daily_Return"])
    strat_metrics = compute_metrics(valid["Strategy_Return"])
    bh_metrics = compute_metrics(valid["Daily_Return"])

    print("=" * 60)
    print("  SMA Crossover Strategy Results (AAPL)")
    print("=" * 60)
    print(
        f"  Period:            {df['Date'].iloc[0].date()} to {df['Date'].iloc[-1].date()}"
    )
    print("  SMA Windows:       10-day / 30-day")
    print()
    print("  Strategy Performance:")
    print(f"    Annualized Return: {strat_metrics['annualized_return']:+.2%}")
    print(f"    Annualized Vol:    {strat_metrics['annualized_vol']:.2%}")
    print(f"    Sharpe Ratio:      {strat_metrics['sharpe_ratio']:.4f}")
    print(f"    Max Drawdown:      {strat_metrics['max_drawdown']:.2%}")
    print()
    print("  Buy & Hold Performance:")
    print(f"    Annualized Return: {bh_metrics['annualized_return']:+.2%}")
    print(f"    Annualized Vol:    {bh_metrics['annualized_vol']:.2%}")
    print(f"    Sharpe Ratio:      {bh_metrics['sharpe_ratio']:.4f}")
    print(f"    Max Drawdown:      {bh_metrics['max_drawdown']:.2%}")
    print()

    outperformance = (
        strat_metrics["annualized_return"] - bh_metrics["annualized_return"]
    )
    print(f"  Strategy vs Buy&Hold: {outperformance:+.2%} annualized")
    print("=" * 60)

    # Show sample signals
    print("\nSample signals (first rows with valid data):")
    sample = valid.head(8)
    for _, row in sample.iterrows():
        pos_label = "LONG" if row["Position"] == 1 else "SHORT"
        print(
            f"  {row['Date'].date()}  Close={row['Close']:.2f}  "
            f"SMA10={row['SMA_10']:.2f}  SMA30={row['SMA_30']:.2f}  "
            f"Pos={pos_label}  Ret={row['Strategy_Return']:+.4f}"
        )


if __name__ == "__main__":
    df = load_data()
    df = compute_indicators(df)
    df = generate_signals(df)
    df = compute_strategy_returns(df)
    print_results(df)
