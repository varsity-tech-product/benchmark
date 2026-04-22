#!/usr/bin/env python3
"""
Ingest Reddit finance subreddit data from Arctic Shift API.

Downloads submissions and comments for selected subreddits via the Arctic Shift
search API, writing zstandard-compressed ndjson files compatible with the
downstream structure_reddit.py pipeline.

API docs: https://arctic-shift.photon-reddit.com/download-tool
"""

import argparse
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
import zstandard as zstd
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

MAX_RETRIES = 5
RETRY_BACKOFF = 2  # seconds, doubled each retry
MAX_CONSECUTIVE_ERRORS = 15  # consecutive API errors before giving up on a task
EMPTY_RESPONSE_RETRIES = 5  # consecutive empty responses before treating as done
EMPTY_RESPONSE_DELAY = 5  # seconds between empty response retries
REQUEST_TIMEOUT = (10, 120)  # (connect, read) seconds

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw" / "reddit"

# Arctic Shift API (new domain)
ARCTIC_SHIFT_API = "https://arctic-shift.photon-reddit.com/api"

SUBREDDITS = [
    "personalfinance",
    "investing",
    "financialindependence",
    "stocks",
    "tax",
    "realestateinvesting",
]

# API endpoint mapping: original file_type -> API resource
API_RESOURCE = {
    "submissions": "posts",
    "comments": "comments",
}


def _make_session() -> requests.Session:
    """Create a requests session with automatic retries and connection pooling."""
    session = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10, pool_connections=5)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def _truncate_to_last_newline(path: Path):
    """Ensure file ends with a complete line (remove any trailing partial line)."""
    size = path.stat().st_size
    if size == 0:
        return
    with open(path, "rb+") as f:
        f.seek(size - 1)
        if f.read(1) == b"\n":
            return  # Already ends with newline
        # Search backwards for the last newline
        pos = size - 2
        while pos >= 0:
            f.seek(pos)
            if f.read(1) == b"\n":
                f.truncate(pos + 1)
                return
            pos -= 1
        # No newline found at all — truncate to empty
        f.truncate(0)


def _count_lines(path: Path) -> int:
    """Count lines in a file efficiently."""
    count = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            count += chunk.count(b"\n")
    return count


def get_min_date(subreddit: str) -> int:
    """
    Get the earliest post timestamp (ms) for a subreddit.

    Returns:
        Millisecond timestamp, or 0 on failure
    """
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                f"{ARCTIC_SHIFT_API}/utils/min",
                params={"subreddit": subreddit, "meta-app": "download-tool"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            date_str = resp.json().get("data")
            if date_str is None:
                return 0
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            return int(dt.timestamp() * 1000)
        except Exception as e:
            if attempt < MAX_RETRIES - 1:
                wait = RETRY_BACKOFF * (2**attempt)
                print(f"  Warning: min date fetch failed ({e}), retrying in {wait}s...")
                time.sleep(wait)
            else:
                print(
                    f"  Error: could not fetch min date after {MAX_RETRIES} attempts: {e}"
                )
                return 0


def get_record_count(subreddit: str) -> dict:
    """
    Get approximate post and comment counts for progress display.

    Returns:
        Dict with 'posts' and 'comments' counts
    """
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(
                f"{ARCTIC_SHIFT_API}/subreddits/search",
                params={"subreddit": subreddit, "meta-app": "download-tool"},
                timeout=REQUEST_TIMEOUT,
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if data:
                meta = data[0].get("_meta", {})
                return {
                    "posts": meta.get("num_posts", 0),
                    "comments": meta.get("num_comments", 0),
                }
            return {"posts": 0, "comments": 0}
        except Exception:
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2**attempt))
    return {"posts": 0, "comments": 0}


