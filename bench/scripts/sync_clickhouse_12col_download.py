#!/usr/bin/env python3
"""Synchronize local 12-column custom Binance data with ClickHouse.

This script validates the local zip tree against ClickHouse and redownloads only
the symbol/resolution pairs that are missing, incomplete, or internally gapped.
"""

from __future__ import annotations

import argparse
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from download_clickhouse_12col import RESOLUTIONS, discover_symbols, download_symbol_resolution, get_client
from validate_clickhouse_12col_download import diff_slice_keys, fetch_clickhouse, scan_local, ts_to_str


@dataclass(frozen=True)
class RepairTask:
    symbol: str
    resolution: str
    reason: str


def collect_repair_tasks(
    output_root: Path, validate_workers: int, symbols: list[str] | None = None
) -> tuple[list[RepairTask], list[str]]:
    client = get_client()
    all_symbols = discover_symbols(client) if symbols is None else symbols
    tasks = [(symbol, resolution) for symbol in all_symbols for resolution in RESOLUTIONS]

    local_stats = {}
    with ThreadPoolExecutor(max_workers=validate_workers) as pool:
        futures = {
            pool.submit(scan_local, symbol, resolution, output_root): (symbol, resolution)
            for symbol, resolution in tasks
        }
        for future in as_completed(futures):
            stat = future.result()
            local_stats[(stat.symbol, stat.resolution)] = stat

    source_stats = {}
    with ThreadPoolExecutor(max_workers=validate_workers) as pool:
        futures = {
            pool.submit(fetch_clickhouse, symbol, resolution): (symbol, resolution)
            for symbol, resolution in tasks
        }
        for future in as_completed(futures):
            stat = future.result()
            source_stats[(stat.symbol, stat.resolution)] = stat

    repair_tasks: list[RepairTask] = []
    source_errors: list[str] = []

    for key in tasks:
        local = local_stats[key]
        source = source_stats[key]

        if source.error:
            source_errors.append(f"{local.symbol}/{local.resolution}: {source.error}")
            continue

        if not source.exists or source.min_ts is None or source.max_ts is None:
            continue

        reason: str | None = None
        if local.zip_count == 0:
            reason = (
                f"missing local output, source {ts_to_str(source.min_ts)}.."
                f"{ts_to_str(source.max_ts)}"
            )
        elif local.bad_zip_count > 0 or local.error:
            reason = f"bad local zip: {local.error}"
        elif local.gap_count > 0:
            missing_local, extra_local = diff_slice_keys(local.symbol, local.resolution, output_root)
            if missing_local or extra_local:
                detail_parts = []
                if missing_local:
                    detail_parts.append(f"missing local slices: {','.join(missing_local[:10])}")
                if extra_local:
                    detail_parts.append(f"extra local slices: {','.join(extra_local[:10])}")
                reason = "; ".join(detail_parts)
        elif local.first_ts != source.min_ts or local.last_ts != source.max_ts:
            reason = (
                f"local {ts_to_str(local.first_ts)}..{ts_to_str(local.last_ts)} vs "
                f"source {ts_to_str(source.min_ts)}..{ts_to_str(source.max_ts)}"
            )

        if reason is not None:
            repair_tasks.append(RepairTask(local.symbol, local.resolution, reason))

    return repair_tasks, source_errors


def clear_local_pair(output_root: Path, symbol: str, resolution: str) -> int:
    folder = RESOLUTIONS[resolution]["folder"]
    symbol_dir = output_root / folder / symbol
    if not symbol_dir.is_dir():
        return 0

    removed = 0
    for path in symbol_dir.iterdir():
        if not path.is_file():
            continue
        if path.name.endswith("_trade.zip") or path.name.endswith(".zip.tmp"):
            path.unlink()
            removed += 1
    return removed


def repair_one(output_root: Path, task: RepairTask) -> tuple[str, int, int, int, str]:
    removed = clear_local_pair(output_root, task.symbol, task.resolution)
    label, rows, slices = download_symbol_resolution(task.symbol, task.resolution, output_root)
    return label, rows, slices, removed, task.reason


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("bench/data/custom/binance"),
        help="Root of the custom binance output tree",
    )
    parser.add_argument(
        "--validate-workers",
        type=int,
        default=8,
        help="Parallel workers for validation probes",
    )
    parser.add_argument(
        "--repair-workers",
        type=int,
        default=4,
        help="Parallel workers for redownloads",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=3,
        help="Maximum validation+repair rounds",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only process the first N discovered symbols after sorting",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the current repair plan without downloading",
    )
    args = parser.parse_args()

    output_root = args.output_dir
    output_root.mkdir(parents=True, exist_ok=True)

    symbols = discover_symbols(get_client())
    if args.limit is not None:
        symbols = symbols[: args.limit]

    print(f"Symbols: {len(symbols)}")
    print(f"Output: {output_root}")
    print(f"Validate workers: {args.validate_workers}")
    print(f"Repair workers: {args.repair_workers}")
    print(f"Max rounds: {args.max_rounds}")

    for round_idx in range(1, args.max_rounds + 1):
        print()
        print(f"Round {round_idx}/{args.max_rounds}: validating against ClickHouse...", flush=True)
        repair_tasks, source_errors = collect_repair_tasks(
            output_root=output_root,
            validate_workers=args.validate_workers,
            symbols=symbols,
        )

        print(f"Source probe errors: {len(source_errors)}")
        print(f"Repair tasks: {len(repair_tasks)}")
        if repair_tasks:
            print("Sample repair tasks:")
            for task in repair_tasks[:20]:
                print(f"  - {task.symbol}/{task.resolution}: {task.reason}")
        if source_errors:
            print("Sample source errors:")
            for line in source_errors[:20]:
                print(f"  - {line}")

        if source_errors:
            return 1

        if not repair_tasks:
            print("Validation clean. Local data matches ClickHouse for the configured range.")
            return 0

        if args.dry_run:
            return 1

        print()
        print(f"Round {round_idx}: redownloading {len(repair_tasks)} pairs...", flush=True)
        start = time.monotonic()
        total_rows = 0
        total_slices = 0
        errors = 0

        with ThreadPoolExecutor(max_workers=args.repair_workers) as pool:
            futures = {
                pool.submit(repair_one, output_root, task): task
                for task in repair_tasks
            }
            done = 0
            for future in as_completed(futures):
                task = futures[future]
                done += 1
                try:
                    label, rows, slices, removed, reason = future.result()
                    total_rows += rows
                    total_slices += slices
                    elapsed = time.monotonic() - start
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (len(repair_tasks) - done) / rate if rate > 0 else 0
                    print(
                        f"  [{done}/{len(repair_tasks)}] {label}: {rows:,} rows, "
                        f"{slices} zips, removed {removed} old files "
                        f"({rate:.2f}/s, ETA {eta:.0f}s)",
                        flush=True,
                    )
                except Exception as exc:
                    errors += 1
                    print(
                        f"  [{done}/{len(repair_tasks)}] {task.symbol}/{task.resolution}: "
                        f"ERROR {exc} ({task.reason})",
                        flush=True,
                    )

        elapsed = time.monotonic() - start
        print()
        print(
            f"Round {round_idx} repair done in {elapsed:.0f}s: "
            f"{total_rows:,} rows, {total_slices:,} zips, {errors} errors",
            flush=True,
        )

        if errors:
            return 1

    print("Reached max rounds before validation became clean.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
