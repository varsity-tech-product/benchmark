# Moving Averages: A Comprehensive Guide

Moving averages are among the most widely used tools in technical analysis. They smooth out price data by creating a constantly updated average price, making it easier to identify trends and generate trading signals.

---

## 1. Simple Moving Average (SMA)

### Definition

The Simple Moving Average is the unweighted arithmetic mean of the previous *n* data points. Each observation in the window contributes equally to the average.

### Formula

Given a series of closing prices $P_1, P_2, \ldots, P_n$, the SMA at time $t$ over a window of size $n$ is:

$$
\text{SMA}_t = \frac{1}{n} \sum_{i=0}^{n-1} P_{t-i} = \frac{P_t + P_{t-1} + \cdots + P_{t-n+1}}{n}
$$

### Python Implementation

```python
import pandas as pd
import numpy as np

def simple_moving_average(prices: pd.Series, window: int) -> pd.Series:
    """
    Calculate the Simple Moving Average.

    Parameters
    ----------
    prices : pd.Series
        Series of prices (typically closing prices).
    window : int
        Number of periods over which to compute the average.

    Returns
    -------
    pd.Series
        The SMA values. The first (window - 1) entries will be NaN.
    """
    return prices.rolling(window=window).mean()
```

### Pandas One-Liner

```python
df['SMA_20'] = df['Close'].rolling(20).mean()
df['SMA_50'] = df['Close'].rolling(50).mean()
df['SMA_200'] = df['Close'].rolling(200).mean()
```

### Characteristics

- **Lagging indicator**: The SMA always trails the current price because it averages past data.
- **Equal weighting**: Every observation in the window has identical influence, which means old data can distort the signal.
- **Sensitive to window size**: Shorter windows react faster but generate more noise; longer windows are smoother but slower.

---

## 2. Exponential Moving Average (EMA)

### Definition

The Exponential Moving Average places greater weight on recent observations, making it more responsive to new information than the SMA. It uses a decay (smoothing) factor so that the influence of older prices decreases exponentially.

### Formula

The EMA is defined recursively:

$$
\text{EMA}_t = \alpha \cdot P_t + (1 - \alpha) \cdot \text{EMA}_{t-1}
$$

where the smoothing factor (multiplier) is:

$$
\alpha = \frac{2}{n + 1}
$$

and $n$ is the span (number of periods). The first EMA value is typically seeded with the SMA of the first $n$ observations.

### Python Implementation

```python
def exponential_moving_average(prices: pd.Series, span: int) -> pd.Series:
    """
    Calculate the Exponential Moving Average.

    Parameters
    ----------
    prices : pd.Series
        Series of prices (typically closing prices).
    span : int
        The decay period. Determines the smoothing factor alpha = 2 / (span + 1).

    Returns
    -------
    pd.Series
        The EMA values.
    """
    return prices.ewm(span=span, adjust=False).mean()
```

### Pandas One-Liner

```python
df['EMA_12'] = df['Close'].ewm(span=12, adjust=False).mean()
df['EMA_26'] = df['Close'].ewm(span=26, adjust=False).mean()
```

### Manual Calculation (From Scratch)

```python
def ema_from_scratch(prices: list, span: int) -> list:
    """
    Calculate EMA without pandas, useful for understanding the recursion.
    """
    alpha = 2 / (span + 1)
    ema_values = [prices[0]]  # Seed with first price
    for price in prices[1:]:
        ema_values.append(alpha * price + (1 - alpha) * ema_values[-1])
    return ema_values
```

### SMA vs. EMA Comparison

| Feature          | SMA                         | EMA                                |
|------------------|-----------------------------|------------------------------------|
| Weighting        | Equal across window         | Exponentially decaying             |
| Responsiveness   | Slower to react             | Faster to react                    |
| Noise filtering  | Better at smoothing noise   | More sensitive to recent changes   |
| Computation      | Simple arithmetic mean      | Recursive calculation              |
| Use case         | Long-term trend detection   | Short-term signals, MACD           |

---

## 3. Common Window Sizes

Different window sizes serve different analytical purposes:

| Window | Typical Use                                   |
|--------|-----------------------------------------------|
| 5      | Weekly trend (for daily data)                 |
| 10     | Short-term momentum                           |
| 20     | Monthly trend / Bollinger Band center line    |
| 50     | Medium-term trend; used in golden/death cross |
| 100    | Intermediate trend                            |
| 200    | Long-term trend; institutional benchmark      |

### Rules of Thumb

