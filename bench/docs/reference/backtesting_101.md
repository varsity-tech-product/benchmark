# Backtesting 101: A Practical Guide

Backtesting is the process of testing a trading strategy against historical data to evaluate how it would have performed in the past. It is a foundational practice in quantitative finance that helps traders and researchers assess the viability of a strategy before risking real capital.

---

## 1. What Is Backtesting?

Backtesting simulates the execution of a trading strategy on historical market data. The core idea is straightforward:

1. Define a set of rules (the strategy) that generate buy and sell signals.
2. Apply those rules to historical price data, proceeding chronologically.
3. Track hypothetical trades, portfolio value, and performance over time.
4. Analyze the results using standard performance metrics.

Backtesting does **not** guarantee future performance. It answers only one question: *"How would this strategy have performed on past data?"*

### Why Backtest?

- **Validation**: Confirm that a strategy idea has historical merit before deploying capital.
- **Comparison**: Compare multiple strategies or parameter configurations objectively.
- **Risk assessment**: Understand the worst-case drawdown, volatility, and tail risks.
- **Refinement**: Iterate on signal logic, position sizing, and risk management rules.

---

## 2. Key Steps in a Backtest

### Step 1: Data Preparation

Obtain clean, accurate historical data. At minimum, you need OHLCV (Open, High, Low, Close, Volume) data at the desired frequency (daily, hourly, minute, tick).

```python
import pandas as pd
import yfinance as yf

# Download historical data
df = yf.download('AAPL', start='2015-01-01', end='2023-12-31')
df = df[['Open', 'High', 'Low', 'Close', 'Volume']]
df.dropna(inplace=True)

print(f"Data shape: {df.shape}")
print(f"Date range: {df.index.min()} to {df.index.max()}")
```

**Data quality checklist:**
- Remove or handle missing values (NaN).
- Adjust for stock splits and dividends (use adjusted close).
- Ensure timestamps are in chronological order.
- Check for duplicate timestamps.
- Verify data source reliability.

### Step 2: Signal Generation

Apply your strategy logic to produce trading signals. A signal is typically encoded as:
- `+1` = buy / go long
- `-1` = sell / go short
- `0` = no position / close

```python
# Example: SMA crossover signal
short_window = 50
long_window = 200

df['SMA_short'] = df['Close'].rolling(short_window).mean()
df['SMA_long'] = df['Close'].rolling(long_window).mean()

# Generate signal: 1 when short MA > long MA, else 0
df['signal'] = 0
df.loc[df['SMA_short'] > df['SMA_long'], 'signal'] = 1
df.loc[df['SMA_short'] <= df['SMA_long'], 'signal'] = -1
```

### Step 3: Position Sizing

Determine how much capital to allocate to each trade. Common approaches:

| Method             | Description                                        |
|--------------------|----------------------------------------------------|
| Fixed fraction     | Risk a fixed % of portfolio on each trade          |
| Equal weight       | Divide capital equally across positions             |
| Volatility-based   | Size positions inversely proportional to volatility |
| Kelly criterion    | Optimal fraction based on win rate and payoff ratio |

```python
# Simple equal-weight example: all-in or all-out
initial_capital = 100_000
df['position'] = df['signal'].shift(1)  # Avoid look-ahead bias
df['daily_return'] = df['Close'].pct_change()
df['strategy_return'] = df['position'] * df['daily_return']
```

### Step 4: Performance Measurement

Calculate key performance metrics to evaluate the strategy.

```python
import numpy as np

# Cumulative returns
df['cumulative_market'] = (1 + df['daily_return']).cumprod()
df['cumulative_strategy'] = (1 + df['strategy_return']).cumprod()

# Total return
total_return = df['cumulative_strategy'].iloc[-1] - 1
print(f"Total Strategy Return: {total_return:.2%}")

# Annualized return
n_years = (df.index[-1] - df.index[0]).days / 365.25
annual_return = (1 + total_return) ** (1 / n_years) - 1
print(f"Annualized Return: {annual_return:.2%}")
```

---

## 3. Common Pitfalls

### Look-Ahead Bias

**What it is**: Using information that would not have been available at the time a trading decision was made. This is the most common and most dangerous backtest error.

**Examples:**
- Using today's close price to generate today's signal (should use yesterday's signal for today's trade).
- Computing a moving average that includes future data points.
- Using centered rolling windows.

