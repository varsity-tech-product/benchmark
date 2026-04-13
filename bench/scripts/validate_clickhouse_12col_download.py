#!/usr/bin/env python3
"""Validate custom Binance zip output against the ClickHouse source.

Checks local coverage under bench/data/custom/binance/{minute,hour,daily}/{symbol}
and, when requested, compares local first/last timestamps against ClickHouse for
the configured date range.
"""

from __future__ import annotations

import argparse
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from download_clickhouse_12col import END_MS, RESOLUTIONS, START_MS, discover_symbols, get_client


UTC = timezone.utc


@dataclass
class LocalStat:
    symbol: str
    resolution: str
    zip_count: int = 0
    row_count: int = 0
    first_key: str | None = None
    last_key: str | None = None
    first_ts: int | None = None
    last_ts: int | None = None
    gap_count: int = 0
    bad_zip_count: int = 0
    error: str | None = None


@dataclass
class ClickHouseStat:
    symbol: str
    resolution: str
    exists: bool
    row_count: int = 0
    min_ts: int | None = None
    max_ts: int | None = None
    error: str | None = None


def ts_to_str(ts_ms: int | None) -> str:
    if ts_ms is None:
        return "-"
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=UTC)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _parse_key(name: str, resolution: str) -> str | None:
    if not name.endswith("_trade.zip"):
        return None
    key = name[:-10]
    expected_len = 6 if resolution == "1d" else 8
    if len(key) != expected_len or not key.isdigit():
        return None
    return key


def _iter_expected_keys(start_key: str, end_key: str, resolution: str) -> Iterable[str]:
    if resolution == "1d":
        dt = datetime.strptime(start_key, "%Y%m").replace(tzinfo=UTC)
        end = datetime.strptime(end_key, "%Y%m").replace(tzinfo=UTC)
        while dt <= end:
            yield dt.strftime("%Y%m")
            year = dt.year + (1 if dt.month == 12 else 0)
            month = 1 if dt.month == 12 else dt.month + 1
            dt = dt.replace(year=year, month=month)
        return

    dt = datetime.strptime(start_key, "%Y%m%d").replace(tzinfo=UTC)
    end = datetime.strptime(end_key, "%Y%m%d").replace(tzinfo=UTC)
    while dt <= end:
        yield dt.strftime("%Y%m%d")
        dt += timedelta(days=1)


def _read_first_last_ts(zip_path: Path) -> tuple[int | None, int | None]:
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [name for name in zf.namelist() if name.endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"expected 1 csv entry, found {len(csv_names)}")
        with zf.open(csv_names[0], "r") as fh:
            first_line_raw = fh.readline()
            if not first_line_raw:
                return None, None
            first_line = first_line_raw.decode("utf-8").strip()
            last_line = first_line
            for raw in fh:
                line = raw.decode("utf-8").strip()
                if line:
                    last_line = line
    first_ts = int(first_line.split(",", 1)[0])
    last_ts = int(last_line.split(",", 1)[0])
    return first_ts, last_ts


def _count_rows_in_zip(zip_path: Path) -> int:
    with zipfile.ZipFile(zip_path) as zf:
        csv_names = [name for name in zf.namelist() if name.endswith(".csv")]
        if len(csv_names) != 1:
            raise ValueError(f"expected 1 csv entry, found {len(csv_names)}")
        with zf.open(csv_names[0], "r") as fh:
            rows = 0
            while True:
                chunk = fh.read(1 << 20)
                if not chunk:
                    break
                rows += chunk.count(b"\n")
    return rows


def _local_keys(symbol: str, resolution: str, output_root: Path) -> set[str]:
    folder = RESOLUTIONS[resolution]["folder"]
    symbol_dir = output_root / folder / symbol
    if not symbol_dir.is_dir():
        return set()

    keys = set()
    for path in symbol_dir.iterdir():
        if not path.is_file():
            continue
        key = _parse_key(path.name, resolution)
        if key is not None:
            keys.add(key)
    return keys


