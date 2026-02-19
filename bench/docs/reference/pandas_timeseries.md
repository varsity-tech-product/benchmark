# Pandas Time Series: Essential Methods and Techniques

Pandas provides a powerful suite of tools for working with time series data. This reference covers the core methods used in quantitative finance for data manipulation, transformation, and analysis.

---

## 1. resample() -- Frequency Conversion

The `resample()` method converts time series data from one frequency to another. It groups data into time-based buckets and applies an aggregation function.

### Downsampling (Higher to Lower Frequency)

Convert daily data to weekly, monthly, or quarterly aggregates.

```python
import pandas as pd
import numpy as np

# Assume df has a DatetimeIndex and OHLCV columns
# Daily -> Weekly OHLCV
weekly = df.resample('W').agg({
    'Open': 'first',
    'High': 'max',
    'Low': 'min',
    'Close': 'last',
    'Volume': 'sum'
})

# Daily -> Monthly closing prices
monthly_close = df['Close'].resample('M').last()

# Daily -> Quarterly average
quarterly_avg = df['Close'].resample('Q').mean()
```

### Upsampling (Lower to Higher Frequency)

Convert weekly data to daily, filling gaps.

```python
# Weekly -> Daily with forward fill
daily_from_weekly = weekly['Close'].resample('D').ffill()

# Weekly -> Daily with interpolation
daily_interpolated = weekly['Close'].resample('D').interpolate(method='linear')
```

### Common Frequency Aliases

| Alias | Frequency      | Example                         |
|-------|----------------|---------------------------------|
| `D`   | Calendar day   | Every day including weekends    |
| `B`   | Business day   | Mon-Fri only                    |
| `W`   | Weekly         | End of week (Sunday by default) |
| `M`   | Month end      | Last calendar day of month      |
| `MS`  | Month start    | First calendar day of month     |
| `Q`   | Quarter end    | Last day of quarter             |
| `A`   | Year end       | Last day of year                |
| `H`   | Hourly         | Every hour                      |
| `T`   | Minutely       | Every minute                    |

### OHLCV Resampling Pattern

```python
def resample_ohlcv(df: pd.DataFrame, freq: str) -> pd.DataFrame:
    """
    Resample OHLCV data to a lower frequency.

    Parameters
    ----------
    df : pd.DataFrame
        DataFrame with DatetimeIndex and OHLCV columns.
    freq : str
        Target frequency (e.g., 'W', 'M', 'Q').

    Returns
    -------
    pd.DataFrame
        Resampled OHLCV DataFrame.
    """
    return df.resample(freq).agg({
        'Open': 'first',
        'High': 'max',
        'Low': 'min',
        'Close': 'last',
        'Volume': 'sum'
    }).dropna()
```

---

## 2. rolling() -- Moving Window Calculations

The `rolling()` method creates a moving window view of the data, enabling calculations like moving averages, rolling standard deviations, and more.

### Basic Syntax

```python
df['Close'].rolling(window=20).mean()       # 20-day simple moving average
df['Close'].rolling(window=20).std()        # 20-day rolling standard deviation
df['Close'].rolling(window=20).min()        # 20-day rolling minimum
df['Close'].rolling(window=20).max()        # 20-day rolling maximum
df['Close'].rolling(window=20).sum()        # 20-day rolling sum
df['Close'].rolling(window=20).median()     # 20-day rolling median
```

### Key Parameters

```python
# min_periods: minimum number of observations required
# Produces a value as soon as 1 observation is available
df['Close'].rolling(window=20, min_periods=1).mean()

# center: use centered window (NOT for trading -- causes look-ahead bias)
# Only use for statistical analysis, NEVER for backtesting
df['Close'].rolling(window=20, center=True).mean()

# win_type: use a weighted window (e.g., Gaussian)
df['Close'].rolling(window=20, win_type='gaussian').mean(std=3)
```

### Rolling with Custom Functions

```python
# Custom rolling function using apply
df['rolling_skew'] = df['Close'].rolling(60).apply(
    lambda x: x.skew(), raw=False
)

# Rolling z-score
df['z_score'] = df['Close'].rolling(20).apply(
    lambda x: (x.iloc[-1] - x.mean()) / x.std(), raw=False
)
```

### Expanding Windows

An expanding window includes all data from the start up to the current point:

```python
# Cumulative (expanding) mean
df['expanding_mean'] = df['Close'].expanding().mean()

# Cumulative maximum (useful for drawdown calculations)
df['cummax'] = df['Close'].expanding().max()
```

### Exponentially Weighted Windows

```python
# Exponentially weighted moving average
df['EWMA_20'] = df['Close'].ewm(span=20, adjust=False).mean()

# Exponentially weighted volatility
df['EWMA_vol'] = df['Close'].pct_change().ewm(span=20).std()
```

---