**Prevention:**
```python
# WRONG: signal uses current data to trade current period
df['signal'] = (df['SMA_short'] > df['SMA_long']).astype(int)
df['strategy_return'] = df['signal'] * df['daily_return']  # Look-ahead!

# CORRECT: shift signal by 1 day so trades execute the next period
df['position'] = df['signal'].shift(1)
df['strategy_return'] = df['position'] * df['daily_return']
```

### Survivorship Bias

**What it is**: Testing only on assets that still exist today, ignoring those that were delisted, went bankrupt, or were acquired. This inflates performance because the worst performers are excluded.

**Prevention:**
- Use point-in-time datasets that include delisted securities.
- Source survivorship-bias-free data (e.g., CRSP, Quandl delisted tickers).
- If unavailable, acknowledge this limitation in your analysis.

### Overfitting (Curve Fitting)

**What it is**: Tuning strategy parameters to fit historical data so precisely that the strategy captures noise rather than genuine patterns. An overfit strategy performs brilliantly in-sample but poorly out-of-sample.

**Warning signs:**
- Strategy requires many parameters (> 3-4 free parameters is suspicious).
- Performance degrades significantly with small parameter changes.
- In-sample performance is dramatically better than out-of-sample.

**Prevention:**
- Use walk-forward optimization (rolling train/test splits).
- Keep strategies simple (fewer parameters).
- Report out-of-sample results honestly.
- Use cross-validation techniques adapted for time series.

### Transaction Cost Neglect

**What it is**: Ignoring commissions, slippage, bid-ask spread, and market impact.

**Prevention:**
```python
# Include transaction costs
cost_per_trade = 0.001  # 10 bps per trade (round-trip)
df['trades'] = df['position'].diff().abs()
df['strategy_return_net'] = df['strategy_return'] - (df['trades'] * cost_per_trade)
```

---

## 4. Key Performance Metrics

| Metric                  | What It Measures                               | Good Value         |
|-------------------------|------------------------------------------------|--------------------|
| Total Return            | Overall gain/loss                              | Positive           |
| Annualized Return       | Yearly compounded return                       | > risk-free rate   |
| Sharpe Ratio            | Risk-adjusted return                           | > 1.0              |
| Sortino Ratio           | Downside risk-adjusted return                  | > 1.5              |
| Maximum Drawdown        | Worst peak-to-trough decline                   | < 20%              |
| Calmar Ratio            | Annualized return / max drawdown               | > 1.0              |
| Win Rate                | % of profitable trades                         | > 50% (depends)    |
| Profit Factor           | Gross profit / gross loss                      | > 1.5              |
| Number of Trades        | How often the strategy trades                  | Sufficient sample  |

---

## 5. Simple Vectorized Backtest: Complete Example

A vectorized backtest uses array operations (no loops) for speed. It works well for strategies without complex order logic.