def download_data(
    subreddit: str,
    file_type: str,
    dest_path: Path,
    session: requests.Session,
    resume: bool = True,
    tqdm_position: int = 0,
    stop_event: threading.Event = None,
) -> bool:
    """
    Download subreddit data via Arctic Shift API, writing .zst ndjson.

    Paginates through the search API using created_utc cursor.

    Args:
        subreddit: Subreddit name (without r/)
        file_type: "submissions" or "comments"
        dest_path: Path to save the .zst file
        session: Reusable requests.Session for connection pooling
        resume: Whether to resume from a partial download
        tqdm_position: Position for tqdm progress bar (for parallel display)
        stop_event: Threading event to signal graceful stop

    Returns:
        True if all data was downloaded successfully
    """
    resource = API_RESOURCE[file_type]
    url = f"{ARCTIC_SHIFT_API}/{resource}/search"

    # Determine start cursor
    cursor_file = dest_path.with_suffix(".cursor")
    cursor = 0

    if resume and cursor_file.exists():
        cursor = int(cursor_file.read_text().strip())
        print(f"  Resuming r/{subreddit} {file_type} from cursor {cursor}")
    else:
        cursor = get_min_date(subreddit)
        if cursor == 0:
            return False

    # Get total count for progress bar
    counts = get_record_count(subreddit)
    total_estimate = counts.get(resource, 0)

    # Use a temporary .jsonl file for safe append-based resume,
    # then compress to .zst only after all data is fetched.
    tmp_jsonl = dest_path.with_suffix(".jsonl.tmp")
    mode = "a" if (resume and tmp_jsonl.exists() and cursor_file.exists()) else "w"

    # If resuming in append mode, ensure the last line is complete
    # and count existing records for accurate progress display
    existing_records = 0
    if mode == "a":
        _truncate_to_last_newline(tmp_jsonl)
        existing_records = _count_lines(tmp_jsonl)

    new_records = 0
    completed = False
    try:
        with open(tmp_jsonl, mode, encoding="utf-8") as f:
            with tqdm(
                total=total_estimate or None,
                initial=existing_records,
                unit=" rec",
                desc=f"  r/{subreddit} {file_type[:4]}",
                position=tqdm_position,
                leave=True,
            ) as pbar:
                empty_streak = 0
                consecutive_errors = 0

                while True:
                    # Check stop signal before each API request
                    if stop_event and stop_event.is_set():
                        break

                    try:
                        resp = session.get(
                            url,
                            params={
                                "subreddit": subreddit,
                                "limit": "auto",
                                "sort": "asc",
                                "after": str(cursor),
                                "meta-app": "download-tool",
                            },
                            timeout=REQUEST_TIMEOUT,
                        )
                        resp.raise_for_status()
                        records = resp.json().get("data", [])
                    except (requests.RequestException, ValueError) as e:
                        consecutive_errors += 1
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            tqdm.write(
                                f"  Giving up r/{subreddit} {file_type}: "
                                f"{consecutive_errors} consecutive errors. "
                                f"Last: {e}"
                            )
                            break
                        wait = min(
                            RETRY_BACKOFF * (2 ** min(consecutive_errors, 6)),
                            120,
                        )
                        tqdm.write(
                            f"  Retry {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS} "
                            f"r/{subreddit} {file_type}: {e} (wait {wait}s)"
                        )
                        time.sleep(wait)
                        continue

                    # Reset error counter on successful request
                    consecutive_errors = 0

                    if not records:
                        empty_streak += 1
                        if empty_streak >= EMPTY_RESPONSE_RETRIES:
                            completed = True
                            break
                        # Transient empty response — wait and retry
                        time.sleep(EMPTY_RESPONSE_DELAY)
                        continue

                    # Got data — reset empty streak
                    empty_streak = 0

                    for record in records:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")

                    f.flush()
                    batch_size = len(records)
                    new_records += batch_size
                    pbar.update(batch_size)

                    # Advance cursor
                    last_created = records[-1].get("created_utc")
                    if last_created is None:
                        # Fallback: skip ahead 1 second
                        cursor += 1000
                    else:
                        new_cursor = int(last_created * 1000)
                        if new_cursor == cursor:
                            new_cursor += 1000
                        cursor = new_cursor

                    # Save cursor for resume
                    cursor_file.write_text(str(cursor))

        total_records = existing_records + new_records

        if completed:
            tqdm.write(
                f"  Done r/{subreddit} {file_type}: "
                f"{total_records:,} records. Compressing..."
            )
            # Compress jsonl -> zst
            cctx = zstd.ZstdCompressor()
            with open(tmp_jsonl, "rb") as f_in, open(dest_path, "wb") as f_out:
                cctx.copy_stream(f_in, f_out)

            # Clean up temp files on success
            tmp_jsonl.unlink(missing_ok=True)
            cursor_file.unlink(missing_ok=True)
            tqdm.write(
                f"  Saved {dest_path.name} ({dest_path.stat().st_size / 1e6:.1f} MB)"
            )
        elif stop_event and stop_event.is_set():
            tqdm.write(
                f"  Paused r/{subreddit} {file_type}: "
                f"{total_records:,} records saved. Re-run to resume."
            )
        else:
            tqdm.write(
                f"  Stopped r/{subreddit} {file_type}: "
                f"{total_records:,} records saved. Re-run to resume."
            )

        return completed

    except requests.RequestException as e:
        tqdm.write(f"  Error r/{subreddit} {file_type}: {e} (re-run to resume)")
        return False
    except KeyboardInterrupt:
        tqdm.write(f"  Interrupted r/{subreddit} {file_type}. Re-run to resume.")
        return False


