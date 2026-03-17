# Long/Short Mean Reversion Strategy
# Implements a mean reversion strategy that goes long when price is below
# the lower Bollinger Band and short when above the upper Bollinger Band.
# Positions: 1 (long), 0 (flat), -1 (short)

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

    # Mean-reverting price process (Ornstein-Uhlenbeck like)
    price = np.zeros(n)
    price[0] = 150.0
    mean_price = 160.0
    theta = 0.02  # mean reversion speed
    sigma = 3.0  # volatility

    for i in range(1, n):
        price[i] = (
            price[i - 1]
            + theta * (mean_price - price[i - 1])
            + sigma * np.random.randn()
        )

    df = pd.DataFrame(
        {
            "Date": dates[:n],
            "Open": price + np.random.uniform(-1, 1, n),
            "High": price + np.abs(np.random.normal(0, 1.5, n)),
            "Low": price - np.abs(np.random.normal(0, 1.5, n)),
            "Close": price,
            "Volume": np.random.randint(20_000_000, 80_000_000, size=n),
        }
    )
    return df


def compute_bollinger_bands(df, window=20, num_std=2.0):
    """Compute Bollinger Bands."""
    df["BB_Mid"] = df["Close"].rolling(window=window).mean()
    df["BB_Std"] = df["Close"].rolling(window=window).std()
    df["BB_Upper"] = df["BB_Mid"] + num_std * df["BB_Std"]
    df["BB_Lower"] = df["BB_Mid"] - num_std * df["BB_Std"]
    return df


def generate_positions(df):
    """Generate position signals based on Bollinger Band breakouts.

    BUG: Short positions are coded as 0 instead of -1.

    When price is above the upper band, we should go short (position = -1).
    But the code sets position = 0 (flat) instead. This means the strategy
    completely misses all the short-side profits from mean reversion back
    toward the middle band.

    The correct mapping should be:
        - Price below lower band: position = 1  (long, expect reversion up)
        - Price between bands:    position = 0  (flat, no edge)
        - Price above upper band: position = -1 (short, expect reversion down)
    """
    df["Position"] = 0  # Default: flat

    # Long when price is below the lower band
    df.loc[df["Close"] < df["BB_Lower"], "Position"] = 1

    # BUG: Should be -1 for short, but coded as 0 (flat)
    # This line effectively does nothing since Position is already 0
    df.loc[df["Close"] > df["BB_Upper"], "Position"] = 0

    # Shift to avoid look-ahead bias
    df["Position"] = df["Position"].shift(1).fillna(0)

    return df


def compute_strategy_returns(df):
    """Compute strategy returns."""
    df["Daily_Return"] = df["Close"].pct_change()
    df["Strategy_Return"] = df["Position"] * df["Daily_Return"]

    df["Cumulative_Strategy"] = (1 + df["Strategy_Return"]).cumprod()
    df["Cumulative_BuyHold"] = (1 + df["Daily_Return"]).cumprod()
    return df


def compute_trade_statistics(df):
    """Compute detailed trade statistics."""
    valid = df.dropna(subset=["Position", "Strategy_Return"])

    long_days = (valid["Position"] == 1).sum()
    short_days = (valid["Position"] == -1).sum()
    flat_days = (valid["Position"] == 0).sum()

    long_returns = valid.loc[valid["Position"] == 1, "Strategy_Return"]
    short_returns = valid.loc[valid["Position"] == -1, "Strategy_Return"]

    stats = {
        "long_days": long_days,
        "short_days": short_days,
        "flat_days": flat_days,
        "long_avg_return": long_returns.mean() if len(long_returns) > 0 else 0.0,
        "short_avg_return": short_returns.mean() if len(short_returns) > 0 else 0.0,
        "long_total_return": long_returns.sum() if len(long_returns) > 0 else 0.0,
        "short_total_return": short_returns.sum() if len(short_returns) > 0 else 0.0,
    }
    return stats


def print_results(df, trade_stats):
    """Print strategy performance and trade statistics."""
    valid = df.dropna(subset=["Strategy_Return"])
    strat_ret = valid["Strategy_Return"]
    bh_ret = valid["Daily_Return"]

    ann_ret = strat_ret.mean() * 252
    ann_vol = strat_ret.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol if ann_vol != 0 else 0.0

    bh_ann_ret = bh_ret.mean() * 252
    bh_ann_vol = bh_ret.std() * np.sqrt(252)
    bh_sharpe = bh_ann_ret / bh_ann_vol if bh_ann_vol != 0 else 0.0

    cumulative = (1 + strat_ret).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_dd = drawdown.min()

    print("=" * 60)
    print("  Long/Short Mean Reversion Strategy (AAPL)")
    print("=" * 60)
    print(
        f"  Period:            {df['Date'].iloc[0].date()} to {df['Date'].iloc[-1].date()}"
    )
    print("  Bollinger Bands:   20-day, 2 std dev")
    print()
    print("  Strategy Performance:")
    print(f"    Annualized Return: {ann_ret:+.2%}")
    print(f"    Annualized Vol:    {ann_vol:.2%}")
    print(f"    Sharpe Ratio:      {sharpe:.4f}")
    print(f"    Max Drawdown:      {max_dd:.2%}")
    print()
    print("  Buy & Hold Performance:")
    print(f"    Annualized Return: {bh_ann_ret:+.2%}")
    print(f"    Annualized Vol:    {bh_ann_vol:.2%}")
    print(f"    Sharpe Ratio:      {bh_sharpe:.4f}")
    print()
    print("  Position Breakdown:")
    print(f"    Long Days:         {trade_stats['long_days']}")
    print(f"    Short Days:        {trade_stats['short_days']}")
    print(f"    Flat Days:         {trade_stats['flat_days']}")
    print(f"    Long Avg Return:   {trade_stats['long_avg_return']:.6f}")
    print(f"    Short Avg Return:  {trade_stats['short_avg_return']:.6f}")
    print(f"    Long Total Return: {trade_stats['long_total_return']:.4f}")
    print(f"    Short Total Return:{trade_stats['short_total_return']:.4f}")
    print("=" * 60)

    # Show some sample positions
    print("\nSample positions (first rows where position is non-zero):")
    non_zero = df[df["Position"] != 0].head(8)
    for _, row in non_zero.iterrows():
        pos_label = {1: "LONG", -1: "SHORT", 0: "FLAT"}.get(int(row["Position"]), "?")
        print(
            f"  {row['Date'].date()}  Close={row['Close']:.2f}  "
            f"BB_Upper={row['BB_Upper']:.2f}  BB_Lower={row['BB_Lower']:.2f}  "
            f"Pos={pos_label}"
        )


if __name__ == "__main__":
    df = load_data()
    df = compute_bollinger_bands(df)
    df = generate_positions(df)
    df = compute_strategy_returns(df)
    trade_stats = compute_trade_statistics(df)
    print_results(df, trade_stats)
