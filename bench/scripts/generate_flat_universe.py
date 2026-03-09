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


def generate_flat_universe(input_path: Path) -> list[str]:
    """Read structured universe.json and return flat list of tier1 symbols."""
    with open(input_path) as f:
        universe = json.load(f)

    symbols_raw = universe["tiers"]["tier1"]["symbols"]
    return [sym["symbol"] for sym in symbols_raw]


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