def needs_download(subreddit: str, file_type: str) -> bool:
    """Check if a file needs downloading (missing, empty, or partial)."""
    dest_path = RAW_DATA_DIR / subreddit / f"{file_type}.zst"
    if not dest_path.exists() or dest_path.stat().st_size == 0:
        return True
    cursor_file = dest_path.with_suffix(".cursor")
    tmp_jsonl = dest_path.with_suffix(".jsonl.tmp")
    return cursor_file.exists() or tmp_jsonl.exists()


def main():
    parser = argparse.ArgumentParser(
        description="Download Reddit finance subreddit data from Arctic Shift API"
    )
    parser.add_argument(
        "--subreddit",
        type=str,
        choices=SUBREDDITS,
        default=None,
        help="Download a specific subreddit (default: all)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel downloads (default: 4)",
    )

    args = parser.parse_args()

    subreddits = [args.subreddit] if args.subreddit else SUBREDDITS

    print("Reddit Data Ingestion")
    print("=" * 60)
    print(f"Subreddits: {', '.join(subreddits)}")
    print(f"Output directory: {RAW_DATA_DIR}")
    print(f"Workers: {args.workers}")
    print()

    # Build task list: (subreddit, file_type) pairs that need downloading
    tasks = []
    for sub in subreddits:
        (RAW_DATA_DIR / sub).mkdir(parents=True, exist_ok=True)
        for file_type in ("submissions", "comments"):
            if needs_download(sub, file_type):
                tasks.append((sub, file_type))
            else:
                print(f"  Skipping r/{sub} {file_type} (already exists)")

    if not tasks:
        print("\nAll files already downloaded!")
        return

    print(f"\nDownloading {len(tasks)} files...\n")

    stop_event = threading.Event()
    failed = []

    if args.workers <= 1 or len(tasks) == 1:
        # Sequential mode
        session = _make_session()

        for sub, file_type in tasks:
            if stop_event.is_set():
                break
            dest_path = RAW_DATA_DIR / sub / f"{file_type}.zst"
            try:
                if not download_data(
                    sub,
                    file_type,
                    dest_path,
                    session,
                    tqdm_position=0,
                    stop_event=stop_event,
                ):
                    failed.append(f"{sub}/{file_type}")
            except KeyboardInterrupt:
                stop_event.set()
                failed.append(f"{sub}/{file_type}")
                break
    else:
        # Parallel mode — each thread gets its own session for thread safety
        def _run_task(task_info):
            sub, file_type, pos = task_info
            dest_path = RAW_DATA_DIR / sub / f"{file_type}.zst"
            thread_session = _make_session()
            try:
                ok = download_data(
                    sub,
                    file_type,
                    dest_path,
                    thread_session,
                    tqdm_position=pos,
                    stop_event=stop_event,
                )
            except Exception as e:
                tqdm.write(f"  Unexpected error r/{sub} {file_type}: {e}")
                ok = False
            return (sub, file_type, ok)

        task_args = [(sub, ft, i) for i, (sub, ft) in enumerate(tasks)]

        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = {executor.submit(_run_task, t): t for t in task_args}
            try:
                for future in as_completed(futures):
                    try:
                        sub, file_type, ok = future.result()
                        if not ok:
                            failed.append(f"{sub}/{file_type}")
                    except Exception as e:
                        sub, ft, _ = futures[future]
                        tqdm.write(f"  Task failed r/{sub} {ft}: {e}")
                        failed.append(f"{sub}/{ft}")
            except KeyboardInterrupt:
                print("\n\nInterrupted! Saving progress...")
                stop_event.set()
                # Threads check stop_event and exit after current batch.
                # The 'with' block waits for them to finish gracefully.

    # Summary
    print()
    print("=" * 60)
    if stop_event.is_set():
        print("Download interrupted. Progress has been saved.")
        print("Re-run to resume.")
    elif failed:
        print(f"Failed: {', '.join(failed)}")
        print("Re-run to resume failed downloads.")
        sys.exit(1)
    else:
        print("Reddit ingestion complete!")
        print(f"Data location: {RAW_DATA_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
