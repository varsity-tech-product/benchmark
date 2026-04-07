#!/usr/bin/env python3
"""Convert raw Binance CSV data to LEAN engine directory structure.

Reads raw kline CSVs (from download_binance_full_universe.py) and produces
the LEAN-format directory tree expected by QuantConnect's data readers.
This pipeline produces trade-bar files only. It does NOT generate quote or
margin-interest sidecars for the current benchmark contract.

Supports two security types (--security-type flag):

  cryptofuture (default):
    Path:   Data/cryptofuture/binance/
    Daily:  {sym}_trade.zip  →  {sym}_trade_perp.csv
    Hour:   {sym}_trade.zip  →  {sym}_trade_perp.csv
    Minute: {date}_trade.zip →  {date}_{sym}_minute_trade_perp.csv
    Prices: Raw decimals (no scaling)
    Minute timestamp: milliseconds from midnight (0, 60000, ...)

  crypto:
    Path:   Data/crypto/binance/
    Daily:  {sym}_trade.zip  →  {sym}.csv
    Hour:   {sym}_trade.zip  →  {sym}.csv
    Minute: {date}_trade.zip →  {date}_trade.csv
    Prices: Raw decimals (no scaling)
    Minute timestamp: milliseconds from midnight (0, 60000, ...)

LEAN TradeBar CSV format (all crypto types):
    Timestamp, Open, High, Low, Close, Volume
    Prices are stored as raw decimals (NOT scaled).

Usage:
    python convert_binance_to_lean.py --input-dir PATH --output-dir PATH
    python convert_binance_to_lean.py --input-dir bench/data/raw/i-series --output-dir bench/data/lean
    python convert_binance_to_lean.py --input-dir PATH --output-dir PATH --security-type crypto
"""

from __future__ import annotations

import argparse
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from tqdm import tqdm

# Mapping from Binance interval names to LEAN resolution directory names
INTERVAL_TO_LEAN_DIR = {
    "1d": "daily",
    "4h": "4hour",
    "1h": "hour",
    "5m": "5minute",
    "1m": "minute",
}

# Low-resolution formats get a single zip per symbol.
# High-resolution formats get one zip per day per symbol.
LOW_RES_INTERVALS = {"1d", "4h", "1h"}
HIGH_RES_INTERVALS = {"5m", "1m"}

# LEAN date format for low-resolution (daily)
LEAN_DATE_FMT_DAILY = "%Y%m%d 00:00"
# LEAN date format for intraday low-res (hourly, 4hour)
LEAN_DATE_FMT_INTRADAY = "%Y%m%d %H:%M"


