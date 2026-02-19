# Multi-Indicator Optimized Trading Strategy
# Uses 12 tunable parameters optimized on AAPL training data (2020-2022).
# Tests out-of-sample performance on 2023-2024.

import os

import numpy as np
import pandas as pd

DATA_PATH = "/data/AAPL.csv"

# ===========================================================================
# BUG: Over-parametrized strategy with 12 hand-tuned parameters.
# All parameters were optimized specifically on AAPL 2020-2022 data.
# This creates a strategy that curve-fits the training period perfectly
# but has no genuine predictive power out-of-sample.
#
# The 12 parameters:
#   1. fast_ma_window         2. slow_ma_window
#   3. rsi_period             4. rsi_overbought
#   5. rsi_oversold           6. bb_window
#   7. bb_num_std             8. vol_lookback
#   9. vol_threshold          10. momentum_period
#   11. signal_weight_ma      12. signal_weight_rsi
# ===========================================================================

# "Optimized" parameters (tuned to AAPL 2020-2022)
PARAMS = {
    "fast_ma_window": 7,  # Unusual choice (not standard 10 or 20)
    "slow_ma_window": 23,  # Unusual choice (not standard 50)
    "rsi_period": 11,  # Non-standard (typically 14)
    "rsi_overbought": 73,  # Very specific threshold
    "rsi_oversold": 31,  # Very specific threshold
    "bb_window": 17,  # Non-standard (typically 20)
    "bb_num_std": 1.85,  # Very specific (typically 2.0)
    "vol_lookback": 13,  # Unusual
    "vol_threshold": 0.0147,  # Suspiciously precise
    "momentum_period": 8,  # Unusual
    "signal_weight_ma": 0.37,  # Precise weight
    "signal_weight_rsi": 0.63,  # Precise weight (sums to 1.0)
}


def load_data():
    """Load AAPL price data from CSV, or generate sample data if not available."""
    if os.path.exists(DATA_PATH):
        df = pd.read_csv(DATA_PATH, parse_dates=["Date"])
        df = df.sort_values("Date").reset_index(drop=True)
        return df

    print("[INFO] CSV not found at {}. Generating sample data.".format(DATA_PATH))
    np.random.seed(42)
    dates = pd.bdate_range(start="2020-01-02", end="2024-12-31")
    n = len(dates)

    # Generate price with regime changes to make overfitting obvious
    daily_returns = np.zeros(n)
    # 2020-2022: specific pattern the optimizer will latch onto
    regime1_end = int(n * 0.6)  # ~end of 2022
    np.random.seed(42)
    daily_returns[:regime1_end] = np.random.normal(0.0005, 0.018, regime1_end)
    # Add a specific periodic pattern in training data
    for i in range(regime1_end):
        daily_returns[i] += 0.003 * np.sin(2 * np.pi * i / 23)  # cycle matches slow_ma
        daily_returns[i] += 0.002 * np.sin(2 * np.pi * i / 7)  # cycle matches fast_ma

    # 2023-2024: different regime (no periodic patterns)
    np.random.seed(999)
    daily_returns[regime1_end:] = np.random.normal(0.0003, 0.022, n - regime1_end)

    price = 130.0 * np.cumprod(1 + daily_returns)

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


def compute_rsi(series, period):
    """Compute Relative Strength Index."""
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(window=period, min_periods=period).mean()
    avg_loss = loss.rolling(window=period, min_periods=period).mean()
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def compute_indicators(df, params):
    """Compute all technical indicators using the 12 parameters."""
    p = params

    # Moving Averages (params 1, 2)
    df["Fast_MA"] = df["Close"].rolling(window=p["fast_ma_window"]).mean()
    df["Slow_MA"] = df["Close"].rolling(window=p["slow_ma_window"]).mean()

    # RSI (param 3)
    df["RSI"] = compute_rsi(df["Close"], p["rsi_period"])

    # Bollinger Bands (params 6, 7)
    df["BB_Mid"] = df["Close"].rolling(window=p["bb_window"]).mean()
    bb_std = df["Close"].rolling(window=p["bb_window"]).std()
    df["BB_Upper"] = df["BB_Mid"] + p["bb_num_std"] * bb_std
    df["BB_Lower"] = df["BB_Mid"] - p["bb_num_std"] * bb_std

    # Volatility filter (params 8, 9)
    df["Volatility"] = df["Close"].pct_change().rolling(window=p["vol_lookback"]).std()

    # Momentum (param 10)
    df["Momentum"] = df["Close"].pct_change(periods=p["momentum_period"])

    return df


def generate_composite_signal(df, params):
    """Generate composite trading signal from multiple indicators.

    Uses weighted combination of signals (params 11, 12) to create
    a single composite score that determines position.
    """
    p = params

    # MA crossover signal component
    ma_signal = np.where(df["Fast_MA"] > df["Slow_MA"], 1.0, -1.0)

    # RSI signal component (params 4, 5)
    rsi_signal = np.where(
        df["RSI"] < p["rsi_oversold"],
        1.0,
        np.where(df["RSI"] > p["rsi_overbought"], -1.0, 0.0),
    )

    # Bollinger Band signal
    bb_signal = np.where(
        df["Close"] < df["BB_Lower"],
        1.0,
        np.where(df["Close"] > df["BB_Upper"], -1.0, 0.0),
    )

    # Momentum filter
    momentum_filter = np.where(df["Momentum"] > 0, 1.0, -1.0)

    # Volatility filter (param 9): only trade when vol is below threshold
    vol_filter = np.where(df["Volatility"] < p["vol_threshold"], 1.0, 0.5)

    # Weighted composite (params 11, 12)
    composite = (
        p["signal_weight_ma"] * ma_signal
        + p["signal_weight_rsi"] * rsi_signal
        + 0.3 * bb_signal  # fixed weight for BB
        + 0.2 * momentum_filter  # fixed weight for momentum
    ) * vol_filter

    # Convert to position
    df["Composite_Score"] = composite
    df["Position"] = np.where(composite > 0.3, 1, np.where(composite < -0.3, -1, 0))

    # Shift to avoid look-ahead
    df["Position"] = df["Position"].shift(1).fillna(0)

    return df


