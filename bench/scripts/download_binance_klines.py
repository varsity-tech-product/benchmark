#!/usr/bin/env python3
"""Download and freeze Binance USDT-M futures datasets for S- and B-series tasks.

The kline files come from Binance Data Vision daily archives. Funding-rate data
is pulled from Binance's public futures REST API because it is lightweight and
does not require authentication.

Features:
  - Automatic retry with exponential backoff (429 / 5xx safe)
  - Per-day checkpoint caching (resume interrupted downloads)
  - CHECKSUM verification for zip archives
  - Relaxed gap validation for 1d interval (warns on missing days)
"""

from __future__ import annotations

import argparse
import hashlib
import io
import sys
import zipfile
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

DATA_VISION_BASE = "https://data.binance.vision/data/futures/um/daily/klines"
FUNDING_RATE_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
RAW_KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]
STANDARD_COLUMNS = [
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "quote_volume",
    "trade_count",
    "taker_buy_vol",
    "taker_buy_quote_vol",
]

# Funding rate: 3 times per day (every 8h) for most of the data range.
_FUNDING_RATE_PER_DAY = 3


@dataclass(frozen=True)
class KlineDataset:
    output_name: str
    symbol: str
    interval: str
    start_date: date
    end_date: date


@dataclass(frozen=True)
class FundingDataset:
    output_name: str
    symbol: str
    start_date: date
    end_date: date


KLINE_DATASETS = {
    "BTCUSDT_1d_2021_2024.csv": KlineDataset(
        output_name="BTCUSDT_1d_2021_2024.csv",
        symbol="BTCUSDT",
        interval="1d",
        start_date=date(2021, 1, 1),
        end_date=date(2024, 12, 31),
    ),
    "ETHUSDT_1d_2021_2024.csv": KlineDataset(
        output_name="ETHUSDT_1d_2021_2024.csv",
        symbol="ETHUSDT",
        interval="1d",
        start_date=date(2021, 1, 1),
        end_date=date(2024, 12, 31),
    ),
    "BTCUSDT_1h_2023_2024.csv": KlineDataset(
        output_name="BTCUSDT_1h_2023_2024.csv",
        symbol="BTCUSDT",
        interval="1h",
        start_date=date(2023, 1, 1),
        end_date=date(2024, 12, 31),
    ),
    "ETHUSDT_1h_2023_2024.csv": KlineDataset(
        output_name="ETHUSDT_1h_2023_2024.csv",
        symbol="ETHUSDT",
        interval="1h",
        start_date=date(2023, 1, 1),
        end_date=date(2024, 12, 31),
    ),
    "BTCUSDT_5m_2024Q4.csv": KlineDataset(
        output_name="BTCUSDT_5m_2024Q4.csv",
        symbol="BTCUSDT",
        interval="5m",
        start_date=date(2024, 10, 1),
        end_date=date(2024, 12, 31),
    ),
}

FUNDING_DATASETS = {
    "BTCUSDT_funding_2021_2024.csv": FundingDataset(
        output_name="BTCUSDT_funding_2021_2024.csv",
        symbol="BTCUSDT",
        start_date=date(2021, 1, 1),
        end_date=date(2024, 12, 31),
    )
}


