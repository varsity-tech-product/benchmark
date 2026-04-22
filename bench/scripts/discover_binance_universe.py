#!/usr/bin/env python3
"""Discover all Binance USDT-M perpetual futures symbols from Data Vision S3.

Queries the S3 XML listing at data.binance.vision to enumerate all symbol
folders under data/futures/um/daily/klines/. Filters to perpetual USDT-M
contracts only (excludes dated delivery contracts like BTCUSDT_250627).

Usage:
    python discover_binance_universe.py
    python discover_binance_universe.py --output bench/data/universe_full.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from xml.etree import ElementTree

import requests

S3_BUCKET_URL = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
S3_PREFIX = "data/futures/um/daily/klines/"
S3_NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "universe_full.json"


def list_s3_prefixes(prefix: str, max_keys: int = 2000) -> list[str]:
    """Page through S3 XML listing and return all CommonPrefixes under *prefix*."""
    session = requests.Session()
    session.headers["User-Agent"] = "QuantTutorBench/universe-discovery"
    prefixes: list[str] = []
    marker: str | None = None

    while True:
        params: dict[str, str | int] = {
            "delimiter": "/",
            "prefix": prefix,
            "max-keys": max_keys,
        }
        if marker:
            params["marker"] = marker

        resp = session.get(S3_BUCKET_URL, params=params, timeout=60)
        resp.raise_for_status()

        root = ElementTree.fromstring(resp.content)

        for cp in root.findall("s3:CommonPrefixes/s3:Prefix", S3_NS):
            if cp.text:
                prefixes.append(cp.text)

        is_truncated = root.findtext("s3:IsTruncated", namespaces=S3_NS)
        if is_truncated and is_truncated.lower() == "true":
            # Use the last prefix as marker for next page
            next_marker = root.findtext("s3:NextMarker", namespaces=S3_NS)
            if next_marker:
                marker = next_marker
            elif prefixes:
                marker = prefixes[-1]
            else:
                break
        else:
            break

    return prefixes


def extract_symbol_from_prefix(prefix: str, base_prefix: str) -> str | None:
    """Extract symbol name from an S3 prefix like 'data/futures/um/daily/klines/BTCUSDT/'."""
    # Strip base prefix and trailing slash
    relative = prefix[len(base_prefix):].rstrip("/")
    return relative if relative else None


def is_perpetual_usdt(symbol: str) -> bool:
    """Return True if symbol is a perpetual USDT-margined contract.

    Filters out:
    - Dated delivery contracts (contain '_' like BTCUSDT_250627)
    - Non-USDT margined (don't end with 'USDT')
    """
    if "_" in symbol:
        return False
    if not symbol.endswith("USDT"):
        return False
    return True


def discover_symbols() -> tuple[list[str], int]:
    """Query S3 and return (sorted symbol list, total S3 entry count)."""
    print(f"Querying S3 listing: {S3_BUCKET_URL}?prefix={S3_PREFIX}")
    raw_prefixes = list_s3_prefixes(S3_PREFIX)
    total = len(raw_prefixes)
    print(f"  Found {total} total entries")

    symbols = []
    for p in raw_prefixes:
        sym = extract_symbol_from_prefix(p, S3_PREFIX)
        if sym and is_perpetual_usdt(sym):
            symbols.append(sym)

    symbols.sort()
    print(f"  After filtering: {len(symbols)} perpetual USDT-M symbols")
    return symbols, total


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output path for discovery JSON (default: %(default)s)",
    )
    args = parser.parse_args()

    symbols, total_entries = discover_symbols()
    if not symbols:
        print("ERROR: No symbols discovered. Check network connectivity.", file=sys.stderr)
        return 1

    result = {
        "version": "2.0",
        "source": "data.binance.vision",
        "discovery_date": date.today().isoformat(),
        "filter": "USDT-M perpetuals only (no delivery contracts)",
        "total_s3_entries": total_entries,
        "symbol_count": len(symbols),
        "symbols": symbols,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(result, f, indent=2)

    print(f"\nWrote {len(symbols)} symbols to {args.output}")
    print(f"  Total S3 entries: {result['total_s3_entries']}")
    print(f"  Perpetual USDT-M: {len(symbols)}")
    print(f"  Filtered out: {result['total_s3_entries'] - len(symbols)} delivery/non-USDT contracts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
