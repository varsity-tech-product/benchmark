#!/usr/bin/env python3
"""Freeze a benchmark universe from actual raw + LEAN trade coverage.

The input structured universe may contain all currently discoverable symbols.
This script rewrites it into a benchmark contract universe that only retains
symbols with full required coverage for each tier.
"""

from __future__ import annotations

import argparse
import json
from copy import deepcopy
from datetime import date
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = BENCH_ROOT / "data"

DEFAULT_INPUT = DATA_DIR / "universe.json"
DEFAULT_OUTPUT = DATA_DIR / "universe.json"
DEFAULT_FLAT_OUTPUT = DATA_DIR / "lean_universe.json"
DEFAULT_REPORT = DATA_DIR / "benchmark_universe_coverage.json"
DEFAULT_RAW_DIR = DATA_DIR / "raw" / "i-series"
DEFAULT_LEAN_DIR = DATA_DIR / "lean"

REQUIRED_INTERVALS = {
    "tier1": ["1d"],
    "tier2": ["1h", "4h"],
    "tier3": ["1m", "5m"],
}


def _extract_symbol(entry: dict | str) -> str:
    return entry["symbol"] if isinstance(entry, dict) else entry


def _raw_path(raw_dir: Path, tier: str, symbol: str, interval: str) -> Path:
    subdir = {
        "tier1": raw_dir / "tier1_daily",
        "tier2": raw_dir / "tier2_hourly",
        "tier3": raw_dir / "tier3_minute",
    }[tier]
    return subdir / f"{symbol}_{interval}.csv"


def _lean_path(lean_dir: Path, symbol: str, interval: str) -> Path:
    base = lean_dir / "cryptofuture" / "binance"
    if interval == "1d":
        return base / "daily" / f"{symbol.lower()}_trade.zip"
    if interval == "1h":
        return base / "hour" / f"{symbol.lower()}_trade.zip"
    if interval == "4h":
        return base / "4hour" / f"{symbol.lower()}_trade.zip"
    if interval == "1m":
        return base / "minute" / symbol.lower()
    if interval == "5m":
        return base / "5minute" / symbol.lower()
    raise ValueError(f"Unsupported interval: {interval}")


def _lean_exists(lean_dir: Path, symbol: str, interval: str) -> bool:
    target = _lean_path(lean_dir, symbol, interval)
    if interval in {"1m", "5m"}:
        return target.exists() and any(target.glob("*_trade.zip"))
    return target.exists()


def build_coverage_report(
    universe: dict,
    raw_dir: Path,
    lean_dir: Path,
) -> dict:
    report = {
        "generated_on": date.today().isoformat(),
        "required_intervals": deepcopy(REQUIRED_INTERVALS),
        "tiers": {},
        "summary": {
            "input_counts": {},
            "frozen_counts": {},
            "total_missing_contracts": 0,
        },
    }

    for tier, required in REQUIRED_INTERVALS.items():
        original_entries = list(universe["tiers"][tier]["symbols"])
        frozen_entries: list[dict | str] = []
        tier_missing: list[dict] = []

        for entry in original_entries:
            symbol = _extract_symbol(entry)
            missing_intervals: list[dict] = []
            for interval in required:
                raw_exists = _raw_path(raw_dir, tier, symbol, interval).exists()
                lean_exists = _lean_exists(lean_dir, symbol, interval)
                if not (raw_exists and lean_exists):
                    missing_intervals.append(
                        {
                            "interval": interval,
                            "raw_exists": raw_exists,
                            "lean_exists": lean_exists,
                        }
                    )

            if missing_intervals:
                tier_missing.append(
                    {
                        "symbol": symbol,
                        "missing_intervals": missing_intervals,
                    }
                )
            else:
                frozen_entries.append(entry)

        report["tiers"][tier] = {
            "input_count": len(original_entries),
            "frozen_count": len(frozen_entries),
            "symbols_kept": [_extract_symbol(entry) for entry in frozen_entries],
            "symbols_dropped": tier_missing,
        }
        report["summary"]["input_counts"][tier] = len(original_entries)
        report["summary"]["frozen_counts"][tier] = len(frozen_entries)
        report["summary"]["total_missing_contracts"] += len(tier_missing)

    return report