## 3. shift() -- Lagging and Leading Data

The `shift()` method moves data forward or backward in time. This is critical for avoiding look-ahead bias and computing returns.

### Forward Shift (Lagging)

```python
# Lag by 1 period: previous day's close
df['prev_close'] = df['Close'].shift(1)

# Lag by 5 periods: close from 5 days ago
df['close_5d_ago'] = df['Close'].shift(5)

# Use shifted signal to avoid look-ahead bias in backtesting
df['position'] = df['signal'].shift(1)
```

### Backward Shift (Leading)

```python
# Lead by 1 period: tomorrow's close (for label creation)
df['next_close'] = df['Close'].shift(-1)

# Future 5-day return (for supervised learning labels)
df['fwd_return_5d'] = df['Close'].shift(-5) / df['Close'] - 1
```

### Common Use Cases

```python
# 1. Daily returns (equivalent to pct_change)
df['return'] = df['Close'] / df['Close'].shift(1) - 1

# 2. Signal delay for backtesting
df['trade_signal'] = df['raw_signal'].shift(1)  # Trade next day

# 3. Change detection
df['close_changed'] = df['Close'] != df['Close'].shift(1)

# 4. Momentum (price n days ago vs. today)
lookback = 20
df['momentum'] = df['Close'] / df['Close'].shift(lookback) - 1
```

### Frequency-Aware Shifting

```python
# Shift by specific time offset (requires DatetimeIndex)
df['close_1w_ago'] = df['Close'].shift(freq='7D')

# Shift by business days
df['close_1bw_ago'] = df['Close'].shift(freq='5B')
```

---

## 4. pct_change() -- Computing Returns

The `pct_change()` method calculates the fractional change between consecutive elements, making it the standard way to compute period-over-period returns.

### Basic Usage

```python
# Daily returns (simple / arithmetic returns)
df['daily_return'] = df['Close'].pct_change()

# Same as:
df['daily_return_manual'] = df['Close'] / df['Close'].shift(1) - 1

# Weekly returns (5-period change)
df['weekly_return'] = df['Close'].pct_change(periods=5)

# Monthly return (21 trading days)
df['monthly_return'] = df['Close'].pct_change(periods=21)
```

### Log Returns

Log (continuously compounded) returns are additive over time, making them convenient for multi-period analysis:

```python
# Log returns
df['log_return'] = np.log(df['Close'] / df['Close'].shift(1))

# Equivalent:
df['log_return'] = np.log1p(df['daily_return'])

# Cumulative return from log returns
cumulative_log_return = df['log_return'].cumsum()
cumulative_simple_return = np.exp(cumulative_log_return) - 1
```

### Cumulative Returns

```python
# Cumulative product of (1 + return)
df['cumulative_return'] = (1 + df['daily_return']).cumprod() - 1

# Normalize to starting value of 100
df['portfolio_value'] = 100 * (1 + df['daily_return']).cumprod()
```

### Multi-Asset Returns

```python
# Returns for multiple assets
prices = pd.DataFrame({
    'AAPL': aapl_close,
    'GOOG': goog_close,
    'MSFT': msft_close,
})
returns = prices.pct_change().dropna()

# Correlation matrix of returns
correlation_matrix = returns.corr()
```

---

## 5. merge / join -- Combining Time Series

Combining multiple time series is common when working with multi-asset data or merging price data with external signals.

### pd.merge() -- SQL-Style Joins

```python
# Merge two DataFrames on date
merged = pd.merge(prices_df, signals_df, on='Date', how='inner')

# Merge with different column names
merged = pd.merge(
    prices_df, macro_df,
    left_on='Date', right_on='report_date',
    how='left'
)
```

### DataFrame.join() -- Index-Based Joins

```python
# Join on index (both must have DatetimeIndex)
combined = prices_df.join(volumes_df, how='outer', lsuffix='_price', rsuffix='_vol')

# Join multiple DataFrames
combined = prices_df.join([signals_df, macro_df], how='inner')
```

### pd.concat() -- Stacking DataFrames

```python
# Stack vertically (append rows)
all_data = pd.concat([df_2020, df_2021, df_2022], axis=0)

# Stack horizontally (add columns)
combined = pd.concat([aapl_df, goog_df, msft_df], axis=1, keys=['AAPL', 'GOOG', 'MSFT'])
```

### merge_asof() -- Nearest-Time Joins

Ideal for joining data with different frequencies (e.g., trade data with quote data):

```python
# Join trades with the most recent quote at or before each trade
merged = pd.merge_asof(
    trades_df.sort_values('timestamp'),
    quotes_df.sort_values('timestamp'),
    on='timestamp',
    direction='backward'  # Use the most recent quote <= trade time
)

# With tolerance: only match within 5 seconds
merged = pd.merge_asof(
    trades_df.sort_values('timestamp'),
    quotes_df.sort_values('timestamp'),
    on='timestamp',
    tolerance=pd.Timedelta('5s'),
    direction='backward'
)
```

