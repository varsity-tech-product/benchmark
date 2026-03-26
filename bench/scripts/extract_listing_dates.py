#!/usr/bin/env python3
"""Extract per-symbol listing and last-data dates from raw Binance CSVs.

Scans tier1_daily CSVs and reads the first/last row timestamps to derive
each symbol's listing date and last available data date.  Outputs a
structured JSON file (symbol_dates.json) used for point-in-time auditing
and signal-generation guardrails.

Usage:
    python extract_listing_dates.py            # default paths
    python extract_listing_dates.py --raw-dir /path/to/tier1_daily --out /path/to/symbol_dates.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RAW_DIR = BENCH_ROOT / "data" / "raw" / "i-series" / "tier1_daily"
DEFAULT_OUT = BENCH_ROOT / "data" / "symbol_dates.json"

BENCH_START = "2022-01-01"
BENCH_END = "2025-12-31"


def _ts_to_date(ts_ms: int) -> str:
    """Convert millisecond Unix timestamp to ISO date string."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _extract_dates(csv_path: Path) -> tuple[str, str] | None:
    """Return (listing_date, last_data_date) from a raw Binance daily CSV.

    Reads only the first and last data rows (skips header).
    Returns None if the file is empty or malformed.
    """
    try:
        with open(csv_path, "r") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if header is None:
                return None

            first_row = next(reader, None)
            if first_row is None:
                return None

            first_ts = int(first_row[0])

            # Read to the last row with valid timestamp
            last_row = first_row
            for row in reader:
                if row and row[0].strip():
                    last_row = row

            last_ts = int(last_row[0])

        return _ts_to_date(first_ts), _ts_to_date(last_ts)
    except (ValueError, IndexError, StopIteration):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract listing dates from raw CSVs")
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR,
                        help="Directory containing tier1 daily CSVs")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT,
                        help="Output JSON path")
    args = parser.parse_args()

    raw_dir: Path = args.raw_dir
    out_path: Path = args.out

    if not raw_dir.is_dir():
        print(f"ERROR: raw directory not found: {raw_dir}", file=sys.stderr)
        sys.exit(1)

    csv_files = sorted(raw_dir.glob("*_1d.csv"))
    if not csv_files:
        print(f"ERROR: no *_1d.csv files found in {raw_dir}", file=sys.stderr)
        sys.exit(1)

    bench_start_date = date.fromisoformat(BENCH_START)
    bench_end_date = date.fromisoformat(BENCH_END)

    symbols: dict[str, dict] = {}
    n_before = 0
    n_during = 0
    n_delisted = 0
    n_errors = 0

    for csv_path in csv_files:
        sym = csv_path.stem.replace("_1d", "").upper()
        result = _extract_dates(csv_path)
        if result is None:
            print(f"  WARN: skipping {csv_path.name} (empty or malformed)")
            n_errors += 1
            continue

        listing_date_str, last_data_str = result
        listing_date = date.fromisoformat(listing_date_str)
        last_data_date = date.fromisoformat(last_data_str)

        symbols[sym] = {
            "listing_date": listing_date_str,
            "last_data_date": last_data_str,
        }

        if listing_date <= bench_start_date:
            n_before += 1
        else:
            n_during += 1

        if last_data_date < bench_end_date:
            n_delisted += 1

    output = {
        "version": "1.0",
        "generated_date": date.today().isoformat(),
        "bench_window": [BENCH_START, BENCH_END],
        "source": "bench/data/raw/i-series/tier1_daily/*_1d.csv (first/last row timestamps)",
        "symbols": symbols,
        "stats": {
            "total_symbols": len(symbols),
            "listed_before_bench_start": n_before,
            "listed_during_bench_window": n_during,
            "delisted_before_bench_end": n_delisted,
            "parse_errors": n_errors,
        },
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(output, f, indent=2)
        f.write("\n")

    print(f"Wrote {out_path}")
    print(f"  Total symbols:    {len(symbols)}")
    print(f"  Listed before {BENCH_START}: {n_before}")
    print(f"  Listed during window:  {n_during}")
    print(f"  Delisted early:        {n_delisted}")
    if n_errors:
        print(f"  Parse errors:          {n_errors}")


if __name__ == "__main__":
    main()
