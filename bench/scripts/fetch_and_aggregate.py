#!/usr/bin/env python3
"""Download Binance aggTrades and aggregate into 12-column kline zip files.

This script replicates the exact data pipeline used to produce the
benchmark's extended kline dataset.  It downloads raw aggTrades from
Binance's public data archive (data.binance.vision), aggregates them
into klines with per-side taker/maker volume breakdown, and writes
the result as zip-sliced files ready for LEAN custom-data backtesting.

The aggregation is bit-identical to the upstream pipeline:
  - Each trade's `is_buyer_maker` (m) flag determines direction:
      m=false  →  buyer is taker  →  taker_buy_*
      m=true   →  seller is taker →  taker_sell_*
  - Time window: floor(timestamp_ms / interval_ms) * interval_ms
  - 12 output columns per kline bar (no header):
      open_time_ms, open, high, low, close, volume,
      taker_buy_volume, taker_sell_volume,
      taker_buy_quote_volume, taker_sell_quote_volume,
      taker_buy_trades, taker_sell_trades

Output directory structure:
  {output_root}/{resolution}/{symbol_lower}/{slice_key}_trade.zip
    -> {slice_key}_{symbol_lower}_{resolution}.csv

Usage:
  # Fetch BTCUSDT 1h klines for Jan 2024
  python fetch_and_aggregate.py --symbol BTCUSDT --start 2024-01-01 \\
      --end 2024-01-31 --interval 1h --output-dir bench/data/12col

  # Fetch multiple intervals at once
  python fetch_and_aggregate.py --symbol BTCUSDT --start 2024-01-01 \\
      --end 2024-12-31 --interval 1m,1h,1d --output-dir bench/data/12col

  # Fetch COIN-margined contract
  python fetch_and_aggregate.py --symbol BTCUSD_PERP --start 2024-01-01 \\
      --end 2024-01-31 --interval 1h --output-dir bench/data/12col \\
      --contract-type coin
"""

from __future__ import annotations

import argparse
import asyncio
import io
import ssl
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, NamedTuple

import aiohttp
from tqdm import tqdm

# ── Interval definitions ──────────────────────────────────────────────

INTERVAL_MS: dict[str, int] = {
    "1s": 1_000,
    "1m": 60_000,
    "3m": 180_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "2h": 7_200_000,
    "4h": 14_400_000,
    "6h": 21_600_000,
    "8h": 28_800_000,
    "12h": 43_200_000,
    "1d": 86_400_000,
}

RESOLUTION_FOLDER: dict[str, str] = {
    "1s": "second",
    "1m": "minute",
    "3m": "3minute",
    "5m": "5minute",
    "15m": "15minute",
    "30m": "30minute",
    "1h": "hour",
    "2h": "2hour",
    "4h": "4hour",
    "6h": "6hour",
    "8h": "8hour",
    "12h": "12hour",
    "1d": "daily",
}


# ── Lightweight trade struct ──────────────────────────────────────────

class AggTradeLite(NamedTuple):
    """Lightweight aggTrade: price, quantity, timestamp_ms, is_buyer_maker."""
    p: Decimal
    q: Decimal
    T: int
    m: bool

    @staticmethod
    def from_csv_line(line: str) -> AggTradeLite:
        """Parse a CSV line: a,p,q,f,l,T,m"""
        parts = line.split(",")
        return AggTradeLite(
            p=Decimal(parts[1]),
            q=Decimal(parts[2]),
            T=int(parts[5]),
            m=parts[6].strip().lower() == "true",
        )

    @property
    def is_taker_buy(self) -> bool:
        """m=false means buyer is taker (active buy)."""
        return not self.m


# ── Kline state accumulator ──────────────────────────────────────────