- **Intraday traders** often use 9, 12, 21-period MAs on minute or hourly bars.
- **Swing traders** favor 20 and 50-period MAs on daily bars.
- **Position traders and investors** focus on 100 and 200-period MAs on daily or weekly bars.

---

## 4. Crossover Signals

Moving average crossovers are one of the most common systematic trading signals. They involve two moving averages of different window sizes applied to the same price series.

### Golden Cross (Bullish Signal)

A **golden cross** occurs when a shorter-period moving average crosses **above** a longer-period moving average. This is interpreted as a bullish signal, suggesting upward momentum is building.

- Classic definition: 50-day SMA crosses above the 200-day SMA.
- Interpretation: Short-term trend is accelerating past the long-term trend.

### Death Cross (Bearish Signal)

A **death cross** occurs when a shorter-period moving average crosses **below** a longer-period moving average. This is interpreted as a bearish signal.

- Classic definition: 50-day SMA crosses below the 200-day SMA.
- Interpretation: Short-term trend is decelerating below the long-term trend.

### Detecting Crossovers in Python

```python
def detect_crossovers(df: pd.DataFrame, short_col: str, long_col: str) -> pd.DataFrame:
    """
    Detect golden cross and death cross signals.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame containing the two moving average columns.
    short_col : str
        Column name for the shorter-period moving average.
    long_col : str
        Column name for the longer-period moving average.

    Returns
    -------
    pd.DataFrame
        Original DataFrame with added 'signal' column:
        +1 = golden cross, -1 = death cross, 0 = no crossover.
    """
    df = df.copy()
    # Position: 1 when short > long, 0 otherwise
    df['position'] = (df[short_col] > df[long_col]).astype(int)
    # Signal: change in position
    df['signal'] = df['position'].diff()
    # Map: +1 = golden cross, -1 = death cross, 0 = no signal
    df['signal'] = df['signal'].fillna(0).astype(int)
    return df
```

### Full Working Example

```python
import pandas as pd
import matplotlib.pyplot as plt

# Assume df has columns: 'Date', 'Close'
df['SMA_50'] = df['Close'].rolling(50).mean()
df['SMA_200'] = df['Close'].rolling(200).mean()

# Detect crossovers
df['position'] = (df['SMA_50'] > df['SMA_200']).astype(int)
df['signal'] = df['position'].diff()

golden_crosses = df[df['signal'] == 1]
death_crosses = df[df['signal'] == -1]

# Plot
fig, ax = plt.subplots(figsize=(14, 7))
ax.plot(df['Date'], df['Close'], label='Close', alpha=0.7)
ax.plot(df['Date'], df['SMA_50'], label='SMA 50', linewidth=1.5)
ax.plot(df['Date'], df['SMA_200'], label='SMA 200', linewidth=1.5)
ax.scatter(golden_crosses['Date'], golden_crosses['Close'],
           marker='^', color='green', s=100, label='Golden Cross', zorder=5)
ax.scatter(death_crosses['Date'], death_crosses['Close'],
           marker='v', color='red', s=100, label='Death Cross', zorder=5)
ax.set_title('Moving Average Crossover Strategy')
ax.legend()
plt.tight_layout()
plt.show()
```

---

## 5. Variations and Advanced Topics

### Weighted Moving Average (WMA)

Assigns linearly increasing weights to more recent observations:

$$
\text{WMA}_t = \frac{n \cdot P_t + (n-1) \cdot P_{t-1} + \cdots + 1 \cdot P_{t-n+1}}{n + (n-1) + \cdots + 1}
$$

### Hull Moving Average (HMA)

Aims to reduce lag while maintaining smoothness:

$$
\text{HMA}(n) = \text{WMA}\left(\sqrt{n},\; 2 \cdot \text{WMA}(n/2) - \text{WMA}(n)\right)
$$

### Volume-Weighted Average Price (VWAP)

Incorporates volume into the averaging:

$$
\text{VWAP} = \frac{\sum (P_i \times V_i)}{\sum V_i}
$$

---

## 6. Practical Considerations

1. **NaN handling**: The first `window - 1` values of a rolling calculation are NaN. Use `min_periods` to control when computation begins.
2. **Centering**: `rolling(..., center=True)` centers the window, but this introduces look-ahead bias in trading applications. Never center when backtesting.
3. **Performance**: For very long series, pandas rolling operations are highly optimized using C extensions and should be preferred over manual loops.
4. **Multiple assets**: Use `groupby` when computing MAs for a multi-asset DataFrame:
   ```python
   df['SMA_20'] = df.groupby('ticker')['Close'].transform(lambda x: x.rolling(20).mean())
   ```