def scan_local(
    symbol: str,
    resolution: str,
    output_root: Path,
    count_rows: bool = False,
) -> LocalStat:
    folder = RESOLUTIONS[resolution]["folder"]
    stat = LocalStat(symbol=symbol, resolution=resolution)
    symbol_dir = output_root / folder / symbol

    if not symbol_dir.is_dir():
        return stat

    key_to_path: dict[str, Path] = {}
    for path in symbol_dir.iterdir():
        if not path.is_file():
            continue
        key = _parse_key(path.name, resolution)
        if key is None:
            continue
        key_to_path[key] = path

    keys = sorted(key_to_path)
    stat.zip_count = len(keys)
    if not keys:
        return stat

    stat.first_key = keys[0]
    stat.last_key = keys[-1]

    expected = set(_iter_expected_keys(stat.first_key, stat.last_key, resolution))
    actual = set(keys)
    stat.gap_count = len(expected - actual)

    try:
        if count_rows:
            for key in keys:
                stat.row_count += _count_rows_in_zip(key_to_path[key])
        first_ts, first_last_ts = _read_first_last_ts(key_to_path[stat.first_key])
        if stat.first_key == stat.last_key:
            last_ts = first_last_ts
        else:
            _, last_ts = _read_first_last_ts(key_to_path[stat.last_key])
        stat.first_ts = first_ts
        stat.last_ts = last_ts
    except Exception as exc:
        stat.bad_zip_count += 1
        stat.error = str(exc)

    return stat


def fetch_clickhouse(symbol: str, resolution: str) -> ClickHouseStat:
    table = f"{RESOLUTIONS[resolution]['table_prefix']}{symbol}"
    client = get_client()
    try:
        rows = client.execute(
            f"SELECT count(), min(open_time), max(open_time) FROM {table} "
            f"WHERE open_time >= {START_MS} AND open_time < {END_MS}"
        )
    except Exception as exc:
        if "Unknown table" in str(exc):
            return ClickHouseStat(symbol=symbol, resolution=resolution, exists=False)
        return ClickHouseStat(
            symbol=symbol, resolution=resolution, exists=False, error=str(exc)
        )

    row_count, min_ts, max_ts = rows[0]
    if row_count == 0:
        return ClickHouseStat(
            symbol=symbol,
            resolution=resolution,
            exists=True,
            row_count=0,
        )
    return ClickHouseStat(
        symbol=symbol,
        resolution=resolution,
        exists=True,
        row_count=row_count,
        min_ts=min_ts,
        max_ts=max_ts,
    )


def fetch_clickhouse_slice_keys(symbol: str, resolution: str) -> set[str]:
    table = f"{RESOLUTIONS[resolution]['table_prefix']}{symbol}"
    if resolution == "1d":
        expr = "formatDateTime(toDateTime(intDiv(open_time, 1000), 'UTC'), '%Y%m')"
    else:
        expr = "formatDateTime(toDateTime(intDiv(open_time, 1000), 'UTC'), '%Y%m%d')"

    client = get_client()
    rows = client.execute(
        f"SELECT DISTINCT {expr} AS slice_key FROM {table} "
        f"WHERE open_time >= {START_MS} AND open_time < {END_MS} "
        f"ORDER BY slice_key"
    )
    return {row[0] for row in rows}