def _build_retry_session() -> requests.Session:
    """Create a requests.Session with automatic retry on transient errors."""
    retry = Retry(
        total=5,
        backoff_factor=1.0,  # 1s, 2s, 4s, 8s, 16s
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session = requests.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update({"User-Agent": "QuantTutorBench/binance-data-freezer"})
    return session


def daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _cache_dir_for(output_dir: Path, dataset_name: str) -> Path:
    """Return the per-day checkpoint cache directory for a dataset."""
    cache = output_dir / ".cache" / dataset_name.replace(".csv", "")
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def _verify_checksum(
    session: requests.Session,
    zip_url: str,
    zip_content: bytes,
) -> bool:
    """Download and verify the CHECKSUM file for a zip archive.

    Returns True if checksum matches or CHECKSUM file is unavailable.
    Returns False only on actual mismatch.
    """
    checksum_url = zip_url + ".CHECKSUM"
    try:
        resp = session.get(checksum_url, timeout=15)
        if resp.status_code != 200:
            return True  # CHECKSUM not available, skip
        # Format: "<sha256>  <filename>\n"
        expected_hash = resp.text.strip().split()[0].lower()
        actual_hash = hashlib.sha256(zip_content).hexdigest().lower()
        return actual_hash == expected_hash
    except Exception:
        return True  # network error on checksum, don't block


def fetch_daily_kline_archive(
    session: requests.Session,
    symbol: str,
    interval: str,
    day: date,
    cache_dir: Path | None = None,
) -> pd.DataFrame:
    """Download a single day's kline archive, using cache if available."""
    # Check cache first
    if cache_dir is not None:
        cached = cache_dir / f"{day.isoformat()}.parquet"
        if cached.exists():
            return pd.read_parquet(cached)

    url = (
        f"{DATA_VISION_BASE}/{symbol}/{interval}/"
        f"{symbol}-{interval}-{day.isoformat()}.zip"
    )
    response = session.get(url, timeout=30)
    response.raise_for_status()

    # Verify checksum
    if not _verify_checksum(session, url, response.content):
        raise ValueError(f"CHECKSUM mismatch for {url}")

    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        members = archive.namelist()
        if not members:
            raise ValueError(f"Zip archive had no members: {url}")
        with archive.open(members[0]) as fh:
            df = pd.read_csv(fh, header=None, names=RAW_KLINE_COLUMNS)

    normalized = normalize_kline_frame(df)

    # Save to cache (parquet is compact and fast)
    if cache_dir is not None:
        normalized.to_parquet(cached, index=False)

    return normalized


def normalize_kline_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Standardize raw Binance kline columns to the repo schema."""
    numeric_cols = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "quote_volume",
        "count",
        "taker_buy_volume",
        "taker_buy_quote_volume",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    normalized = pd.DataFrame(
        {
            "timestamp": df["open_time"].astype("Int64"),
            "open": df["open"],
            "high": df["high"],
            "low": df["low"],
            "close": df["close"],
            "volume": df["volume"],
            "quote_volume": df["quote_volume"],
            "trade_count": df["count"].astype("Int64"),
            "taker_buy_vol": df["taker_buy_volume"],
            "taker_buy_quote_vol": df["taker_buy_quote_volume"],
        }
    )
    return normalized


def validate_kline_frame(df: pd.DataFrame, interval: str) -> None:
    """Validate kline integrity after concatenation.

    For 1d interval, gaps are reported as warnings (Binance may have
    maintenance days or listing gaps). For sub-daily intervals (1h, 5m),
    gaps are treated as hard errors.
    """
    missing = [col for col in STANDARD_COLUMNS if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    if df["timestamp"].isna().any():
        raise ValueError("Found NaN timestamps in kline data.")

    if not df["timestamp"].is_monotonic_increasing:
        raise ValueError("Timestamps are not monotonic increasing.")

    if df["timestamp"].duplicated().any():
        raise ValueError("Duplicate timestamps found in merged kline data.")

    expected_ms = {
        "5m": 5 * 60 * 1000,
        "1h": 60 * 60 * 1000,
        "1d": 24 * 60 * 60 * 1000,
    }[interval]
    deltas = df["timestamp"].diff().dropna()
    if deltas.empty:
        return

    bad_mask = deltas != expected_ms
    if not bad_mask.any():
        return

    if interval == "1d":
        # For daily data, warn about gaps but don't fail.
        # Binance futures trade 24/7 but archives can have missing days.
        n_gaps = int(bad_mask.sum())
        gap_days = deltas[bad_mask] / (24 * 60 * 60 * 1000)
        max_gap = gap_days.max()
        print(
            f"  WARNING: {n_gaps} gap(s) detected in daily kline data "
            f"(max gap: {max_gap:.0f} days). This is normal for Binance archives."
        )
    else:
        bad = deltas[bad_mask].iloc[0]
        raise ValueError(f"Gap or overlap detected for interval {interval}: {bad}")


def download_kline_dataset(
    session: requests.Session,
    dataset: KlineDataset,
    output_dir: Path,
) -> Path:
    cache_dir = _cache_dir_for(output_dir, dataset.output_name)
    frames = []
    days = list(daterange(dataset.start_date, dataset.end_date))
    skipped = 0

    for day in tqdm(days, desc=f"[kline] {dataset.output_name}", unit="day"):
        cached = cache_dir / f"{day.isoformat()}.parquet"
        if cached.exists():
            skipped += 1
        frames.append(
            fetch_daily_kline_archive(
                session, dataset.symbol, dataset.interval, day, cache_dir
            )
        )

    if skipped > 0:
        print(f"  (resumed: {skipped}/{len(days)} days from cache)")

    merged = pd.concat(frames, ignore_index=True)
    merged = merged.dropna(subset=["timestamp"])
    merged = (
        merged.sort_values("timestamp")
        .drop_duplicates("timestamp")
        .reset_index(drop=True)
    )
    validate_kline_frame(merged, dataset.interval)

    out_path = output_dir / dataset.output_name
    merged.to_csv(out_path, index=False)

    # Clean up cache after successful write
    _clean_cache(cache_dir)

    return out_path


def _clean_cache(cache_dir: Path) -> None:
    """Remove checkpoint cache after successful dataset completion."""
    try:
        for f in cache_dir.iterdir():
            f.unlink()
        cache_dir.rmdir()
        # Also remove parent .cache dir if empty
        parent = cache_dir.parent
        if parent.name == ".cache" and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def download_funding_dataset(
    session: requests.Session,
    dataset: FundingDataset,
    output_dir: Path,
) -> Path:
    start_ms = int(
        datetime.combine(
            dataset.start_date, datetime.min.time(), tzinfo=timezone.utc
        ).timestamp()
        * 1000
    )
    end_ms = (
        int(
            datetime.combine(
                dataset.end_date + timedelta(days=1),
                datetime.min.time(),
                tzinfo=timezone.utc,
            ).timestamp()
            * 1000
        )
        - 1
    )

    # Estimate total rows: 3 funding events per day
    total_days = (dataset.end_date - dataset.start_date).days + 1
    estimated_total = total_days * _FUNDING_RATE_PER_DAY

    rows: list[dict] = []
    cursor = start_ms
    pbar = tqdm(
        desc=f"[funding] {dataset.output_name}",
        unit="row",
        total=estimated_total,
    )
    while cursor <= end_ms:
        params = {
            "symbol": dataset.symbol,
            "startTime": cursor,
            "endTime": end_ms,
            "limit": 1000,
        }
        response = session.get(FUNDING_RATE_URL, params=params, timeout=30)
        response.raise_for_status()
        batch = response.json()
        if not batch:
            break
        rows.extend(batch)
        cursor = int(batch[-1]["fundingTime"]) + 1
        pbar.update(len(batch))
        if len(batch) < 1000:
            break
    pbar.close()

    if not rows:
        raise ValueError(f"No funding-rate rows returned for {dataset.symbol}.")

    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_numeric(df["fundingTime"], errors="coerce").astype("Int64")
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    df["mark_price"] = pd.to_numeric(df["markPrice"], errors="coerce")
    df = df[["timestamp", "funding_rate", "mark_price"]].sort_values("timestamp")
    df = df.drop_duplicates("timestamp").reset_index(drop=True)

    if df["timestamp"].isna().any():
        raise ValueError("Found NaN timestamps in funding-rate data.")
    if not df["timestamp"].is_monotonic_increasing:
        raise ValueError("Funding-rate timestamps are not monotonic increasing.")

    out_path = output_dir / dataset.output_name
    df.to_csv(out_path, index=False)
    return out_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help=(
            "Dataset name to download. Use multiple times for subsets. " "Default: all."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path(__file__).resolve().parent.parent / "data" / "frozen"),
        help="Directory where merged CSV files will be written.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable per-day checkpoint caching (always re-download).",
    )
    return parser.parse_args()


def resolve_requested_datasets(
    dataset_args: list[str],
) -> tuple[list[KlineDataset], list[FundingDataset]]:
    if "all" in dataset_args:
        return list(KLINE_DATASETS.values()), list(FUNDING_DATASETS.values())

    kline = []
    funding = []
    for name in dataset_args:
        if name in KLINE_DATASETS:
            kline.append(KLINE_DATASETS[name])
        elif name in FUNDING_DATASETS:
            funding.append(FUNDING_DATASETS[name])
        else:
            raise KeyError(f"Unknown dataset: {name}")
    return kline, funding


def main() -> int:
    args = parse_args()
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    requested = args.dataset or ["all"]

    try:
        kline_datasets, funding_datasets = resolve_requested_datasets(requested)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    # If --no-cache, prevent caching by not creating cache dirs
    if args.no_cache:
        # Monkey-patch _cache_dir_for to return None
        global _cache_dir_for
        _original = _cache_dir_for
        _cache_dir_for = lambda *_a, **_kw: None  # noqa: E731

    session = _build_retry_session()

    total = len(kline_datasets) + len(funding_datasets)
    for i, dataset in enumerate(kline_datasets, 1):
        print(f"\n[{i}/{total}] {dataset.output_name}")
        out_path = download_kline_dataset(session, dataset, output_dir)
        print(f"  -> Wrote {out_path}")

    for j, dataset in enumerate(funding_datasets, len(kline_datasets) + 1):
        print(f"\n[{j}/{total}] {dataset.output_name}")
        out_path = download_funding_dataset(session, dataset, output_dir)
        print(f"  -> Wrote {out_path}")

    session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
