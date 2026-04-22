#!/usr/bin/env python3
"""Download Binance USDT-M futures klines for the full I-series universe.

Reads universe.json for symbol lists across three tiers and downloads daily
kline archives from Binance Data Vision in parallel. Supports resuming
interrupted downloads (existing files are skipped).

Usage:
    python download_binance_full_universe.py [--tier {1,2,3,all}] [--output-dir PATH]
    python download_binance_full_universe.py --universe universe.json --tier 1
    python download_binance_full_universe.py --tier all --workers 16

    # Download ALL symbols from universe_full.json (discovered from Data Vision S3):
    python download_binance_full_universe.py --universe-mode full --tier 1 --workers 16
"""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests
from download_binance_klines import (
    FUNDING_RATE_URL,
    daterange,
    fetch_daily_kline_archive,
    validate_kline_frame,
)
from tqdm import tqdm

# Import benchmark dates for full-universe mode
try:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "config"))
    from benchmark_dates import BENCH_END, BENCH_START

    FULL_UNIVERSE_START = date.fromisoformat(BENCH_START)
    FULL_UNIVERSE_END = date.fromisoformat(BENCH_END)
except ImportError:
    FULL_UNIVERSE_START = date(2022, 1, 1)
    FULL_UNIVERSE_END = date(2025, 12, 31)

DEFAULT_OUTPUT_DIR = (
    Path(__file__).resolve().parent.parent / "data" / "raw" / "i-series"
)
DEFAULT_UNIVERSE = Path(__file__).resolve().parent.parent / "data" / "universe.json"
FULL_UNIVERSE = Path(__file__).resolve().parent.parent / "data" / "universe_full.json"
DEFAULT_REPORT_PATH = DEFAULT_OUTPUT_DIR / "download_report.json"

# Tier-to-interval mapping (matches implementation_section_plan.md section 2.4)
TIER_CONFIG = {
    "tier1": {
        "intervals": ["1d"],
        "default_start": date(2019, 12, 31),
        "default_end": date(2025, 12, 31),
    },
    "tier2": {
        "intervals": ["1h", "4h"],
        "default_start": date(2022, 1, 1),
        "default_end": date(2025, 12, 31),
    },
    "tier3": {
        "intervals": ["5m", "1m"],
        "default_start": date(2024, 1, 1),
        "default_end": date(2025, 12, 31),
    },
}

# Subdirectory names for each tier's output
TIER_SUBDIRS = {
    "tier1": "tier1_daily",
    "tier2": "tier2_hourly",
    "tier3": "tier3_minute",
}


@dataclass(frozen=True)
class DownloadJob:
    """A single symbol+interval download job."""

    symbol: str
    interval: str
    start_date: date
    end_date: date
    output_path: Path


def load_universe(universe_path: Path) -> dict:
    """Load and validate universe.json."""
    with open(universe_path) as f:
        universe = json.load(f)

    if "tiers" not in universe:
        raise ValueError(f"universe.json missing 'tiers' key: {universe_path}")

    return universe


def load_full_universe(full_path: Path) -> dict:
    """Load universe_full.json and wrap it in tiered format for job building."""
    with open(full_path) as f:
        full = json.load(f)

    if "symbols" not in full:
        raise ValueError(f"universe_full.json missing 'symbols' key: {full_path}")

    # Wrap as tier1-only universe so build_jobs() works unchanged
    return {
        "tiers": {
            "tier1": {
                "description": "ALL USDT-M perpetual futures from Binance Data Vision",
                "timeframes": ["1d"],
                "symbols": full["symbols"],  # plain strings
            }
        }
    }


def get_symbols_for_tier(universe: dict, tier_name: str) -> list[dict | str]:
    """Extract the symbol list for a given tier from universe.json."""
    tier_data = universe["tiers"].get(tier_name)
    if tier_data is None:
        raise ValueError(f"Tier '{tier_name}' not found in universe.json")

    symbols = tier_data.get("symbols", [])

    # Handle the "same as tierX" string reference for funding
    if isinstance(symbols, str) and "same as" in symbols:
        ref_tier = symbols.replace("same as ", "").strip()
        return get_symbols_for_tier(universe, ref_tier)

    return symbols


def extract_symbol_name(sym_entry: dict | str) -> str:
    """Extract symbol string from either a dict entry or plain string."""
    if isinstance(sym_entry, dict):
        return sym_entry["symbol"]
    return sym_entry


def extract_listing_date(sym_entry: dict | str, default: date) -> date:
    """Extract listing date from a symbol entry, falling back to default."""
    if isinstance(sym_entry, dict) and "listing_date" in sym_entry:
        return date.fromisoformat(sym_entry["listing_date"])
    return default