def read_raw_csv(csv_path: Path) -> pd.DataFrame:
    """Read a raw Binance kline CSV (as produced by download scripts)."""
    df = pd.read_csv(csv_path)

    required = ["timestamp", "open", "high", "low", "close", "volume"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in {csv_path}: {missing}")

    # Drop rows with NaN in required columns (e.g. trailing empty rows)
    df = df.dropna(subset=required).reset_index(drop=True)

    return df


def timestamp_ms_to_datetime(ts_ms: int) -> datetime:
    """Convert millisecond Unix timestamp to UTC datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def _ms_from_midnight(dt: datetime) -> int:
    """Return milliseconds elapsed since midnight UTC for a datetime."""
    return (dt.hour * 3600 + dt.minute * 60 + dt.second) * 1000


def _fmt_price(v: float) -> str:
    """Format a price as fixed-point decimal, never scientific notation.

    LEAN's CryptoFuture CSV parser does not handle scientific notation
    (e.g. '6.68e-05'), so all prices must be in fixed-point format.
    Uses 10 decimal places then strips trailing zeros for readability.
    """
    return f"{v:.10f}".rstrip("0").rstrip(".")


def format_lean_row(
    dt: datetime,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
    interval: str,
) -> str:
    """Format a single row in LEAN TradeBar CSV format.

    LEAN crypto data uses raw decimal prices (no scaling).
    Low-res (daily/hour): timestamp is date string.
    High-res (minute/5min): timestamp is milliseconds from midnight.
    """
    if interval == "1d":
        ts = dt.strftime(LEAN_DATE_FMT_DAILY)
    elif interval in LOW_RES_INTERVALS:
        ts = dt.strftime(LEAN_DATE_FMT_INTRADAY)
    else:
        # High-res: milliseconds from midnight
        ts = str(_ms_from_midnight(dt))

    o = _fmt_price(open_price)
    h = _fmt_price(high_price)
    lo = _fmt_price(low_price)
    c = _fmt_price(close_price)
    return f"{ts},{o},{h},{lo},{c},{volume}"


def convert_to_lean_lines(df: pd.DataFrame, interval: str) -> list[str]:
    """Convert a raw kline DataFrame to LEAN-format CSV lines."""
    lines = []
    for _, row in df.iterrows():
        dt = timestamp_ms_to_datetime(int(row["timestamp"]))
        line = format_lean_row(
            dt=dt,
            open_price=float(row["open"]),
            high_price=float(row["high"]),
            low_price=float(row["low"]),
            close_price=float(row["close"]),
            volume=float(row["volume"]),
            interval=interval,
        )
        lines.append(line)
    return lines


def write_low_res_zip(
    lines: list[str],
    symbol: str,
    lean_dir: str,
    output_base: Path,
    security_type: str,
) -> Path:
    """Write a single zip file for a low-resolution symbol (daily/hourly/4hour).

    CryptoFuture: {sym}_trade.zip containing {sym}_trade_perp.csv
    Crypto:       {sym}_trade.zip containing {sym}.csv
    """
    sym = symbol.lower()
    out_dir = output_base / security_type / "binance" / lean_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{sym}_trade.zip"

    if security_type == "cryptofuture":
        csv_name = f"{sym}_trade_perp.csv"
    else:
        csv_name = f"{sym}.csv"

    csv_content = "\n".join(lines) + "\n"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(csv_name, csv_content)

    return zip_path


def write_high_res_zips(
    df: pd.DataFrame,
    lines: list[str],
    symbol: str,
    lean_dir: str,
    output_base: Path,
    security_type: str,
) -> list[Path]:
    """Write per-day zip files for high-resolution data (5m/1m).

    CryptoFuture: {date}_trade.zip containing {date}_{sym}_minute_trade_perp.csv
    Crypto:       {date}_trade.zip containing {date}_trade.csv
    """
    sym = symbol.lower()
    out_dir = output_base / security_type / "binance" / lean_dir / sym
    out_dir.mkdir(parents=True, exist_ok=True)

    # Map LEAN dir back to resolution label for CSV naming
    res_label = lean_dir  # "minute", "5minute"

    # Group lines by date
    dates = []
    for _, row in df.iterrows():
        dt = timestamp_ms_to_datetime(int(row["timestamp"]))
        dates.append(dt.strftime("%Y%m%d"))

    # Build date -> lines mapping
    date_lines: dict[str, list[str]] = {}
    for date_str, line in zip(dates, lines):
        date_lines.setdefault(date_str, []).append(line)

    written = []
    for date_str, day_lines in sorted(date_lines.items()):
        zip_path = out_dir / f"{date_str}_trade.zip"
        csv_content = "\n".join(day_lines) + "\n"

        if security_type == "cryptofuture":
            csv_name = f"{date_str}_{sym}_{res_label}_trade_perp.csv"
        else:
            csv_name = f"{date_str}_trade.csv"

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(csv_name, csv_content)
        written.append(zip_path)

    return written


def detect_interval_from_filename(filename: str) -> str | None:
    """Extract interval from filenames like 'BTCUSDT_1d.csv' or 'BTCUSDT_1h.csv'."""
    stem = Path(filename).stem  # e.g. "BTCUSDT_1d"
    parts = stem.rsplit("_", 1)
    if len(parts) == 2:
        interval = parts[1]
        if interval in INTERVAL_TO_LEAN_DIR:
            return interval
    return None


def detect_symbol_from_filename(filename: str) -> str:
    """Extract symbol from filenames like 'BTCUSDT_1d.csv'."""
    stem = Path(filename).stem
    parts = stem.rsplit("_", 1)
    return parts[0]


def find_raw_csvs(input_dir: Path) -> list[Path]:
    """Recursively find all raw kline CSV files under input_dir."""
    csvs = sorted(input_dir.rglob("*.csv"))
    # Exclude funding rate files
    return [p for p in csvs if "funding" not in p.name.lower()]


def convert_single_csv(
    csv_path: Path,
    output_base: Path,
    security_type: str,
) -> tuple[str, int]:
    """Convert a single raw CSV to LEAN format.

    Returns (description, row_count).
    """
    interval = detect_interval_from_filename(csv_path.name)
    if interval is None:
        return f"SKIP (unknown interval): {csv_path.name}", 0

    symbol = detect_symbol_from_filename(csv_path.name)
    lean_dir = INTERVAL_TO_LEAN_DIR[interval]

    df = read_raw_csv(csv_path)
    if df.empty:
        return f"SKIP (empty): {csv_path.name}", 0

    # Sort by timestamp
    df = df.sort_values("timestamp").reset_index(drop=True)

    lines = convert_to_lean_lines(df, interval)

    if interval in LOW_RES_INTERVALS:
        out_path = write_low_res_zip(
            lines, symbol, lean_dir, output_base, security_type
        )
        return f"{symbol}/{interval} -> {out_path.name}", len(lines)
    else:
        written = write_high_res_zips(
            df, lines, symbol, lean_dir, output_base, security_type
        )
        return f"{symbol}/{interval} -> {len(written)} daily zips", len(lines)


def aggregate_minute_to_daily(
    minute_csv: Path,
    output_base: Path,
    security_type: str,
    existing_daily_csv: Path | None = None,
) -> tuple[str, int]:
    """Aggregate minute raw CSV into daily bars, filling gaps not covered by daily data.

    If existing_daily_csv is provided, only generates bars for dates NOT already
    present in the daily file, then merges both into a single output.
    """
    symbol = detect_symbol_from_filename(minute_csv.name)

    df_min = read_raw_csv(minute_csv)
    df_min = df_min.sort_values("timestamp").reset_index(drop=True)
    df_min["date"] = df_min["timestamp"].apply(
        lambda ts: timestamp_ms_to_datetime(int(ts)).strftime("%Y%m%d")
    )

    # Aggregate minute → daily OHLCV
    agg = (
        df_min.groupby("date")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .reset_index()
    )

    # If existing daily data, only keep dates not already covered
    existing_dates: set[str] = set()
    if existing_daily_csv and existing_daily_csv.exists():
        df_daily = read_raw_csv(existing_daily_csv)
        df_daily = df_daily.sort_values("timestamp").reset_index(drop=True)
        existing_dates = {
            timestamp_ms_to_datetime(int(ts)).strftime("%Y%m%d")
            for ts in df_daily["timestamp"]
        }
        new_dates = agg[~agg["date"].isin(existing_dates)]
        if new_dates.empty:
            return f"{symbol}/daily: no new dates to fill", 0
        # Merge: new aggregated dates + existing daily data lines
        existing_lines = convert_to_lean_lines(df_daily, "1d")
    else:
        new_dates = agg
        existing_lines = []

    # Convert aggregated rows to LEAN lines
    new_lines = []
    for _, row in new_dates.iterrows():
        date_str = f"{row['date']} 00:00"
        o = _fmt_price(float(row["open"]))
        h = _fmt_price(float(row["high"]))
        lo = _fmt_price(float(row["low"]))
        c = _fmt_price(float(row["close"]))
        new_lines.append(f"{date_str},{o},{h},{lo},{c},{row['volume']}")

    all_lines = sorted(existing_lines + new_lines)

    out_path = write_low_res_zip(all_lines, symbol, "daily", output_base, security_type)
    filled = len(new_dates)
    total = len(all_lines)
    return (
        f"{symbol}/daily: {filled} bars from minute, {total} total -> {out_path.name}",
        total,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        required=True,
        help="Directory containing raw Binance kline CSVs.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        required=True,
        help="Output directory for LEAN-format data (will contain cryptofuture/binance/...).",
    )
    parser.add_argument(
        "--security-type",
        choices=["cryptofuture", "crypto"],
        default="cryptofuture",
        help="LEAN security type: 'cryptofuture' (default) for AddCryptoFuture, "
        "'crypto' for AddCrypto.",
    )
    parser.add_argument(
        "--fill-daily-from-minute",
        type=Path,
        default=None,
        metavar="MINUTE_DIR",
        help="Aggregate minute CSVs from this directory to fill gaps in daily data. "
        "For each symbol with minute data, generates daily bars for dates "
        "not already covered by daily raw data.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List files that would be converted without writing.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    security_type = args.security_type

    if not input_dir.is_dir():
        print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
        return 2

    csvs = find_raw_csvs(input_dir)
    if not csvs:
        print(f"No CSV files found under {input_dir}", file=sys.stderr)
        return 2

    print(f"Found {len(csvs)} raw CSV files in {input_dir}")
    print(f"Security type: {security_type}")

    if args.dry_run:
        for csv_path in csvs:
            interval = detect_interval_from_filename(csv_path.name)
            symbol = detect_symbol_from_filename(csv_path.name)
            res_type = "low-res" if interval in LOW_RES_INTERVALS else "high-res"
            print(f"  {csv_path.name} -> {symbol}/{interval} ({res_type})")
        print(f"\nDry run: {len(csvs)} files would be converted.")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    total_rows = 0

    for csv_path in tqdm(csvs, desc="Converting", unit="file"):
        try:
            desc, rows = convert_single_csv(csv_path, output_dir, security_type)
            total_rows += rows
            if desc.startswith("SKIP"):
                tqdm.write(f"  {desc}")
        except Exception as exc:
            tqdm.write(f"  ERROR {csv_path.name}: {exc}")

    out_path = output_dir / security_type / "binance"
    print(f"\nConversion complete: {total_rows} total rows across {len(csvs)} files")
    print(f"Output: {out_path}")

    # Fill daily gaps from minute data if requested
    if args.fill_daily_from_minute:
        minute_dir = args.fill_daily_from_minute.resolve()
        minute_csvs = [
            p
            for p in find_raw_csvs(minute_dir)
            if detect_interval_from_filename(p.name) == "1m"
        ]
        if minute_csvs:
            print(f"\nFilling daily gaps from {len(minute_csvs)} minute files...")
            for mc in minute_csvs:
                symbol = detect_symbol_from_filename(mc.name)
                # Find matching daily CSV if it exists
                daily_csv = input_dir / f"{symbol}_1d.csv"
                if not daily_csv.exists():
                    daily_csv = None
                try:
                    desc, rows = aggregate_minute_to_daily(
                        mc, output_dir, security_type, daily_csv
                    )
                    print(f"  {desc}")
                except Exception as exc:
                    print(f"  ERROR filling daily for {symbol}: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
