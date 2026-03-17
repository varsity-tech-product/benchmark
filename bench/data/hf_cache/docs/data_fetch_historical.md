# Historical Financial Data Ingestion

Historical data ingestion is the process of programmatically retrieving past market prices and economic indicators from public data providers. It is a foundational step in quantitative analysis — all backtesting, factor research, and risk modeling depend on clean, correctly sourced historical datasets.

## 1. Common Free Data Sources

The table below lists widely used free-tier providers for market prices and macroeconomic indicators.

| Data Type | Provider | Official Docs | Auth |
|---|---|---|---|
| Market prices | Alpha Vantage | https://www.alphavantage.co/documentation/ | API key |
| Market prices | Twelve Data | https://twelvedata.com/docs | API key |
| Market prices | Nasdaq Data Link | https://docs.data.nasdaq.com/ | API key (dataset-dependent) |
| Macro | FRED | https://fred.stlouisfed.org/docs/api/fred/ | API key |
| Macro | World Bank Indicators API | https://datahelpdesk.worldbank.org/knowledgebase/articles/889392-about-the-indicators-api-documentation | No key |
| Macro | BLS Public API | https://www.bls.gov/bls/api_features.htm | No key for basic usage |
| Macro | BEA | https://www.bea.gov/resources/for-developers | API key |
| Market prices | Stooq | https://stooq.com/ | **No key** |

> **Note on API keys**: This document describes public data APIs for educational purposes. No real API keys are provisioned in the tutoring environment. Providers marked "API key" (Alpha Vantage, FRED, etc.) require the user to register their own key. When no key is available, prefer **key-free** sources such as Stooq or the World Bank API as practical alternatives for demonstration.

## 2. API Endpoint Patterns

### Alpha Vantage — Daily Prices

`GET https://www.alphavantage.co/query`

Typical params:

- `function=TIME_SERIES_DAILY_ADJUSTED`
- `symbol=SPY` (or AAPL, MSFT, etc.)
- `outputsize=full`
- `apikey=<YOUR_API_KEY>`

### FRED — Economic Observations

`GET https://api.stlouisfed.org/fred/series/observations`

Typical params:

- `series_id=CPIAUCSL` (CPI) or `UNRATE` (unemployment)
- `api_key=<YOUR_API_KEY>`
- `file_type=json`
- `observation_start=2015-01-01`
- `observation_end=2025-12-31`

> **Note**: FRED does not offer a demo/anonymous key. Calls without a valid `api_key` will return an error. If no key is available, consider using the World Bank Indicators API (no key required) for macroeconomic data.

### Stooq — Daily Prices (No Key Required)

`GET https://stooq.com/q/d/l/`

Typical params:

- `s=aapl.us` (ticker with exchange suffix)
- `d1=20180101` (start date, YYYYMMDD)
- `d2=20251231` (end date, YYYYMMDD)
- `i=d` (interval: d=daily)

This endpoint returns CSV directly — no JSON parsing needed, and **no API key** is required.

### World Bank — Country Indicator

`GET https://api.worldbank.org/v2/country/{country_code}/indicator/{indicator_code}`

Typical params:

- `format=json`
- `per_page=20000`

Examples:

- US GDP: `country_code=USA`, `indicator_code=NY.GDP.MKTP.CD`
- US CPI proxy: `indicator_code=FP.CPI.TOTL`

## 3. Python Implementation

Below is a minimal, runnable example that fetches daily adjusted prices from Alpha Vantage and saves them to a CSV file.

```python
import os, requests, csv

API_KEY = os.environ.get("ALPHAVANTAGE_API_KEY", "demo")

def fetch_daily_prices(symbol: str, output_path: str) -> None:
    """Fetch daily adjusted prices and save to CSV."""
    url = "https://www.alphavantage.co/query"
    params = {
        "function": "TIME_SERIES_DAILY_ADJUSTED",
        "symbol": symbol,
        "outputsize": "full",
        "apikey": API_KEY,
    }
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    ts = data.get("Time Series (Daily)", {})
    if not ts:
        raise ValueError(f"No time-series data returned for {symbol}")

    rows = []
    for date_str, vals in sorted(ts.items()):
        rows.append({
            "date": date_str,
            "open": float(vals["1. open"]),
            "high": float(vals["2. high"]),
            "low": float(vals["3. low"]),
            "close": float(vals["4. close"]),
            "adjusted_close": float(vals["5. adjusted close"]),
            "volume": int(vals["6. volume"]),
        })

    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

# Usage
fetch_daily_prices("AAPL", "aapl_daily.csv")
```

