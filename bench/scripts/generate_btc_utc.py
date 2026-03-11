#!/usr/bin/env python3
"""
Generate BTC_UTC.csv for the X05 timezone merge task.

Reads hourly BTCUSDT data, resamples to daily OHLCV using UTC day boundaries,
and outputs with explicit UTC timezone info in the Date column (ISO format with +00:00).
"""

import pandas as pd

RAW_PATH = "/home/rick/Desktop/benchmark/bench/data/raw/i-series/tier2_hourly/BTCUSDT_1h.csv"
OUT_PATH = "/home/rick/Desktop/benchmark/bench/data/frozen/BTC_UTC.csv"

START = "2022-01-01"
END = "2024-12-31"


def main():
    # 1. Read raw hourly CSV
    df = pd.read_csv(RAW_PATH)

    # 2. Convert millisecond timestamps to UTC datetime
    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    # 3. Set as index for resampling
    df = df.set_index("datetime")

    # 4. Resample to daily OHLCV using UTC calendar day boundaries
    daily = df.resample("1D").agg(
        {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
    )

    # Drop rows where all values are NaN (days with no data)
    daily = daily.dropna(how="all")

    # 5. Filter to date range 2022-01-01 to 2024-12-31
    daily = daily.loc[START:END]

    # 6. Build output DataFrame with explicit UTC timezone in Date column
    #    The Date column retains UTC timezone info (ISO format with +00:00)
    out = pd.DataFrame(
        {
            "Date": daily.index.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            "Open": daily["open"].values,
            "High": daily["high"].values,
            "Low": daily["low"].values,
            "Close": daily["close"].values,
            "Volume": daily["volume"].values,
        }
    )

    # 7. Save to CSV
    out.to_csv(OUT_PATH, index=False)
    print(f"Wrote {len(out)} rows to {OUT_PATH}")
    print(f"Date range: {out['Date'].iloc[0]} to {out['Date'].iloc[-1]}")
    print(f"\nFirst 5 rows:")
    print(out.head().to_string(index=False))
    print(f"\nLast 5 rows:")
    print(out.tail().to_string(index=False))


if __name__ == "__main__":
    main()