def diff_slice_keys(symbol: str, resolution: str, output_root: Path) -> tuple[list[str], list[str]]:
    local_keys = _local_keys(symbol, resolution, output_root)
    source_keys = fetch_clickhouse_slice_keys(symbol, resolution)
    missing_local = sorted(source_keys - local_keys)
    extra_local = sorted(local_keys - source_keys)
    return missing_local, extra_local


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("bench/data/custom/binance"),
        help="Root of the custom binance output tree",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=8,
        help="Parallel workers for both local scanning and ClickHouse probes",
    )
    parser.add_argument(
        "--compare-clickhouse",
        action="store_true",
        help="Compare local first/last timestamps against ClickHouse",
    )
    parser.add_argument(
        "--compare-counts",
        action="store_true",
        help="Also compare local row counts against ClickHouse row counts",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Only validate the first N symbols after sorting",
    )
    args = parser.parse_args()

    if args.compare_clickhouse:
        client = get_client()
        symbols = discover_symbols(client)
    else:
        symbols: set[str] = set()
        for resolution in RESOLUTIONS:
            folder = args.output_dir / RESOLUTIONS[resolution]["folder"]
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                if path.is_dir():
                    symbols.add(path.name)
        symbols = sorted(symbols)

    if args.limit is not None:
        symbols = symbols[: args.limit]

    tasks = [(symbol, resolution) for symbol in symbols for resolution in RESOLUTIONS]
    print(f"Symbols: {len(symbols)}")
    print(f"Tasks: {len(tasks)}")
    print(f"Local root: {args.output_dir}")
    print(
        f"Range: {ts_to_str(START_MS)} UTC to "
        f"{ts_to_str(END_MS - 1)} UTC"
    )

    local_stats: dict[tuple[str, str], LocalStat] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(
                scan_local,
                symbol,
                resolution,
                args.output_dir,
                args.compare_counts,
            ): (symbol, resolution)
            for symbol, resolution in tasks
        }
        for future in as_completed(futures):
            stat = future.result()
            local_stats[(stat.symbol, stat.resolution)] = stat

    if not args.compare_clickhouse:
        present = sum(1 for stat in local_stats.values() if stat.zip_count > 0)
        gaps = sum(1 for stat in local_stats.values() if stat.gap_count > 0)
        bad = sum(1 for stat in local_stats.values() if stat.bad_zip_count > 0)
        print(f"Present symbol/resolution outputs: {present}/{len(tasks)}")
        print(f"Outputs with internal slice gaps: {gaps}")
        print(f"Outputs with unreadable edge zips: {bad}")
        return 0

    ch_stats: dict[tuple[str, str], ClickHouseStat] = {}
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {
            pool.submit(fetch_clickhouse, symbol, resolution): (symbol, resolution)
            for symbol, resolution in tasks
        }
        for future in as_completed(futures):
            stat = future.result()
            ch_stats[(stat.symbol, stat.resolution)] = stat

    ok = 0
    missing_local = 0
    count_mismatch = 0
    mismatched_edges = 0
    local_gaps = 0
    source_errors = 0
    bad_local = 0
    extra_local_no_source = 0
    issues: list[str] = []

    for task in tasks:
        local = local_stats[task]
        source = ch_stats[task]

        if source.error:
            source_errors += 1
            issues.append(f"{local.symbol}/{local.resolution}: ClickHouse error: {source.error}")
            continue

        if not source.exists or source.row_count == 0:
            if local.zip_count > 0:
                extra_local_no_source += 1
                issues.append(
                    f"{local.symbol}/{local.resolution}: local files exist but source has no rows"
                )
            continue

        if local.zip_count == 0:
            missing_local += 1
            issues.append(
                f"{local.symbol}/{local.resolution}: missing local output, "
                f"source range {ts_to_str(source.min_ts)} to {ts_to_str(source.max_ts)}"
            )
            continue

        if local.bad_zip_count > 0 or local.error:
            bad_local += 1
            issues.append(f"{local.symbol}/{local.resolution}: bad local zip: {local.error}")
            continue

        if local.gap_count > 0:
            missing_gap_keys, extra_gap_keys = diff_slice_keys(
                local.symbol, local.resolution, args.output_dir
            )
            if missing_gap_keys or extra_gap_keys:
                local_gaps += 1
                detail_parts = []
                if missing_gap_keys:
                    detail_parts.append(
                        f"missing local slices: {','.join(missing_gap_keys[:10])}"
                    )
                if extra_gap_keys:
                    detail_parts.append(
                        f"extra local slices: {','.join(extra_gap_keys[:10])}"
                    )
                issues.append(
                    f"{local.symbol}/{local.resolution}: " + "; ".join(detail_parts)
                )
                continue

        if args.compare_counts and local.row_count != source.row_count:
            count_mismatch += 1
            issues.append(
                f"{local.symbol}/{local.resolution}: local rows {local.row_count:,} "
                f"vs source rows {source.row_count:,}"
            )
            continue

        if local.first_ts != source.min_ts or local.last_ts != source.max_ts:
            mismatched_edges += 1
            issues.append(
                f"{local.symbol}/{local.resolution}: local "
                f"{ts_to_str(local.first_ts)}..{ts_to_str(local.last_ts)} vs source "
                f"{ts_to_str(source.min_ts)}..{ts_to_str(source.max_ts)}"
            )
            continue

        ok += 1

    print()
    print(f"Validated against ClickHouse: {len(tasks)} symbol/resolution pairs")
    print(f"OK: {ok}")
    print(f"Missing local output: {missing_local}")
    if args.compare_counts:
        print(f"Row count mismatch: {count_mismatch}")
    print(f"Local edge mismatch: {mismatched_edges}")
    print(f"Local internal slice gaps: {local_gaps}")
    print(f"Unreadable local zips: {bad_local}")
    print(f"Extra local output where source empty: {extra_local_no_source}")
    print(f"Source probe errors: {source_errors}")

    if issues:
        print()
        print("Sample issues:")
        for line in issues[:50]:
            print(f"  - {line}")

    return 0 if not issues else 1


if __name__ == "__main__":
    raise SystemExit(main())