class _KlineState:
    """Accumulates trades into a single kline bar."""

    __slots__ = (
        "open_time", "open_price", "high_price", "low_price", "close_price",
        "volume", "taker_buy_volume", "taker_sell_volume",
        "taker_buy_quote_volume", "taker_sell_quote_volume",
        "taker_buy_trades", "taker_sell_trades",
    )

    def __init__(self, open_time: int, price: Decimal) -> None:
        self.open_time = open_time
        self.open_price = price
        self.high_price = price
        self.low_price = price
        self.close_price = price
        self.volume = Decimal(0)
        self.taker_buy_volume = Decimal(0)
        self.taker_sell_volume = Decimal(0)
        self.taker_buy_quote_volume = Decimal(0)
        self.taker_sell_quote_volume = Decimal(0)
        self.taker_buy_trades = 0
        self.taker_sell_trades = 0

    def update(self, trade: AggTradeLite) -> None:
        if trade.p > self.high_price:
            self.high_price = trade.p
        if trade.p < self.low_price:
            self.low_price = trade.p
        self.close_price = trade.p

        self.volume += trade.q
        quote_qty = trade.p * trade.q

        if trade.is_taker_buy:
            self.taker_buy_volume += trade.q
            self.taker_buy_quote_volume += quote_qty
            self.taker_buy_trades += 1
        else:
            self.taker_sell_volume += trade.q
            self.taker_sell_quote_volume += quote_qty
            self.taker_sell_trades += 1

    def to_tuple(self) -> tuple[Any, ...]:
        """12-field tuple matching the canonical kline schema."""
        return (
            self.open_time,
            float(self.open_price),
            float(self.high_price),
            float(self.low_price),
            float(self.close_price),
            float(self.volume),
            float(self.taker_buy_volume),
            float(self.taker_sell_volume),
            float(self.taker_buy_quote_volume),
            float(self.taker_sell_quote_volume),
            self.taker_buy_trades,
            self.taker_sell_trades,
        )