def freeze_structured_universe(
    universe: dict,
    report: dict,
) -> dict:
    frozen = deepcopy(universe)
    frozen["freeze_date"] = date.today().isoformat()
    frozen["coverage_policy"] = (
        "coverage-frozen benchmark universe; symbol kept only if raw CSV and "
        "LEAN trade coverage exist for all required tier intervals"
    )
    frozen["required_intervals"] = deepcopy(REQUIRED_INTERVALS)
    frozen["source_universe_symbol_counts"] = deepcopy(report["summary"]["input_counts"])
    frozen["frozen_universe_symbol_counts"] = deepcopy(report["summary"]["frozen_counts"])

    for tier in REQUIRED_INTERVALS:
        kept = report["tiers"][tier]["symbols_kept"]
        original_entries = universe["tiers"][tier]["symbols"]
        frozen["tiers"][tier]["symbols"] = [
            entry for entry in original_entries if _extract_symbol(entry) in set(kept)
        ]

    funding = universe["tiers"].get("funding", {}).get("symbols", [])
    if isinstance(funding, list):
        tier2_symbols = {
            _extract_symbol(entry) for entry in frozen["tiers"]["tier2"]["symbols"]
        }
        frozen["tiers"]["funding"]["symbols"] = [
            entry for entry in funding if _extract_symbol(entry) in tier2_symbols
        ]

    return frozen


def generate_flat_universe(universe: dict) -> list[str]:
    tier1 = [_extract_symbol(entry) for entry in universe["tiers"]["tier1"]["symbols"]]
    tier2 = [_extract_symbol(entry) for entry in universe["tiers"]["tier2"]["symbols"]]
    tier2_set = set(tier2)
    remaining = sorted(symbol for symbol in tier1 if symbol not in tier2_set)
    return tier2 + remaining


def freeze_universe_files(
    input_path: Path = DEFAULT_INPUT,
    raw_dir: Path = DEFAULT_RAW_DIR,
    lean_dir: Path = DEFAULT_LEAN_DIR,
    output_path: Path = DEFAULT_OUTPUT,
    flat_output_path: Path = DEFAULT_FLAT_OUTPUT,
    report_path: Path = DEFAULT_REPORT,
) -> dict:
    universe = json.loads(input_path.read_text())
    report = build_coverage_report(universe, raw_dir=raw_dir, lean_dir=lean_dir)
    frozen = freeze_structured_universe(universe, report)
    flat = generate_flat_universe(frozen)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(frozen, indent=2, ensure_ascii=False) + "\n")
    flat_output_path.parent.mkdir(parents=True, exist_ok=True)
    flat_output_path.write_text(json.dumps(flat, indent=2, ensure_ascii=False) + "\n")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")

    return {
        "report": report,
        "frozen_universe": frozen,
        "flat_universe": flat,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--raw-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--lean-dir", type=Path, default=DEFAULT_LEAN_DIR)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--flat-output", type=Path, default=DEFAULT_FLAT_OUTPUT)
    parser.add_argument("--report-output", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    result = freeze_universe_files(
        input_path=args.input,
        raw_dir=args.raw_dir,
        lean_dir=args.lean_dir,
        output_path=args.output,
        flat_output_path=args.flat_output,
        report_path=args.report_output,
    )
    summary = result["report"]["summary"]
    print(
        json.dumps(
            {
                "input_counts": summary["input_counts"],
                "frozen_counts": summary["frozen_counts"],
                "total_missing_contracts": summary["total_missing_contracts"],
                "output": str(args.output),
                "flat_output": str(args.flat_output),
                "report_output": str(args.report_output),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
