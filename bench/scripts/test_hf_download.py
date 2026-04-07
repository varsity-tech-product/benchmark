#!/usr/bin/env python3
"""Test HuggingFace download by fetching into a temp directory.

Does NOT touch local bench/data/ — downloads to /tmp/hf_download_test/.
Verifies all 4 categories: docs, lean, normal, reference.

Usage:
    python scripts/test_hf_download.py
    python scripts/test_hf_download.py --revision <commit_hash>
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--revision", help="HF dataset revision to test")
    parser.add_argument(
        "--keep", action="store_true", help="Don't delete temp dir after test"
    )
    args = parser.parse_args()

    test_dir = Path("/tmp/hf_download_test")
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_cache = test_dir / "hf_cache"
    test_cache.mkdir(parents=True)

    from scripts.data_manager import HF_REPO_ID, ensure_data

    revision = args.revision
    print(f"HF repo: {HF_REPO_ID}")
    print(f"Revision: {revision or 'latest'}")
    print(f"Test cache dir: {test_cache}")
    print()

    # Test 1: lean series (downloads I.tar.gz + docs + reference)
    print("=" * 60)
    print("TEST 1: ensure_data(series='lean')")
    print("=" * 60)
    try:
        paths = ensure_data(
            series="lean",
            cache_dir=str(test_cache),
            revision=revision,
        )
        print(f"  docs:       {paths.docs}")
        print(f"  lean_data:  {paths.lean_data}")
        print(f"  search:     {paths.data_search_dirs}")
        print(f"  student:    {paths.student_code}")

        # Verify key files
        checks = [
            ("docs dir", Path(paths.docs).is_dir()),
            (
                "lean I dir",
                Path(paths.lean_data).is_dir() if paths.lean_data else False,
            ),
            (
                "I/universe.json",
                (
                    (Path(paths.lean_data) / "universe.json").is_file()
                    if paths.lean_data
                    else False
                ),
            ),
        ]
        for name, ok in checks:
            print(f"  {'✅' if ok else '❌'} {name}")
        print()
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        print()

    # Test 2: normal series (downloads BDS + X + A + docs + reference)
    print("=" * 60)
    print("TEST 2: ensure_data(series='normal')")
    print("=" * 60)
    try:
        paths2 = ensure_data(
            series="normal",
            cache_dir=str(test_cache),
            revision=revision,
        )
        print(f"  docs:     {paths2.docs}")
        print(f"  search:   {paths2.data_search_dirs}")
        print(f"  student:  {paths2.student_code}")

        checks2 = [
            ("docs dir", Path(paths2.docs).is_dir()),
            ("BDEX dir", any(Path(d).is_dir() for d in paths2.data_search_dirs)),
        ]
        for name, ok in checks2:
            print(f"  {'✅' if ok else '❌'} {name}")
        print()
    except Exception as e:
        print(f"  ❌ FAILED: {e}")
        print()

    # Test 3: reference data
    print("=" * 60)
    print("TEST 3: reference data")
    print("=" * 60)
    ref_dir = test_cache.parent / "reference"
    if ref_dir.is_dir():
        ref_files = list(ref_dir.glob("*.json"))
        print(f"  Reference dir: {ref_dir}")
        print(f"  JSON files: {len(ref_files)}")
        has_i01 = (ref_dir / "I01_reference_trades.json").is_file()
        has_e02 = (ref_dir / "E02_reference_trades.json").is_file()
        has_x07 = (ref_dir / "X07_reference_trades.json").is_file()
        print(f"  {'✅' if has_i01 else '❌'} I01_reference_trades.json")
        print(f"  {'✅' if has_e02 else '❌'} E02_reference_trades.json")
        print(f"  {'✅' if has_x07 else '❌'} X07_reference_trades.json")
    else:
        print(f"  ❌ Reference dir not found: {ref_dir}")
    print()

    # Summary
    print("=" * 60)
    test_size = sum(f.stat().st_size for f in test_dir.rglob("*") if f.is_file())
    print(f"Total downloaded: {test_size / (1024*1024):.1f} MB")
    print(f"Test dir: {test_dir}")

    if not args.keep:
        shutil.rmtree(test_dir)
        print("Test dir cleaned up.")
    else:
        print("Test dir kept (--keep).")


if __name__ == "__main__":
    main()
