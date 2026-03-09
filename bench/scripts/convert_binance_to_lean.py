#!/usr/bin/env python3
"""Convert raw Binance CSV data to LEAN engine directory structure.

Reads raw kline CSVs (from download_binance_full_universe.py) and produces
the LEAN-format directory tree expected by QuantConnect's data readers.

LEAN TradeBar CSV format:
    Date, Open (scaled), High (scaled), Low (scaled), Close (scaled), Volume

Scaling: LEAN stores crypto prices as integers with a fixed scale factor.
For crypto, prices are multiplied by 10000 (1e4) and stored as integers.

Directory structure produced:
    Data/crypto/binance/
        daily/btcusdt.zip              (low-res: single zip per symbol)
        hour/btcusdt.zip               (low-res: single zip per symbol)
        minute/btcusdt/20240101_trade.zip  (high-res: one zip per day)
        5minute/btcusdt/20240101_trade.zip (high-res: one zip per day)

Usage:
    python convert_binance_to_lean.py --input-dir PATH --output-dir PATH
    python convert_binance_to_lean.py --input-dir bench/data/raw/i-series --output-dir bench/data/lean
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from tqdm import tqdm

# LEAN crypto price scale factor (prices stored as price * SCALE)
LEAN_PRICE_SCALE = 10000

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
# LEAN date format for intraday (hourly and sub-hourly)
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


def format_lean_row(
    dt: datetime,
    open_price: float,
    high_price: float,
    low_price: float,
    close_price: float,
    volume: float,
    interval: str,
) -> str:
    """Format a single row in LEAN TradeBar CSV format."""
    if interval == "1d":
        date_str = dt.strftime(LEAN_DATE_FMT_DAILY)
    else:
        date_str = dt.strftime(LEAN_DATE_FMT_INTRADAY)

    # Scale prices to LEAN's integer representation
    o = int(round(open_price * LEAN_PRICE_SCALE))
    h = int(round(high_price * LEAN_PRICE_SCALE))
    lo = int(round(low_price * LEAN_PRICE_SCALE))
    c = int(round(close_price * LEAN_PRICE_SCALE))

    return f"{date_str},{o},{h},{lo},{c},{volume}"


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
) -> Path:
    """Write a single zip file for a low-resolution symbol (daily/hourly).

    Output: output_base/crypto/binance/{lean_dir}/{symbol_lower}.zip
    The zip contains a single CSV file named {symbol_lower}.csv.
    """
    symbol_lower = symbol.lower().replace("usdt", "usdt")
    out_dir = output_base / "crypto" / "binance" / lean_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    zip_path = out_dir / f"{symbol_lower}.zip"

    csv_content = "\n".join(lines) + "\n"

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{symbol_lower}.csv", csv_content)

    return zip_path


def write_high_res_zips(
    df: pd.DataFrame,
    lines: list[str],
    symbol: str,
    lean_dir: str,
    output_base: Path,
) -> list[Path]:
    """Write per-day zip files for high-resolution data (5m/1m).

    Output: output_base/crypto/binance/{lean_dir}/{symbol_lower}/{YYYYMMDD}_trade.zip
    Each zip contains a single CSV named {YYYYMMDD}_trade.csv.
    """
    symbol_lower = symbol.lower()
    out_dir = output_base / "crypto" / "binance" / lean_dir / symbol_lower
    out_dir.mkdir(parents=True, exist_ok=True)

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

        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{date_str}_trade.csv", csv_content)
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
        out_path = write_low_res_zip(lines, symbol, lean_dir, output_base)
        return f"{symbol}/{interval} -> {out_path.name}", len(lines)
    else:
        written = write_high_res_zips(df, lines, symbol, lean_dir, output_base)
        return f"{symbol}/{interval} -> {len(written)} daily zips", len(lines)


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
        help="Output directory for LEAN-format data (will contain crypto/binance/...).",
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

    if not input_dir.is_dir():
        print(f"Input directory does not exist: {input_dir}", file=sys.stderr)
        return 2

    csvs = find_raw_csvs(input_dir)
    if not csvs:
        print(f"No CSV files found under {input_dir}", file=sys.stderr)
        return 2

    print(f"Found {len(csvs)} raw CSV files in {input_dir}")

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
            desc, rows = convert_single_csv(csv_path, output_dir)
            total_rows += rows
            if desc.startswith("SKIP"):
                tqdm.write(f"  {desc}")
        except Exception as exc:
            tqdm.write(f"  ERROR {csv_path.name}: {exc}")

    print(f"\nConversion complete: {total_rows} total rows across {len(csvs)} files")
    print(f"Output: {output_dir / 'crypto' / 'binance'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