```python
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt

# ------------------------------------------------------------------
# 1. Load data
# ------------------------------------------------------------------
# Replace with your data source
df = pd.read_csv('price_data.csv', parse_dates=['Date'], index_col='Date')

# ------------------------------------------------------------------
# 2. Compute indicators
# ------------------------------------------------------------------
short_window = 50
long_window = 200

df['SMA_50'] = df['Close'].rolling(short_window).mean()
df['SMA_200'] = df['Close'].rolling(long_window).mean()

# ------------------------------------------------------------------
# 3. Generate signals
# ------------------------------------------------------------------
df['signal'] = 0
df.loc[df['SMA_50'] > df['SMA_200'], 'signal'] = 1    # Long
df.loc[df['SMA_50'] <= df['SMA_200'], 'signal'] = 0    # Flat

# Shift signal to avoid look-ahead bias
df['position'] = df['signal'].shift(1)

# ------------------------------------------------------------------
# 4. Compute returns
# ------------------------------------------------------------------
df['daily_return'] = df['Close'].pct_change()
df['strategy_return'] = df['position'] * df['daily_return']

# Transaction costs
cost_bps = 10  # basis points per round trip
df['trades'] = df['position'].diff().abs()
df['strategy_return_net'] = df['strategy_return'] - (df['trades'] * cost_bps / 10_000)

# Cumulative returns
df['cum_market'] = (1 + df['daily_return']).cumprod()
df['cum_strategy'] = (1 + df['strategy_return_net']).cumprod()

# ------------------------------------------------------------------
# 5. Performance metrics
# ------------------------------------------------------------------
trading_days = 252

total_return = df['cum_strategy'].iloc[-1] - 1
n_years = len(df) / trading_days
ann_return = (1 + total_return) ** (1 / n_years) - 1
ann_vol = df['strategy_return_net'].std() * np.sqrt(trading_days)
sharpe = ann_return / ann_vol if ann_vol != 0 else 0

# Maximum drawdown
cum_max = df['cum_strategy'].cummax()
drawdown = (df['cum_strategy'] - cum_max) / cum_max
max_drawdown = drawdown.min()

print(f"Total Return:      {total_return:>10.2%}")
print(f"Annualized Return: {ann_return:>10.2%}")
print(f"Annualized Vol:    {ann_vol:>10.2%}")
print(f"Sharpe Ratio:      {sharpe:>10.2f}")
print(f"Max Drawdown:      {max_drawdown:>10.2%}")
print(f"Num Trades:        {int(df['trades'].sum()):>10d}")

# ------------------------------------------------------------------
# 6. Visualization
# ------------------------------------------------------------------
fig, axes = plt.subplots(3, 1, figsize=(14, 10), sharex=True)

# Price and MAs
axes[0].plot(df.index, df['Close'], label='Close', alpha=0.7)
axes[0].plot(df.index, df['SMA_50'], label='SMA 50')
axes[0].plot(df.index, df['SMA_200'], label='SMA 200')
axes[0].set_title('Price and Moving Averages')
axes[0].legend()

# Cumulative returns
axes[1].plot(df.index, df['cum_market'], label='Buy & Hold')
axes[1].plot(df.index, df['cum_strategy'], label='Strategy (net)')
axes[1].set_title('Cumulative Returns')
axes[1].legend()

# Drawdown
axes[2].fill_between(df.index, drawdown, 0, color='red', alpha=0.3)
axes[2].set_title('Drawdown')
axes[2].set_ylabel('Drawdown %')

plt.tight_layout()
plt.show()
```

---

## 6. Event-Driven vs. Vectorized Backtesting

| Aspect          | Vectorized                       | Event-Driven                        |
|-----------------|----------------------------------|-------------------------------------|
| Speed           | Very fast (numpy/pandas)         | Slower (loop-based)                 |
| Complexity      | Simple strategies only           | Handles complex order logic         |
| Realism         | Approximation                    | More realistic execution simulation |
| Look-ahead risk | Easy to introduce accidentally   | Naturally avoids it                 |
| Libraries       | Custom pandas code               | Zipline, Backtrader, VectorBT       |

### When to Use Each

- **Vectorized**: Initial exploration, simple long/short signals, fast iteration.
- **Event-driven**: Strategies with stop losses, trailing stops, multi-asset rebalancing, complex position sizing.

---

## 7. Walk-Forward Optimization

To combat overfitting, split the data into rolling train/test windows:

```python
def walk_forward_backtest(df, train_size=252, test_size=63):
    """
    Walk-forward optimization with rolling windows.

    Parameters
    ----------
    df : pd.DataFrame
        Price data with 'Close' column.
    train_size : int
        Number of days in the training window.
    test_size : int
        Number of days in the testing window.

    Returns
    -------
    pd.DataFrame
        Out-of-sample results concatenated.
    """
    results = []
    start = 0

    while start + train_size + test_size <= len(df):
        train = df.iloc[start : start + train_size]
        test = df.iloc[start + train_size : start + train_size + test_size]

        # Optimize on train (e.g., find best SMA window)
        best_window = optimize_on_train(train)  # User-defined function

        # Apply to test
        test_result = apply_strategy(test, best_window)  # User-defined function
        results.append(test_result)

        start += test_size  # Roll forward

    return pd.concat(results)
```

---

## 8. Checklist Before Trusting a Backtest

- [ ] No look-ahead bias (signals are shifted appropriately).
- [ ] Transaction costs are included.
- [ ] Data is survivorship-bias-free (or limitation is acknowledged).
- [ ] Out-of-sample testing has been performed.
- [ ] Strategy has a reasonable number of parameters (< 5).
- [ ] Results are robust to small parameter changes.
- [ ] Sufficient number of trades for statistical significance (> 30).
- [ ] Risk metrics (max drawdown, Sharpe) are acceptable.
- [ ] Strategy logic has an economic rationale (not just data mining).
