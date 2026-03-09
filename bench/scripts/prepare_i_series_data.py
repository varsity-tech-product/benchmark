#!/usr/bin/env python3
"""End-to-end pipeline for I-series data: download, convert, generate flat universe, upload, verify.

Orchestrates the full data pipeline:
  Phase 1: Download raw Binance klines (reuses download_binance_full_universe)
  Phase 2: Convert to LEAN format (reuses convert_binance_to_lean)
  Phase 3: Generate flat universe.json for C# algorithms
  Phase 4: Upload to HuggingFace (optional --upload)
  Phase 5: Verify data_manager roundtrip (optional --verify)

Usage:
    python prepare_i_series_data.py --workers 16
    python prepare_i_series_data.py --skip-download --skip-convert --upload
    python prepare_i_series_data.py --skip-download --skip-convert --verify
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = BENCH_ROOT / "scripts"
DATA_DIR = BENCH_ROOT / "data"

RAW_DIR = DATA_DIR / "raw" / "i-series"
LEAN_DIR = DATA_DIR / "lean"
UNIVERSE_PATH = DATA_DIR / "universe.json"
FLAT_UNIVERSE_PATH = DATA_DIR / "lean_universe.json"

DEFAULT_REPO_ID = "Varsity-Tech/quant-tutor-bench-data"


def phase_download(workers: int, include_funding: bool, universe: Path | None = None) -> bool:
    """Phase 1: Download raw Binance data."""
    print("\n" + "=" * 60)
    print("Phase 1: Download raw Binance klines")
    print("=" * 60)

    uni = universe or UNIVERSE_PATH
    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "download_binance_full_universe.py"),
        "--tier", "all",
        "--universe", str(uni),
        "--output-dir", str(RAW_DIR),
        "--workers", str(workers),
    ]
    if include_funding:
        cmd.append("--include-funding")

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"ERROR: Download failed with exit code {result.returncode}")
        return False

    print("Phase 1 complete.")
    return True


def phase_convert() -> bool:
    """Phase 2: Convert raw CSVs to LEAN format."""
    print("\n" + "=" * 60)
    print("Phase 2: Convert to LEAN format")
    print("=" * 60)

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "convert_binance_to_lean.py"),
        "--input-dir", str(RAW_DIR),
        "--output-dir", str(LEAN_DIR),
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"ERROR: Conversion failed with exit code {result.returncode}")
        return False

    print("Phase 2 complete.")
    return True


def phase_flat_universe(universe: Path | None = None) -> bool:
    """Phase 3: Generate flat universe.json and copy into LEAN data dir."""
    print("\n" + "=" * 60)
    print("Phase 3: Generate flat universe.json")
    print("=" * 60)

    uni = universe or UNIVERSE_PATH
    if not uni.exists():
        print(f"ERROR: Structured universe.json not found: {uni}")
        return False

    # Import and run inline to avoid subprocess overhead
    sys.path.insert(0, str(SCRIPTS_DIR))
    from generate_flat_universe import generate_flat_universe

    import json
    symbols = generate_flat_universe(uni)

    # Write to default location
    FLAT_UNIVERSE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(FLAT_UNIVERSE_PATH, "w") as f:
        json.dump(symbols, f, indent=2)
    print(f"Wrote {len(symbols)} symbols to {FLAT_UNIVERSE_PATH}")

    # Also copy into LEAN data directory so algorithms find it
    lean_universe = LEAN_DIR / "universe.json"
    LEAN_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(str(FLAT_UNIVERSE_PATH), str(lean_universe))
    print(f"Copied flat universe to {lean_universe}")

    print("Phase 3 complete.")
    return True


def phase_upload(repo_id: str) -> bool:
    """Phase 4: Upload LEAN data to HuggingFace."""
    print("\n" + "=" * 60)
    print("Phase 4: Upload to HuggingFace")
    print("=" * 60)

    cmd = [
        sys.executable,
        str(SCRIPTS_DIR / "upload_lean_to_hf.py"),
        "--lean-dir", str(LEAN_DIR),
        "--universe", str(FLAT_UNIVERSE_PATH),
        "--repo-id", repo_id,
    ]

    print(f"Running: {' '.join(cmd)}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"ERROR: Upload failed with exit code {result.returncode}")
        return False

    print("Phase 4 complete.")
    return True


def phase_verify() -> bool:
    """Phase 5: Verify data_manager roundtrip."""
    print("\n" + "=" * 60)
    print("Phase 5: Verify data_manager roundtrip")
    print("=" * 60)

    try:
        sys.path.insert(0, str(BENCH_ROOT))
        from scripts.data_manager import ensure_data
        paths = ensure_data(series="i")
        print(f"  lean_data: {paths.lean_data}")
        print(f"  universe:  {paths.universe}")

        # Check lean data exists and has content
        lean_path = Path(paths.lean_data)
        if not lean_path.is_dir():
            print(f"ERROR: LEAN data dir does not exist: {lean_path}")
            return False

        zip_count = len(list(lean_path.rglob("*.zip")))
        print(f"  zip files: {zip_count}")

        # Check universe.json inside LEAN dir
        lean_universe = lean_path / "universe.json"
        if lean_universe.exists():
            import json
            with open(lean_universe) as f:
                symbols = json.load(f)
            print(f"  lean/universe.json: {len(symbols)} symbols")
        else:
            print("  WARNING: lean/universe.json not found")

        if zip_count == 0:
            print("ERROR: No zip files found in LEAN data directory")
            return False

        print("Phase 5 complete — verification passed.")
        return True

    except Exception as e:
        print(f"ERROR: Verification failed: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Skip Phase 1 (download).",
    )
    parser.add_argument(
        "--skip-convert", action="store_true",
        help="Skip Phase 2 (convert).",
    )
    parser.add_argument(
        "--include-funding", action="store_true",
        help="Include funding rate downloads in Phase 1.",
    )
    parser.add_argument(
        "--upload", action="store_true",
        help="Run Phase 4 (upload to HuggingFace).",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="Run Phase 5 (verify data_manager roundtrip).",
    )
    parser.add_argument(
        "--repo-id", default=DEFAULT_REPO_ID,
        help="HuggingFace repo ID for upload (default: %(default)s).",
    )
    parser.add_argument(
        "--universe", type=Path, default=None,
        help="Path to universe.json (default: bench/data/universe.json).",
    )
    parser.add_argument(
        "--workers", type=int, default=8,
        help="Number of parallel download workers (default: 8).",
    )
    args = parser.parse_args()

    phases_run = 0
    phases_ok = 0

    # Phase 1: Download
    if not args.skip_download:
        phases_run += 1
        if phase_download(args.workers, args.include_funding, args.universe):
            phases_ok += 1
        else:
            print("\nAborting: download failed.")
            return 1

    # Phase 2: Convert
    if not args.skip_convert:
        phases_run += 1
        if phase_convert():
            phases_ok += 1
        else:
            print("\nAborting: conversion failed.")
            return 1

    # Phase 3: Flat universe (always runs)
    phases_run += 1
    if phase_flat_universe(args.universe):
        phases_ok += 1
    else:
        print("\nAborting: flat universe generation failed.")
        return 1

    # Phase 4: Upload (optional)
    if args.upload:
        phases_run += 1
        if phase_upload(args.repo_id):
            phases_ok += 1
        else:
            return 1

    # Phase 5: Verify (optional)
    if args.verify:
        phases_run += 1
        if phase_verify():
            phases_ok += 1
        else:
            return 1

    print(f"\nAll done: {phases_ok}/{phases_run} phases succeeded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