def compute_strategy_returns(df):
    """Compute strategy returns."""
    df["Daily_Return"] = df["Close"].pct_change()
    df["Strategy_Return"] = df["Position"] * df["Daily_Return"]
    return df


def compute_metrics(returns):
    """Compute annualized performance metrics."""
    if len(returns) == 0 or returns.std() == 0:
        return {"ann_return": 0, "ann_vol": 0, "sharpe": 0, "max_dd": 0, "win_rate": 0}

    ann_ret = returns.mean() * 252
    ann_vol = returns.std() * np.sqrt(252)
    sharpe = ann_ret / ann_vol

    cumulative = (1 + returns).cumprod()
    running_max = cumulative.cummax()
    drawdown = (cumulative - running_max) / running_max
    max_dd = drawdown.min()

    trading_days = returns[returns != 0]
    win_rate = (
        (trading_days > 0).sum() / len(trading_days) if len(trading_days) > 0 else 0
    )

    return {
        "ann_return": ann_ret,
        "ann_vol": ann_vol,
        "sharpe": sharpe,
        "max_dd": max_dd,
        "win_rate": win_rate,
    }


def print_results(df):
    """Print in-sample vs out-of-sample performance comparison."""
    # Split into train (2020-2022) and test (2023-2024)
    train = df[df["Date"] < "2023-01-01"].dropna(subset=["Strategy_Return"])
    test = df[df["Date"] >= "2023-01-01"].dropna(subset=["Strategy_Return"])

    train_metrics = compute_metrics(train["Strategy_Return"])
    test_metrics = compute_metrics(test["Strategy_Return"])

    train_bh = compute_metrics(train["Daily_Return"])
    test_bh = compute_metrics(test["Daily_Return"])

    print("=" * 70)
    print("  Multi-Indicator Optimized Strategy (AAPL)")
    print("  12 Parameters Tuned on Training Data")
    print("=" * 70)

    print("\n  Parameter Values:")
    for i, (key, val) in enumerate(PARAMS.items(), 1):
        print(f"    {i:2d}. {key:25s} = {val}")

    print("\n" + "-" * 70)
    print(f"  {'Metric':<30s} {'Train (2020-2022)':>18s} {'Test (2023-2024)':>18s}")
    print("-" * 70)

    metrics_list = [
        ("Annualized Return", "ann_return", True),
        ("Annualized Volatility", "ann_vol", True),
        ("Sharpe Ratio", "sharpe", False),
        ("Max Drawdown", "max_dd", True),
        ("Win Rate", "win_rate", True),
    ]

    for label, key, is_pct in metrics_list:
        if is_pct:
            print(
                f"  {label:<30s} {train_metrics[key]:>17.2%} {test_metrics[key]:>17.2%}"
            )
        else:
            print(
                f"  {label:<30s} {train_metrics[key]:>17.4f} {test_metrics[key]:>17.4f}"
            )

    print("-" * 70)
    print("\n  Buy & Hold Comparison:")
    print(f"  {'Metric':<30s} {'Train (2020-2022)':>18s} {'Test (2023-2024)':>18s}")
    print("-" * 70)
    print(
        f"  {'Annualized Return':<30s} {train_bh['ann_return']:>17.2%} {test_bh['ann_return']:>17.2%}"
    )
    print(
        f"  {'Sharpe Ratio':<30s} {train_bh['sharpe']:>17.4f} {test_bh['sharpe']:>17.4f}"
    )
    print("-" * 70)

    # Highlight the overfitting
    sharpe_decay = train_metrics["sharpe"] - test_metrics["sharpe"]
    print(f"\n  Sharpe Ratio Decay (Train - Test): {sharpe_decay:+.4f}")
    if sharpe_decay > 0.5:
        print("  WARNING: Large Sharpe decay suggests potential overfitting!")

    print("=" * 70)

    # Show signal distribution
    print("\nSignal Distribution:")
    for period_name, subset in [("Train", train), ("Test", test)]:
        n_long = (subset["Position"] == 1).sum()
        n_short = (subset["Position"] == -1).sum()
        n_flat = (subset["Position"] == 0).sum()
        total = len(subset)
        print(
            f"  {period_name}: Long={n_long} ({n_long/total:.1%})  "
            f"Short={n_short} ({n_short/total:.1%})  "
            f"Flat={n_flat} ({n_flat/total:.1%})"
        )


if __name__ == "__main__":
    df = load_data()
    df = compute_indicators(df, PARAMS)
    df = generate_composite_signal(df, PARAMS)
    df = compute_strategy_returns(df)
    print_results(df)