def build_jobs(
    universe: dict,
    tiers: list[str],
    output_dir: Path,
) -> list[DownloadJob]:
    """Build the list of download jobs for the requested tiers."""
    jobs = []

    for tier_name in tiers:
        config = TIER_CONFIG[tier_name]
        subdir = TIER_SUBDIRS[tier_name]
        symbols = get_symbols_for_tier(universe, tier_name)

        for sym_entry in symbols:
            symbol = extract_symbol_name(sym_entry)
            start = extract_listing_date(sym_entry, config["default_start"])
            # Clamp start to tier default if listing is before period
            start = max(start, config["default_start"])
            end = config["default_end"]

            for interval in config["intervals"]:
                output_path = output_dir / subdir / f"{symbol}_{interval}.csv"
                jobs.append(
                    DownloadJob(
                        symbol=symbol,
                        interval=interval,
                        start_date=start,
                        end_date=end,
                        output_path=output_path,
                    )
                )

    return jobs


def download_single_job(job: DownloadJob) -> tuple[DownloadJob, str]:
    """Download klines for a single symbol+interval, returning (job, status).

    Skips if output file already exists (resume capability).
    """
    if job.output_path.exists():
        return job, "skipped"

    job.output_path.parent.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "QuantTutorBench/full-universe-downloader"})

    frames = []
    days = list(daterange(job.start_date, job.end_date))

    for day in days:
        try:
            frame = fetch_daily_kline_archive(
                session,
                job.symbol,
                job.interval,
                day,
            )
            frames.append(frame)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                # No data for this day (symbol not yet listed, or gap)
                continue
            raise

    if not frames:
        return job, "empty"

    import pandas as pd

    merged = pd.concat(frames, ignore_index=True)
    merged = (
        merged.sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )

    try:
        validate_kline_frame(merged, job.interval)
    except ValueError:
        # Validation may fail for symbols with gaps; save anyway with warning
        pass

    merged.to_csv(job.output_path, index=False)
    return job, "ok"


def download_funding_for_tier(
    universe: dict,
    output_dir: Path,
    start_date: date = date(2019, 12, 31),
    end_date: date = date(2025, 12, 31),
) -> int:
    """Download funding rate data for all symbols in the funding tier."""
    from datetime import datetime, timezone

    import pandas as pd

    symbols_raw = get_symbols_for_tier(universe, "funding")
    symbols = [extract_symbol_name(s) for s in symbols_raw]
    funding_dir = output_dir / "funding"
    funding_dir.mkdir(parents=True, exist_ok=True)

    downloaded = 0
    session = requests.Session()
    session.headers.update({"User-Agent": "QuantTutorBench/full-universe-downloader"})

    for symbol in tqdm(symbols, desc="[funding]", unit="sym"):
        out_path = funding_dir / f"{symbol}_funding.csv"
        if out_path.exists():
            continue

        start_ms = int(
            datetime.combine(
                start_date, datetime.min.time(), tzinfo=timezone.utc
            ).timestamp()
            * 1000
        )
        end_ms = (
            int(
                datetime.combine(
                    end_date + timedelta(days=1),
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                ).timestamp()
                * 1000
            )
            - 1
        )

        rows: list[dict] = []
        cursor = start_ms
        while cursor <= end_ms:
            params = {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
            try:
                response = session.get(FUNDING_RATE_URL, params=params, timeout=30)
                response.raise_for_status()
            except requests.HTTPError:
                break

            batch = response.json()
            if not batch:
                break
            rows.extend(batch)
            cursor = int(batch[-1]["fundingTime"]) + 1
            if len(batch) < 1000:
                break

        if not rows:
            continue

        df = pd.DataFrame(rows)
        df["timestamp"] = pd.to_numeric(df["fundingTime"], errors="coerce").astype(
            "Int64"
        )
        df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
        df["mark_price"] = pd.to_numeric(df["markPrice"], errors="coerce")
        df = df[["timestamp", "funding_rate", "mark_price"]].sort_values("timestamp")
        df = df.drop_duplicates("timestamp").reset_index(drop=True)
        df.to_csv(out_path, index=False)
        downloaded += 1

    return downloaded


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--universe",
        type=Path,
        default=DEFAULT_UNIVERSE,
        help="Path to universe.json (default: %(default)s)",
    )
    parser.add_argument(
        "--tier",
        choices=["1", "2", "3", "all"],
        default="all",
        help="Which tier(s) to download. Default: all.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Root output directory for raw CSVs (default: %(default)s)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Number of parallel download threads (default: 8)",
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        default=DEFAULT_REPORT_PATH,
        help="Path to write the structured download report (default: %(default)s)",
    )
    parser.add_argument(
        "--allow-empty",
        action="store_true",
        help="Allow required-tier symbols to resolve to empty instead of failing.",
    )
    parser.add_argument(
        "--include-funding",
        action="store_true",
        default=False,
        help="Also download funding rate data for tier2 symbols.",
    )
    parser.add_argument(
        "--universe-mode",
        choices=["tiered", "full"],
        default="tiered",
        help="'tiered' uses universe.json tiers (default). "
        "'full' reads universe_full.json and downloads ALL symbols as tier1.",
    )
    return parser.parse_args()


