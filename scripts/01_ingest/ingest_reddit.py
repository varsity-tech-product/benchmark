#!/usr/bin/env python3
"""
Ingest Reddit finance subreddit data from Arctic Shift dumps.

Downloads zstandard-compressed submissions and comments for selected subreddits.
"""

import argparse
import sys
from pathlib import Path

import requests
from tqdm import tqdm

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw" / "reddit"

# Arctic Shift base URL
ARCTIC_SHIFT_BASE = "https://arctic-shift.com/download/dump"

SUBREDDITS = [
    "personalfinance",
    "investing",
    "financialindependence",
    "stocks",
    "tax",
    "realestateinvesting",
]


def download_file(url: str, dest_path: Path, resume: bool = True) -> bool:
    """
    Download a file with progress bar and resume support.

    Args:
        url: URL to download
        dest_path: Path to save the file
        resume: Whether to resume partial downloads

    Returns:
        True if successful
    """
    headers = {}
    mode = "wb"
    initial_size = 0

    if resume and dest_path.exists():
        initial_size = dest_path.stat().st_size
        headers["Range"] = f"bytes={initial_size}-"
        mode = "ab"
        print(f"  Resuming from {initial_size / 1024 / 1024:.1f} MB")

    try:
        response = requests.get(url, stream=True, headers=headers, timeout=30)

        if response.status_code == 416:
            print(f"  Already complete: {dest_path.name}")
            return True
        elif response.status_code == 206:
            print("  Resuming download...")
        elif response.status_code == 200:
            if initial_size > 0:
                print("  Server doesn't support resume, starting fresh")
                mode = "wb"
                initial_size = 0
        else:
            response.raise_for_status()

        content_length = response.headers.get("content-length")
        total_size = int(content_length) + initial_size if content_length else None

        with open(dest_path, mode) as f:
            with tqdm(
                total=total_size,
                initial=initial_size,
                unit="iB",
                unit_scale=True,
                desc=f"  {dest_path.name}",
            ) as pbar:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    size = f.write(chunk)
                    pbar.update(size)

        return True

    except requests.RequestException as e:
        print(f"  Error downloading {url}: {e}")
        return False


def ingest_subreddit(subreddit: str) -> bool:
    """
    Download submissions and comments dumps for a subreddit.

    Args:
        subreddit: Subreddit name (without r/)

    Returns:
        True if all downloads succeeded
    """
    output_dir = RAW_DATA_DIR / subreddit
    output_dir.mkdir(parents=True, exist_ok=True)

    success = True
    for file_type in ("submissions", "comments"):
        dest_path = output_dir / f"{file_type}.zst"

        if dest_path.exists() and dest_path.stat().st_size > 0:
            print(f"  Skipping {file_type}.zst (already exists)")
            continue

        url = f"{ARCTIC_SHIFT_BASE}/reddit/{file_type}/subreddit/{subreddit}.zst"
        print(f"  Downloading {file_type} for r/{subreddit}...")

        if not download_file(url, dest_path):
            success = False

    return success


def main():
    parser = argparse.ArgumentParser(
        description="Download Reddit finance subreddit data from Arctic Shift dumps"
    )
    parser.add_argument(
        "--subreddit",
        type=str,
        choices=SUBREDDITS,
        default=None,
        help="Download a specific subreddit (default: all)",
    )

    args = parser.parse_args()

    subreddits = [args.subreddit] if args.subreddit else SUBREDDITS

    print("Reddit Data Ingestion")
    print("=" * 60)
    print(f"Subreddits: {', '.join(subreddits)}")
    print(f"Output directory: {RAW_DATA_DIR}")
    print()

    failed = []
    for sub in subreddits:
        print(f"\nr/{sub}")
        print("-" * 40)
        if not ingest_subreddit(sub):
            failed.append(sub)

    print("\n" + "=" * 60)
    if failed:
        print(f"Failed subreddits: {', '.join(failed)}")
        sys.exit(1)
    else:
        print("Reddit ingestion complete!")
        print(f"Data location: {RAW_DATA_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
