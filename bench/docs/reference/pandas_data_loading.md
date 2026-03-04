# Pandas Data Loading: CSV, OHLCV, and Initial Inspection

This reference covers the fundamentals of loading financial data into pandas,
understanding OHLCV columns, and performing initial data quality checks.

---

## 1. Loading CSV Files with pd.read_csv()

### Basic Usage

```python
import pandas as pd

df = pd.read_csv('/data/AAPL_2018_2024.csv')
```

### Common Parameters

```python
# parse_dates: convert date strings to datetime objects
df = pd.read_csv('data.csv', parse_dates=['Date'])

# index_col: set a column as the DataFrame index
df = pd.read_csv('data.csv', parse_dates=['Date'], index_col='Date')

# usecols: load only specific columns
df = pd.read_csv('data.csv', usecols=['Date', 'Close', 'Volume'])

# dtype: specify column types
df = pd.read_csv('data.csv', dtype={'Volume': 'int64'})

# nrows: read only the first N rows (for quick inspection)
df = pd.read_csv('data.csv', nrows=5)
```

---

## 2. Understanding OHLCV Data

OHLCV stands for **Open, High, Low, Close, Volume** — the standard columns
in stock price data.

| Column | Meaning | Example |
|--------|---------|---------|
| Date | Trading date | 2024-01-15 |
| Open | Price at market open | 185.50 |
| High | Highest price during the day | 187.20 |
| Low | Lowest price during the day | 184.80 |
| Close | Price at market close | 186.90 |
| Adj Close | Close adjusted for splits/dividends | 186.45 |
| Volume | Number of shares traded | 52,340,000 |

### Key relationships
- High >= max(Open, Close) always
- Low <= min(Open, Close) always
- Adj Close accounts for stock splits and dividend payments

---

## 3. Initial Data Inspection

### Shape, columns, and types

```python
print(df.shape)          # (rows, columns)
print(df.columns.tolist())
print(df.dtypes)
print(df.info())
```

### First and last rows

```python
print(df.head())         # first 5 rows
print(df.tail())         # last 5 rows
```

### Summary statistics

```python
print(df.describe())     # count, mean, std, min, 25%, 50%, 75%, max
```

### Date range

```python
print(f"Start: {df['Date'].min()}")
print(f"End:   {df['Date'].max()}")
print(f"Trading days: {len(df)}")
```

---

## 4. Data Quality Checks

### Missing values

```python
print(df.isnull().sum())          # missing count per column
print(df.isnull().sum().sum())    # total missing values
```

### Duplicate dates

```python
duplicates = df[df['Date'].duplicated()]
print(f"Duplicate dates: {len(duplicates)}")
```

### Date gaps (missing trading days)

```python
df['Date'] = pd.to_datetime(df['Date'])
df = df.sort_values('Date')
gaps = df['Date'].diff().dt.days
large_gaps = gaps[gaps > 4]  # weekends = 3 days, holidays can be 4
print(f"Gaps > 4 days: {len(large_gaps)}")
```

### Data type validation

```python
# Ensure numeric columns are actually numeric
for col in ['Open', 'High', 'Low', 'Close', 'Volume']:
    if col in df.columns:
        print(f"{col}: {df[col].dtype}")
```

---

## 5. Common Patterns

### Setting Date as index

```python
df['Date'] = pd.to_datetime(df['Date'])
df = df.set_index('Date').sort_index()
```

### Daily returns

```python
df['daily_return'] = df['Close'].pct_change()
```

### Reading multiple files

```python
import glob

files = glob.glob('/data/*.csv')
for f in files:
    df = pd.read_csv(f, parse_dates=['Date'])
    print(f"{f}: {df.shape}")
```
