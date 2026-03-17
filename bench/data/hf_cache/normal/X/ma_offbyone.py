# Moving Average Crossover Strategy
# Computes a dual MA crossover strategy (20-day and 50-day SMA) on AAPL data.
# Generates buy signals when the short MA crosses above the long MA,
# and sell signals when it crosses below.

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

    # Simulate a price series with geometric Brownian motion
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


def compute_moving_averages(df):
    """Compute the 20-day and 50-day simple moving averages."""
    # BUG: Should be rolling(20) for a 20-day MA, but uses 19 (off-by-one)
    df["SMA_20"] = df["Close"].rolling(window=19).mean()
    df["SMA_50"] = df["Close"].rolling(window=50).mean()
    return df


def generate_signals(df):
    """Generate crossover signals: +1 for bullish crossover, -1 for bearish."""
    df["Signal"] = 0
    # Bullish crossover: SMA_20 crosses above SMA_50
    df.loc[
        (df["SMA_20"] > df["SMA_50"])
        & (df["SMA_20"].shift(1) <= df["SMA_50"].shift(1)),
        "Signal",
    ] = 1
    # Bearish crossover: SMA_20 crosses below SMA_50
    df.loc[
        (df["SMA_20"] < df["SMA_50"])
        & (df["SMA_20"].shift(1) >= df["SMA_50"].shift(1)),
        "Signal",
    ] = -1
    return df


def compute_strategy_returns(df):
    """Compute strategy returns based on crossover signals."""
    df["Daily_Return"] = df["Close"].pct_change()

    # Hold position based on last signal
    df["Position"] = 0
    position = 0
    for i in range(len(df)):
        if df.iloc[i]["Signal"] == 1:
            position = 1
        elif df.iloc[i]["Signal"] == -1:
            position = -1
        df.iloc[i, df.columns.get_loc("Position")] = position

    # Shift position by 1 to avoid look-ahead (trade next day)
    df["Position"] = df["Position"].shift(1).fillna(0)
    df["Strategy_Return"] = df["Position"] * df["Daily_Return"]
    df["Cumulative_Strategy"] = (1 + df["Strategy_Return"]).cumprod()
    df["Cumulative_BuyHold"] = (1 + df["Daily_Return"]).cumprod()
    return df


def print_results(df):
    """Print strategy performance summary."""
    valid = df.dropna(subset=["Strategy_Return"])

    total_return_strategy = valid["Cumulative_Strategy"].iloc[-1] - 1
    total_return_bh = valid["Cumulative_BuyHold"].iloc[-1] - 1

    annual_return = valid["Strategy_Return"].mean() * 252
    annual_vol = valid["Strategy_Return"].std() * np.sqrt(252)
    sharpe = annual_return / annual_vol if annual_vol != 0 else 0.0

    n_buy = (df["Signal"] == 1).sum()
    n_sell = (df["Signal"] == -1).sum()

    print("=" * 60)
    print("  Dual MA Crossover Strategy Results (AAPL)")
    print("=" * 60)
    print(
        f"  Period:            {df['Date'].iloc[0].date()} to {df['Date'].iloc[-1].date()}"
    )
    print("  SMA Windows:       20-day / 50-day")
    print(f"  Buy Signals:       {n_buy}")
    print(f"  Sell Signals:      {n_sell}")
    print(f"  Strategy Return:   {total_return_strategy:+.2%}")
    print(f"  Buy & Hold Return: {total_return_bh:+.2%}")
    print(f"  Annualized Return: {annual_return:+.2%}")
    print(f"  Annualized Vol:    {annual_vol:.2%}")
    print(f"  Sharpe Ratio:      {sharpe:.4f}")
    print("=" * 60)

    # Show a few sample MA values
    print("\nSample data (first rows with valid MAs):")
    sample = df.dropna(subset=["SMA_20", "SMA_50"]).head(5)
    for _, row in sample.iterrows():
        print(
            f"  {row['Date'].date()}  Close={row['Close']:.2f}  "
            f"SMA20={row['SMA_20']:.2f}  SMA50={row['SMA_50']:.2f}  "
            f"Signal={int(row['Signal'])}"
        )


if __name__ == "__main__":
    df = load_data()
    df = compute_moving_averages(df)
    df = generate_signals(df)
    df = compute_strategy_returns(df)
    print_results(df)