def _reaggregate_from_base(
    base_klines: list[tuple[Any, ...]],
    target_ms: int,
) -> list[tuple[Any, ...]]:
    """Re-aggregate 1m kline tuples into a coarser interval using float arithmetic.

    This matches ClickHouse's aggregation path (SUM of Float64 values from
    1m materialized view), ensuring identical float rounding.

    Uses math.fsum (compensated Kahan summation) for volume/quote columns
    to minimise float64 accumulation error across large sums.

    Each input tuple: (open_time, O, H, L, C, vol, tbv, tsv, tbqv, tsqv, tbt, tst)
    """
    import math

    buckets: dict[int, list[tuple[Any, ...]]] = {}
    for row in base_klines:
        window = (row[0] // target_ms) * target_ms
        buckets.setdefault(window, []).append(row)

    result: list[tuple[Any, ...]] = []
    for window in sorted(buckets):
        bars = buckets[window]
        # OHLC from first/max/min/last
        o = bars[0][1]
        h = max(b[2] for b in bars)
        lo = min(b[3] for b in bars)
        c = bars[-1][4]
        # Sums via math.fsum (compensated summation, minimal float error)
        vol  = math.fsum(b[5] for b in bars)
        tbv  = math.fsum(b[6] for b in bars)
        tsv  = math.fsum(b[7] for b in bars)
        tbqv = math.fsum(b[8] for b in bars)
        tsqv = math.fsum(b[9] for b in bars)
        tbt = sum(b[10] for b in bars)
        tst = sum(b[11] for b in bars)
        result.append((window, o, h, lo, c, vol, tbv, tsv, tbqv, tsqv, tbt, tst))
    return result


# ── Streaming aggregator ─────────────────────────────────────────────

class StreamingKlineAggregator:
    """Aggregate trades into klines on-the-fly, constant memory."""

    def __init__(self, intervals: list[str]) -> None:
        self._intervals = intervals
        # Intervals that need direct trade-level accumulation:
        # 1m always (base for re-aggregation), plus any sub-minute requested.
        self._trade_intervals = [iv for iv in intervals if INTERVAL_MS[iv] < 60_000]
        if "1m" not in self._trade_intervals:
            self._trade_intervals.append("1m")
        self._interval_ms = {iv: INTERVAL_MS[iv] for iv in self._trade_intervals}
        self._states: dict[str, dict[int, _KlineState]] = {
            iv: {} for iv in self._trade_intervals
        }
        self.trade_count = 0

    def add_trade(self, trade: AggTradeLite) -> None:
        self.trade_count += 1
        # Only accumulate trades into 1m (and sub-minute) buckets.
        # Higher timeframes (5m, 1h, 1d, ...) are re-aggregated from
        # 1m float tuples in finalize(), matching ClickHouse's path.
        for iv in self._trade_intervals:
            ms = self._interval_ms[iv]
            window_start = (trade.T // ms) * ms

            if window_start not in self._states[iv]:
                self._states[iv][window_start] = _KlineState(
                    open_time=window_start, price=trade.p,
                )
            self._states[iv][window_start].update(trade)

    def finalize(self) -> dict[str, list[tuple[Any, ...]]]:
        """Return sorted kline tuples per interval and clear state.

        Two-pass aggregation for float parity with ClickHouse:
          1. Trades → 1m klines via Decimal accumulation (exact).
          2. 1m klines → higher timeframes via float summation,
             matching ClickHouse's MV aggregation path.

        This eliminates the quote-volume float noise that arises when
        millions of Decimal(price*qty) values are summed directly into
        a 1d bar and then cast to float, vs. summing 1440 float64
        values from 1m bars (which is what ClickHouse does).
        """
        # Pass 1: produce 1m klines from Decimal-precise trade accumulation
        base_iv = "1m"
        if base_iv in self._states and self._states[base_iv]:
            base_klines = [
                self._states[base_iv][ws].to_tuple()
                for ws in sorted(self._states[base_iv])
            ]
        else:
            # No 1m in requested intervals — build from the finest available
            base_klines = None

        result: dict[str, list[tuple[Any, ...]]] = {}

        for iv in self._intervals:
            if iv in self._states:
                # Sub-minute or 1m: from direct trade accumulation
                klines = [
                    self._states[iv][ws].to_tuple()
                    for ws in sorted(self._states[iv])
                ]
            else:
                # Higher timeframes: re-aggregate from 1m float tuples
                klines = _reaggregate_from_base(base_klines, INTERVAL_MS[iv])
            result[iv] = klines

        # Clear all states
        for iv in self._trade_intervals:
            self._states[iv].clear()

        return result


# ── Downloader ────────────────────────────────────────────────────────

BASE_URL = "https://data.binance.vision/data/futures"


def _build_url(symbol: str, target_date: date, contract_type: str) -> str:
    api_type = "um" if contract_type == "usdt" else "cm"
    date_str = target_date.strftime("%Y-%m-%d")
    filename = f"{symbol.upper()}-aggTrades-{date_str}.zip"
    return f"{BASE_URL}/{api_type}/daily/aggTrades/{symbol.upper()}/{filename}"


async def download_day(
    session: aiohttp.ClientSession,
    symbol: str,
    target_date: date,
    contract_type: str,
    aggregator: StreamingKlineAggregator,
    semaphore: asyncio.Semaphore,
    max_retries: int = 3,
) -> tuple[int, bool]:
    """Download one day's aggTrades and stream into the aggregator.

    Returns (trade_count_for_day, file_found).
    """
    url = _build_url(symbol, target_date, contract_type)

    for attempt in range(max_retries):
        try:
            async with semaphore:
                async with session.get(url) as resp:
                    if resp.status == 404:
                        return 0, False
                    if resp.status == 429:
                        wait = 2 ** attempt * 5
                        await asyncio.sleep(wait)
                        continue
                    resp.raise_for_status()
                    zip_bytes = await resp.read()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt == max_retries - 1:
                return 0, False
            await asyncio.sleep(2 ** attempt)
            continue

        # Parse ZIP and stream trades into aggregator
        day_trades = 0
        try:
            with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
                del zip_bytes  # free memory early
                namelist = zf.namelist()
                if not namelist:
                    return 0, True
                with zf.open(namelist[0]) as csv_file:
                    first_line = True
                    for line_bytes in csv_file:
                        line = line_bytes.decode("utf-8").strip()
                        if not line:
                            continue
                        if first_line:
                            first_line = False
                            if not line.split(",")[0].strip().isdigit():
                                continue
                        try:
                            trade = AggTradeLite.from_csv_line(line)
                            aggregator.add_trade(trade)
                            day_trades += 1
                        except Exception:
                            pass
        except zipfile.BadZipFile:
            if attempt == max_retries - 1:
                return 0, False
            continue

        return day_trades, True

    return 0, False


async def download_and_aggregate(
    symbol: str,
    date_start: date,
    date_end: date,
    intervals: list[str],
    contract_type: str = "usdt",
    concurrency: int = 10,
    no_gap_fill: bool = False,
) -> dict[str, list[tuple[Any, ...]]]:
    """Download aggTrades for a date range and aggregate into klines.

    Returns {interval: [12-field tuples sorted by open_time]}.
    Gap-fills missing time windows by default (use no_gap_fill=True to skip).
    """
    args_no_gap_fill = no_gap_fill
    aggregator = StreamingKlineAggregator(intervals)
    semaphore = asyncio.Semaphore(concurrency)

    # Build date list
    dates: list[date] = []
    d = date_start
    while d <= date_end:
        dates.append(d)
        d += timedelta(days=1)

    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE
    connector = aiohttp.TCPConnector(ssl=ssl_ctx, limit=concurrency)
    timeout = aiohttp.ClientTimeout(total=120, sock_read=60)

    total_trades = 0
    missing_days = 0

    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
        # Process in batches to control memory
        batch_size = concurrency * 2
        for batch_start in range(0, len(dates), batch_size):
            batch = dates[batch_start:batch_start + batch_size]
            tasks = [
                download_day(session, symbol, d, contract_type, aggregator, semaphore)
                for d in batch
            ]
            results = await asyncio.gather(*tasks)
            for count, found in results:
                total_trades += count
                if not found:
                    missing_days += 1

            # Progress
            done = min(batch_start + batch_size, len(dates))
            tqdm.write(
                f"  [{done}/{len(dates)} days] "
                f"{total_trades:,} trades aggregated"
            )

    if missing_days > 0:
        tqdm.write(f"  Note: {missing_days} days had no data (404)")

    tqdm.write(f"  Total: {total_trades:,} trades -> finalizing klines...")
    klines = aggregator.finalize()

    if not args_no_gap_fill:
        for iv in intervals:
            if klines.get(iv):
                before = len(klines[iv])
                klines[iv] = fill_gaps(klines[iv], INTERVAL_MS[iv])
                filled = len(klines[iv]) - before
                if filled > 0:
                    tqdm.write(f"  {iv}: gap-filled {filled} missing windows")

    return klines


# ── Gap filling ───────────────────────────────────────────────────────

def fill_gaps(
    klines: list[tuple[Any, ...]],
    interval_ms: int,
) -> list[tuple[Any, ...]]:
    """Forward-fill missing time windows with zero-volume klines.

    For any gap between consecutive klines, inserts bars where
    OHLC = previous close and all volume/count fields are zero.
    Only fills gaps within the existing data range (does not extend
    before the first bar or after the last).
    """
    if len(klines) < 2:
        return klines

    existing_times = {row[0] for row in klines}
    first_time = klines[0][0]
    last_time = klines[-1][0]

    # Walk the timeline and forward-fill
    result: list[tuple[Any, ...]] = []
    data_idx = 0
    prev_close = klines[0][4]  # close field at index 4
    current_time = first_time

    while current_time <= last_time:
        # Advance data_idx to consume all bars at or before current_time,
        # updating prev_close as we go
        while data_idx < len(klines) and klines[data_idx][0] <= current_time:
            prev_close = klines[data_idx][4]
            if klines[data_idx][0] == current_time:
                result.append(klines[data_idx])
            data_idx += 1

        if current_time not in existing_times:
            # Fill with prev close, zero volume/counts
            result.append((
                current_time,
                prev_close, prev_close, prev_close, prev_close,  # OHLC
                0.0, 0.0, 0.0, 0.0, 0.0,  # volumes
                0, 0,  # trade counts
            ))

        current_time += interval_ms

    return result


# ── Zip writer ────────────────────────────────────────────────────────

def _fmt(v: float) -> str:
    """Fixed-point format, no scientific notation."""
    return f"{v:.10f}".rstrip("0").rstrip(".")


def _slice_key(ts_ms: int, interval: str) -> str:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)
    if interval == "1d":
        return dt.strftime("%Y%m")
    return dt.strftime("%Y%m%d")


def _zip_entry_name(slice_key: str, symbol: str, resolution: str) -> str:
    return f"{slice_key}_{symbol.lower()}_{resolution}.csv"


def write_klines_to_zips(
    klines: list[tuple[Any, ...]],
    symbol: str,
    interval: str,
    output_root: Path,
) -> int:
    """Write kline tuples as zip-sliced files. Returns number of slices."""
    resolution = RESOLUTION_FOLDER[interval]
    out_dir = output_root / resolution / symbol.lower()
    out_dir.mkdir(parents=True, exist_ok=True)

    # Group by slice key
    slices: dict[str, list[str]] = {}
    for row in klines:
        key = _slice_key(row[0], interval)
        # Format: open_time, O, H, L, C, V, tbv, tsv, tbqv, tsqv, tbt, tst
        line = ",".join([
            str(row[0]),         # open_time (int ms)
            _fmt(row[1]),        # open
            _fmt(row[2]),        # high
            _fmt(row[3]),        # low
            _fmt(row[4]),        # close
            _fmt(row[5]),        # volume
            _fmt(row[6]),        # taker_buy_volume
            _fmt(row[7]),        # taker_sell_volume
            _fmt(row[8]),        # taker_buy_quote_volume
            _fmt(row[9]),        # taker_sell_quote_volume
            str(row[10]),        # taker_buy_trades (int)
            str(row[11]),        # taker_sell_trades (int)
        ])
        slices.setdefault(key, []).append(line)

    for key, lines in sorted(slices.items()):
        zip_path = out_dir / f"{key}_trade.zip"
        entry_name = _zip_entry_name(key, symbol, resolution)
        csv_content = "\n".join(lines) + "\n"

        tmp_path = zip_path.with_suffix(".zip.tmp")
        try:
            with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
                zf.writestr(entry_name, csv_content)
            tmp_path.replace(zip_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    return len(slices)


# ── CLI ───────────────────────────────────────────────────────────────

def parse_date(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


async def async_main(args: argparse.Namespace) -> None:
    intervals = [iv.strip() for iv in args.interval.split(",")]
    for iv in intervals:
        if iv not in INTERVAL_MS:
            print(f"Error: unsupported interval '{iv}'", file=sys.stderr)
            print(f"  Supported: {', '.join(INTERVAL_MS)}", file=sys.stderr)
            sys.exit(1)

    output_root = Path(args.output_dir)
    symbol = args.symbol.upper()

    print(f"=== Fetch & Aggregate: {symbol} ===")
    print(f"  Date range: {args.start} to {args.end}")
    print(f"  Intervals: {', '.join(intervals)}")
    print(f"  Contract type: {args.contract_type}")
    print(f"  Concurrency: {args.concurrency}")
    print(f"  Output: {output_root}")
    print()

    klines_by_interval = await download_and_aggregate(
        symbol=symbol,
        date_start=args.start,
        date_end=args.end,
        intervals=intervals,
        contract_type=args.contract_type,
        concurrency=args.concurrency,
        no_gap_fill=args.no_gap_fill,
    )

    print()
    for iv, klines in klines_by_interval.items():
        if not klines:
            print(f"  {iv}: no klines produced")
            continue
        n_slices = write_klines_to_zips(klines, symbol, iv, output_root)
        print(f"  {iv}: {len(klines):,} bars -> {n_slices} zip slices")

    print("\nDone.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Download Binance aggTrades and aggregate into 12-column kline zip files."
    )
    parser.add_argument("--symbol", required=True, help="Trading symbol (e.g. BTCUSDT)")
    parser.add_argument("--start", type=parse_date, required=True, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=parse_date, required=True, help="End date (YYYY-MM-DD)")
    parser.add_argument(
        "--interval", default="1h",
        help="Comma-separated intervals (e.g. '1m,1h,1d'). Default: 1h"
    )
    parser.add_argument("--output-dir", type=str, required=True, help="Output root directory")
    parser.add_argument(
        "--contract-type", choices=["usdt", "coin"], default="usdt",
        help="Contract type. Default: usdt"
    )
    parser.add_argument(
        "--concurrency", type=int, default=10,
        help="Download concurrency. Default: 10"
    )
    parser.add_argument(
        "--no-gap-fill", action="store_true",
        help="Skip forward-filling missing time windows (gaps will be absent from output)"
    )
    args = parser.parse_args()

    asyncio.run(async_main(args))


if __name__ == "__main__":
    main()