### Alternative: Stooq (no API key)

When no Alpha Vantage API key is available, Stooq provides free daily price CSV data without authentication:

```python
import pandas as pd

def fetch_stooq_prices(symbol: str, output_path: str) -> None:
    """Fetch daily prices from Stooq (no API key needed) and save to CSV."""
    url = f"https://stooq.com/q/d/l/?s={symbol.lower()}.us&i=d"
    df = pd.read_csv(url, parse_dates=["Date"])
    df.columns = [c.lower() for c in df.columns]
    df.sort_values("date", inplace=True)
    df.to_csv(output_path, index=False)

# Usage
fetch_stooq_prices("AAPL", "aapl_stooq_daily.csv")
```

> **Stooq vs Alpha Vantage**: Stooq provides unadjusted OHLCV data only (no `adjusted_close` column). For backtesting with dividend/split adjustments, Alpha Vantage's `TIME_SERIES_DAILY_ADJUSTED` or Yahoo Finance are better choices. However, Stooq is ideal for quick demonstrations when no API key is available.

A similar pattern applies to macroeconomic data — call the provider endpoint, parse the JSON response, and persist to CSV.

## 4. Common Data Schemas

Market price datasets typically include the following columns:

| Column | Type | Description |
|--------|------|-------------|
| `date` | date | Trading date |
| `open` | float | Opening price |
| `high` | float | Intraday high |
| `low` | float | Intraday low |
| `close` | float | Unadjusted closing price |
| `adjusted_close` | float | Split- and dividend-adjusted close |
| `volume` | int | Shares traded |

Macroeconomic indicator datasets typically include:

| Column | Type | Description |
|--------|------|-------------|
| `date` | date | Observation / release date |
| `value` | float | Indicator reading |
| `series_id` | str | Provider series identifier (optional) |

## 5. Quant Caveats

### 5.1 Adjusted vs Unadjusted Prices

Most data providers offer both raw and adjusted close prices. The difference matters for quantitative analysis:

- **Unadjusted close** reflects the actual traded price on that day. However, stock splits, reverse splits, and dividend distributions create artificial discontinuities in the price series.
- **Adjusted close** retroactively accounts for these corporate actions, producing a smooth return series suitable for backtesting.

Using unadjusted prices in a backtest can generate phantom jumps (e.g., a 2-for-1 split appears as a 50% price drop) that corrupt return calculations and signal generation.

### 5.2 Macro Data Release Lag and Revisions

Macroeconomic indicators (GDP, CPI, unemployment) are **not available immediately** at the end of the measurement period:

- **Release lag**: US GDP for Q1 is first published in late April (~30-day lag). CPI for January is released in mid-February.
- **Revisions**: Initial ("advance") releases are often revised weeks or months later. Final revised values can differ substantially from the first estimate.

Using final revised values as if they were known at the original release date introduces **look-ahead bias** — the model trains on information that was not yet available when the trading decision would have been made.

### 5.3 Point-in-Time Discipline

Point-in-time (PIT) data management ensures that every data point in a research dataset reflects only information that was genuinely available at that moment:

- When joining macro indicators to daily price data, use the **publication date** (not the observation period end date) to determine when the value became known.
- Avoid merging datasets by calendar date alone — a GDP value for "2024-Q1" was not known on 2024-03-31, but only after its release date in late April.
- Many institutional data vendors (e.g., Bloomberg, Refinitiv) provide PIT-stamped time series. When using free APIs, document the assumed lag explicitly.

Failure to enforce PIT discipline is one of the most common sources of inflated backtest performance.

## 6. Data Quality Best Practices

After fetching data from any provider, it is good practice to verify:

1. **Completeness**: No unexpectedly missing rows — compare the date range against the trading calendar.
2. **Duplicates**: No duplicate timestamps; deduplicate or raise an error if found.
3. **Sorting**: Rows are sorted chronologically (ascending by date).
4. **Type correctness**: Price and volume columns parse as numeric; date columns parse as valid dates.
5. **Plausibility**: Price values are positive and within a reasonable range; volume is non-negative.

## 7. Rate Limits and Terms

Free-tier API plans are typically rate-limited (e.g., Alpha Vantage: 5 requests/minute, 500/day). Best practices:

- Add retry logic with exponential backoff.
- Cache responses locally to avoid redundant calls.
- Check each provider's terms of service before commercial or redistributed use.
