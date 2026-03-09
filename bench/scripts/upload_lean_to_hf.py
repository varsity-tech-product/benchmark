#!/usr/bin/env python3
"""Upload LEAN-format market data and flat universe.json to HuggingFace.

Uploads the LEAN data directory tree and the flat universe.json to the
quant-tutor-bench HuggingFace dataset repo, matching the layout that
data_manager.py expects when downloading.

HF repo layout produced:
    lean/crypto/binance/daily/*.zip
    lean/crypto/binance/hour/*.zip
    lean/crypto/binance/4hour/*.zip
    lean/crypto/binance/5minute/**/*.zip
    lean/crypto/binance/minute/**/*.zip
    lean/universe.json
    raw/i-series/universe.json

Usage:
    python upload_lean_to_hf.py
    python upload_lean_to_hf.py --lean-dir bench/data/lean --universe bench/data/lean_universe.json
    python upload_lean_to_hf.py --repo-id myorg/myrepo --dry-run
"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_LEAN_DIR = Path(__file__).resolve().parent.parent / "data" / "lean"
DEFAULT_UNIVERSE = Path(__file__).resolve().parent.parent / "data" / "lean_universe.json"
DEFAULT_REPO_ID = "quant-tutor-bench/quant-tutor-bench-data"


def upload(
    lean_dir: Path,
    flat_universe: Path,
    repo_id: str = DEFAULT_REPO_ID,
    dry_run: bool = False,
) -> None:
    """Upload LEAN data and flat universe to HuggingFace."""
    from huggingface_hub import HfApi

    if not lean_dir.is_dir():
        raise FileNotFoundError(f"LEAN data directory not found: {lean_dir}")
    if not flat_universe.is_file():
        raise FileNotFoundError(f"Flat universe file not found: {flat_universe}")

    # Count files for summary
    zip_files = list(lean_dir.rglob("*.zip"))
    print(f"LEAN directory: {lean_dir} ({len(zip_files)} zip files)")
    print(f"Flat universe:  {flat_universe}")
    print(f"HF repo:        {repo_id}")

    if dry_run:
        print("\nDRY RUN — no files uploaded.")
        return

    api = HfApi()
    api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)

    print("\nUploading LEAN data folder...")
    api.upload_folder(
        folder_path=str(lean_dir),
        path_in_repo="lean",
        repo_id=repo_id,
        repo_type="dataset",
    )

    print("Uploading flat universe.json to lean/universe.json...")
    api.upload_file(
        path_or_fileobj=str(flat_universe),
        path_in_repo="lean/universe.json",
        repo_id=repo_id,
        repo_type="dataset",
    )

    print("Uploading flat universe.json to raw/i-series/universe.json...")
    api.upload_file(
        path_or_fileobj=str(flat_universe),
        path_in_repo="raw/i-series/universe.json",
        repo_id=repo_id,
        repo_type="dataset",
    )

    print("\nUpload complete.")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--lean-dir", type=Path, default=DEFAULT_LEAN_DIR,
        help="Path to LEAN data directory (default: %(default)s)",
    )
    parser.add_argument(
        "--universe", type=Path, default=DEFAULT_UNIVERSE,
        help="Path to flat universe JSON file (default: %(default)s)",
    )
    parser.add_argument(
        "--repo-id", default=DEFAULT_REPO_ID,
        help="HuggingFace dataset repo ID (default: %(default)s)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would be uploaded without actually uploading.",
    )
    args = parser.parse_args()

    try:
        upload(
            lean_dir=args.lean_dir,
            flat_universe=args.universe,
            repo_id=args.repo_id,
            dry_run=args.dry_run,
        )
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