def resolve_tiers(tier_arg: str) -> list[str]:
    """Map CLI --tier argument to list of tier names."""
    if tier_arg == "all":
        return ["tier1", "tier2", "tier3"]
    return [f"tier{tier_arg}"]


def main() -> int:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.universe_mode == "full":
        full_path = (
            FULL_UNIVERSE if args.universe == DEFAULT_UNIVERSE else args.universe
        )
        try:
            universe = load_full_universe(full_path)
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
            print(f"Error loading full universe: {exc}", file=sys.stderr)
            return 2
        tiers = ["tier1"]  # full mode only has tier1
        # Override tier1 date range to benchmark window
        TIER_CONFIG["tier1"]["default_start"] = FULL_UNIVERSE_START
        TIER_CONFIG["tier1"]["default_end"] = FULL_UNIVERSE_END
        print(
            f"Full universe mode: {len(universe['tiers']['tier1']['symbols'])} symbols "
            f"({FULL_UNIVERSE_START} to {FULL_UNIVERSE_END})"
        )
    else:
        try:
            universe = load_universe(args.universe)
        except (FileNotFoundError, json.JSONDecodeError, ValueError) as exc:
            print(f"Error loading universe.json: {exc}", file=sys.stderr)
            return 2
        tiers = resolve_tiers(args.tier)

    jobs = build_jobs(universe, tiers, output_dir)
    print(f"Built {len(jobs)} download jobs across tiers: {tiers}")

    # Filter to pending jobs for progress display
    pending = [j for j in jobs if not j.output_path.exists()]
    skipped = len(jobs) - len(pending)
    if skipped:
        print(f"  Skipping {skipped} already-downloaded files (resume mode)")

    job_results: list[dict] = []

    ok_count = 0
    empty_count = 0
    error_count = 0

    if not pending:
        print("All kline files already downloaded.")
    else:

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {
                executor.submit(download_single_job, job): job for job in pending
            }

            with tqdm(total=len(pending), desc="[klines]", unit="file") as pbar:
                for future in as_completed(futures):
                    job = futures[future]
                    try:
                        _, status = future.result()
                        job_results.append(
                            {
                                "symbol": job.symbol,
                                "interval": job.interval,
                                "status": status,
                                "start_date": job.start_date.isoformat(),
                                "end_date": job.end_date.isoformat(),
                                "output_path": str(job.output_path),
                            }
                        )
                        if status == "ok":
                            ok_count += 1
                        elif status == "empty":
                            empty_count += 1
                        else:
                            skipped += 1
                    except Exception as exc:
                        error_count += 1
                        job_results.append(
                            {
                                "symbol": job.symbol,
                                "interval": job.interval,
                                "status": "error",
                                "start_date": job.start_date.isoformat(),
                                "end_date": job.end_date.isoformat(),
                                "output_path": str(job.output_path),
                                "error": str(exc),
                            }
                        )
                        tqdm.write(f"  ERROR {job.symbol}/{job.interval}: {exc}")
                    pbar.update(1)

        print(
            f"\nKline results: {ok_count} downloaded, {empty_count} empty, "
            f"{error_count} errors, {skipped} skipped"
        )

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "universe_mode": args.universe_mode,
        "universe_path": str(args.universe),
        "tiers": tiers,
        "allow_empty": args.allow_empty,
        "output_dir": str(output_dir),
        "summary": {
            "job_count": len(jobs),
            "pending_count": len(pending),
            "downloaded_count": ok_count,
            "empty_count": empty_count,
            "error_count": error_count,
            "skipped_count": skipped,
        },
        "jobs": sorted(
            job_results, key=lambda item: (item["symbol"], item["interval"])
        ),
    }
    args.report_path.parent.mkdir(parents=True, exist_ok=True)
    args.report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"  Wrote report: {args.report_path}")

    if not args.allow_empty and empty_count > 0:
        print(
            "ERROR: Required benchmark contracts resolved to empty downloads.",
            file=sys.stderr,
        )
        return 3
    if error_count > 0:
        print("ERROR: One or more downloads failed.", file=sys.stderr)
        return 4

    # Funding rates (sequential, API rate-limited)
    if args.include_funding or args.tier in ("all",):
        print("\nDownloading funding rates...")
        n_funding = download_funding_for_tier(universe, output_dir)
        print(f"  {n_funding} funding files downloaded")

    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
