#!/usr/bin/env python3
"""Generate a flat universe JSON list from the structured universe.json.

The structured universe.json has nested tiers with symbol objects, but the
LEAN C# algorithms expect a simple JSON array of symbol strings:
    ["BTCUSDT", "ETHUSDT", ...]

This script extracts tier1 symbol names into that flat format.

Usage:
    python generate_flat_universe.py
    python generate_flat_universe.py --input universe.json --output lean_universe.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

DEFAULT_INPUT = Path(__file__).resolve().parent.parent / "data" / "universe.json"
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "lean_universe.json"


def _extract_symbol(sym_entry: dict | str) -> str:
    """Extract symbol string from either a dict entry or plain string."""
    return sym_entry["symbol"] if isinstance(sym_entry, dict) else sym_entry


def generate_flat_universe(input_path: Path) -> list[str]:
    """Read structured universe.json and return flat list of symbols.

    Ordering: tier2 symbols first (they have hourly data for multi-timeframe
    strategies), then remaining tier1 symbols sorted alphabetically.
    """
    with open(input_path) as f:
        universe = json.load(f)

    tier1_raw = universe["tiers"]["tier1"]["symbols"]
    tier1_syms = [_extract_symbol(s) for s in tier1_raw]

    # Put tier2 symbols first so algorithms that Take(N) get data-rich symbols
    tier2_raw = universe["tiers"].get("tier2", {}).get("symbols", [])
    tier2_syms = [_extract_symbol(s) for s in tier2_raw]
    tier2_set = set(tier2_syms)

    # tier2 first (preserving tier2 order), then remaining tier1 sorted
    remaining = sorted(s for s in tier1_syms if s not in tier2_set)
    return tier2_syms + remaining


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input", type=Path, default=DEFAULT_INPUT,
        help="Path to structured universe.json (default: %(default)s)",
    )
    parser.add_argument(
        "--output", type=Path, default=DEFAULT_OUTPUT,
        help="Output path for flat universe JSON (default: %(default)s)",
    )
    args = parser.parse_args()

    if not args.input.exists():
        print(f"ERROR: Input file not found: {args.input}")
        return 1

    symbols = generate_flat_universe(args.input)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(symbols, f, indent=2)

    print(f"Wrote {len(symbols)} symbols to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