### Alignment Patterns

```python
# Align two series with different indices
aligned_a, aligned_b = series_a.align(series_b, join='inner')

# Reindex one series to match another's index
reindexed = series_b.reindex(series_a.index, method='ffill')
```

---

## 6. Handling Missing Data

Missing data is ubiquitous in financial time series: holidays, halted trading, sparse data sources, and different trading calendars all create gaps.

### Detecting Missing Values

```python
# Count NaNs per column
print(df.isnull().sum())

# Percentage of missing data
print(df.isnull().mean() * 100)

# Rows with any NaN
rows_with_nan = df[df.isnull().any(axis=1)]
```

### Forward Fill (ffill)

Propagate the last valid observation forward. This is the most common method for financial data because it carries forward the last known price.

```python
# Forward fill NaN values
df_filled = df.ffill()

# With a limit on consecutive fills
df_filled = df.ffill(limit=5)  # Fill at most 5 consecutive NaNs
```

### Backward Fill (bfill)

Propagate the next valid observation backward. Use with caution in trading contexts as it introduces look-ahead bias.

```python
# Backward fill (use only for non-trading analytical purposes)
df_filled = df.bfill()
```

### Drop Missing Values (dropna)

Remove rows or columns with missing data.

```python
# Drop rows with any NaN
df_clean = df.dropna()

# Drop rows where specific columns have NaN
df_clean = df.dropna(subset=['Close', 'Volume'])

# Drop columns with more than 50% missing
threshold = len(df) * 0.5
df_clean = df.dropna(axis=1, thresh=threshold)
```

### Interpolation

```python
# Linear interpolation
df['Close_interp'] = df['Close'].interpolate(method='linear')

# Time-based interpolation (accounts for irregular time gaps)
df['Close_interp'] = df['Close'].interpolate(method='time')

# Polynomial interpolation
df['Close_interp'] = df['Close'].interpolate(method='polynomial', order=2)
```

### Replacing Specific Values

```python
# Replace zeros with NaN (common with volume data)
df['Volume'] = df['Volume'].replace(0, np.nan)

# Replace inf values
df = df.replace([np.inf, -np.inf], np.nan)
```

### Best Practices for Financial Data

1. **Prices**: Use `ffill()` -- carry forward the last known price.
2. **Volume**: Use `fillna(0)` -- no volume means no trades occurred.
3. **Returns**: Use `dropna()` -- do not fabricate returns.
4. **Indicators**: Recompute after filling prices rather than filling indicator values directly.
5. **Cross-asset alignment**: Use `merge_asof()` or `reindex(method='ffill')` to align different trading calendars.

---

## 7. Date and Time Operations

### Creating DatetimeIndex

```python
# From strings
dates = pd.to_datetime(['2023-01-01', '2023-01-02', '2023-01-03'])

# Generate date range
idx = pd.date_range(start='2023-01-01', end='2023-12-31', freq='B')  # Business days

# Set as index
df.index = pd.to_datetime(df['Date'])
df = df.drop('Date', axis=1)
```

### Filtering by Date

```python
# Slice by date range
df_2023 = df['2023']
df_q1 = df['2023-01':'2023-03']
df_jan = df['2023-01']

# Boolean filtering
df_recent = df[df.index >= '2023-06-01']

# Between dates
mask = df.index.to_series().between('2023-01-01', '2023-06-30')
df_h1 = df[mask]
```

### Extracting Date Components

```python
df['year'] = df.index.year
df['month'] = df.index.month
df['day_of_week'] = df.index.dayofweek  # 0=Monday, 6=Sunday
df['quarter'] = df.index.quarter
df['is_month_end'] = df.index.is_month_end
```

### Time Zone Handling

```python
# Localize to a timezone
df.index = df.index.tz_localize('US/Eastern')

# Convert between timezones
df.index = df.index.tz_convert('UTC')
```

---

## 8. Performance Tips

1. **Use vectorized operations**: Avoid iterating over rows. Use `.rolling()`, `.shift()`, `.pct_change()` instead.
2. **Prefer `numpy` for heavy math**: Convert to numpy arrays for complex calculations: `returns = df['Close'].values`.
3. **Use appropriate dtypes**: Ensure dates are `datetime64`, prices are `float64`.
4. **Avoid `.apply()` when possible**: It is significantly slower than built-in methods.
5. **Use `.pipe()` for chaining**: Build readable transformation pipelines.

```python
result = (
    df
    .pipe(resample_ohlcv, freq='W')
    .assign(returns=lambda x: x['Close'].pct_change())
    .assign(sma_20=lambda x: x['Close'].rolling(20).mean())
    .dropna()
)
```
