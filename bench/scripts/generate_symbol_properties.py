#!/usr/bin/env python3
"""Generate a custom symbol-properties DB that covers all 671 universe symbols.

Extracts LEAN's built-in symbol-properties-database.csv, security-database.csv,
and market-hours-database.json from Docker, then appends entries for any universe
symbols missing from LEAN's DB (fetching real tick/lot sizes from Binance API).

Output:
    bench/data/lean/symbol-properties/symbol-properties-database.csv
    bench/data/lean/symbol-properties/security-database.csv
    bench/data/lean/market-hours/market-hours-database.json

These files are auto-mounted into the LEAN Docker container by
generate_lean_reference.py, overriding LEAN's built-in metadata.

Usage:
    python generate_symbol_properties.py
    python generate_symbol_properties.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent
LEAN_DIR = BENCH_ROOT / "data" / "lean"
UNIVERSE_FILE = BENCH_ROOT / "data" / "lean_universe.json"
LEAN_IMAGE = "quantconnect/lean:latest"

# Output paths
SYMPROPS_DIR = LEAN_DIR / "symbol-properties"
MARKET_HOURS_DIR = LEAN_DIR / "market-hours"


def _docker_cat(path: str) -> str:
    """Extract a file from the LEAN Docker image."""
    result = subprocess.run(
        ["docker", "run", "--rm", "--entrypoint", "bash",
         LEAN_IMAGE, "-c", f"cat {path}"],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to extract {path}: {result.stderr}")
    return result.stdout


def _extract_lean_files() -> tuple[str, str, str]:
    """Extract symbol-properties, security-database, and market-hours from Docker."""
    print("Extracting LEAN metadata from Docker...")
    symprops = _docker_cat("/Lean/Data/symbol-properties/symbol-properties-database.csv")
    security = _docker_cat("/Lean/Data/symbol-properties/security-database.csv")
    market_hours = _docker_cat("/Lean/Data/market-hours/market-hours-database.json")
    print(f"  symbol-properties: {len(symprops.splitlines())} lines")
    print(f"  security-database: {len(security.splitlines())} lines")
    print(f"  market-hours: {len(market_hours)} bytes")
    return symprops, security, market_hours


def _load_universe() -> list[str]:
    """Load the flat universe symbol list."""
    if not UNIVERSE_FILE.exists():
        raise FileNotFoundError(f"Universe file not found: {UNIVERSE_FILE}")
    symbols = json.loads(UNIVERSE_FILE.read_text())
    print(f"Universe: {len(symbols)} symbols")
    return symbols


def _parse_existing_symbols(symprops_csv: str) -> set[str]:
    """Parse existing Binance USDT cryptofuture symbols from the CSV."""
    existing = set()
    for line in symprops_csv.splitlines():
        if line.startswith("#") or line.startswith("market,"):
            continue
        parts = line.split(",")
        if len(parts) >= 3 and parts[0] == "binance" and parts[2] == "cryptofuture":
            existing.add(parts[1].upper())
    return existing


def _fetch_binance_exchange_info() -> dict[str, dict]:
    """Fetch tick size and lot size from Binance Futures API.

    Returns dict mapping symbol → {tick_size, lot_size}.
    """
    print("Fetching Binance Futures exchange info...")
    url = "https://fapi.binance.com/fapi/v1/exchangeInfo"
    req = urllib.request.Request(url, headers={"User-Agent": "quanttutorbench/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode())

    result = {}
    for sym_info in data.get("symbols", []):
        symbol = sym_info["symbol"]
        tick_size = None
        lot_size = None
        for f in sym_info.get("filters", []):
            if f["filterType"] == "PRICE_FILTER":
                tick_size = f["tickSize"]
            elif f["filterType"] == "LOT_SIZE":
                lot_size = f["stepSize"]
        if tick_size and lot_size:
            # Normalize: strip trailing zeros but keep at least one decimal
            tick_size = tick_size.rstrip("0").rstrip(".")
            lot_size = lot_size.rstrip("0").rstrip(".")
            result[symbol] = {"tick_size": tick_size, "lot_size": lot_size}

    print(f"  Got exchange info for {len(result)} symbols")
    return result


def _generate_new_rows(
    missing: list[str],
    exchange_info: dict[str, dict],
) -> list[str]:
    """Generate CSV rows for missing symbols.

    Format: binance,{SYMBOL},cryptofuture,{SYMBOL},USDT,1,{tick_size},{lot_size},{SYMBOL}
    """
    rows = []
    api_hits = 0
    defaults_used = 0

    for symbol in sorted(missing):
        if symbol in exchange_info:
            tick = exchange_info[symbol]["tick_size"]
            lot = exchange_info[symbol]["lot_size"]
            api_hits += 1
        else:
            # Conservative defaults for delisted symbols
            tick = "0.0001"
            lot = "1"
            defaults_used += 1

        row = f"binance,{symbol},cryptofuture,{symbol},USDT,1,{tick},{lot},{symbol}"
        rows.append(row)

    print(f"  Generated {len(rows)} new rows ({api_hits} from API, {defaults_used} defaults)")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be generated without writing files")
    args = parser.parse_args()

    # 1. Extract LEAN's built-in files
    symprops_csv, security_csv, market_hours_json = _extract_lean_files()

    # 2. Load universe and find missing symbols
    universe = _load_universe()
    existing = _parse_existing_symbols(symprops_csv)
    missing = [s for s in universe if s.upper() not in existing]
    print(f"Existing in LEAN DB: {len(existing)} Binance cryptofuture symbols")
    print(f"Missing from LEAN DB: {len(missing)} symbols")

    if missing:
        for s in sorted(missing)[:10]:
            print(f"  {s}")
        if len(missing) > 10:
            print(f"  ... and {len(missing) - 10} more")

    # 3. Fetch real tick/lot sizes from Binance API
    exchange_info = _fetch_binance_exchange_info()

    # 4. Generate new rows
    new_rows = _generate_new_rows(missing, exchange_info)

    # 5. Merge: append new rows to LEAN's CSV
    merged_csv = symprops_csv.rstrip("\n") + "\n"
    if new_rows:
        merged_csv += "\n# Custom entries for quant-tutor-bench universe\n"
        merged_csv += "\n".join(new_rows) + "\n"

    total_binance = len(_parse_existing_symbols(merged_csv))
    print(f"\nMerged DB: {len(merged_csv.splitlines())} lines, {total_binance} Binance cryptofuture symbols")

    if args.dry_run:
        print("\nDRY RUN — no files written.")
        if new_rows:
            print("\nSample new rows:")
            for row in new_rows[:5]:
                print(f"  {row}")
        return 0

    # 6. Write output files
    SYMPROPS_DIR.mkdir(parents=True, exist_ok=True)
    MARKET_HOURS_DIR.mkdir(parents=True, exist_ok=True)

    symprops_out = SYMPROPS_DIR / "symbol-properties-database.csv"
    symprops_out.write_text(merged_csv)
    print(f"Wrote: {symprops_out}")

    security_out = SYMPROPS_DIR / "security-database.csv"
    security_out.write_text(security_csv)
    print(f"Wrote: {security_out}")

    market_hours_out = MARKET_HOURS_DIR / "market-hours-database.json"
    market_hours_out.write_text(market_hours_json)
    print(f"Wrote: {market_hours_out}")

    # 7. Verify all universe symbols are covered
    final_existing = _parse_existing_symbols(merged_csv)
    still_missing = [s for s in universe if s.upper() not in final_existing]
    if still_missing:
        print(f"\nWARNING: {len(still_missing)} symbols still missing after merge:")
        for s in still_missing:
            print(f"  {s}")
        return 1

    print(f"\nAll {len(universe)} universe symbols covered in symbol-properties DB.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
