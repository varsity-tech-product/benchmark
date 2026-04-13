#!/usr/bin/env python3
"""Upload benchmark data artifacts to HuggingFace.

Uploads five categories of data consumed by the benchmark:
  1. 12-col custom market data → custom_binance_12col.tar.gz
  2. Normal data               → docs/**, BDS/**, X/**, A/**  (uploaded as folder)
  3. Reference results         → reference.tar.gz  (eval reference JSONs)
  4. Universe metadata         → raw/i-series/universe*.json
  5. Optional deletion         → remove legacy I.tar.gz from the dataset repo

HF repo layout produced::

    custom_binance_12col.tar.gz                 # 12-col custom market data
    reference.tar.gz                            # eval reference results
    docs/                                       # shared reference docs
    BDS/                                        # normal CSV data
    X/                                          # student code / debug data
    A/                                          # adversarial data
    raw/i-series/universe.json
    raw/i-series/universe_structured.json
    raw/i-series/benchmark_universe_coverage.json

Usage::

    python upload_lean_to_hf.py --dry-run       # preview
    python upload_lean_to_hf.py                 # upload all
    python upload_lean_to_hf.py --only custom   # upload only 12-col archive
    python upload_lean_to_hf.py --only reference  # upload only reference
    python upload_lean_to_hf.py --delete-legacy-i-tar
"""

from __future__ import annotations

import argparse
import tarfile
import tempfile
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _BENCH_ROOT / "data"

DEFAULT_CUSTOM_DATA_DIR = _DATA_DIR / "custom"
DEFAULT_CUSTOM_ARCHIVE_NAME = "custom_binance_12col.tar.gz"

DEFAULT_DOCS_DIR = _DATA_DIR / "hf_cache" / "docs"
DEFAULT_NORMAL_DIR = _DATA_DIR / "hf_cache" / "normal"
DEFAULT_REFERENCE_DIR = _DATA_DIR / "reference"

DEFAULT_UNIVERSE = _DATA_DIR / "lean_universe.json"
DEFAULT_STRUCTURED_UNIVERSE = _DATA_DIR / "universe.json"
DEFAULT_COVERAGE_REPORT = _DATA_DIR / "benchmark_universe_coverage.json"

DEFAULT_REPO_ID = "Varsity-Tech/quant-tutor-bench-data"


# ---------------------------------------------------------------------------
# Archive builders
# ---------------------------------------------------------------------------


def _build_custom_archive(custom_dir: Path, output_path: Path) -> Path:
    """Create custom_binance_12col.tar.gz with top-level binance/ members."""
    binance_dir = custom_dir / "binance"
    if not binance_dir.is_dir():
        raise FileNotFoundError(f"12-col custom data dir not found: {binance_dir}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w:gz") as tf:
        for path in sorted(binance_dir.rglob("*")):
            if not path.is_file():
                continue
            tf.add(path, arcname=str(Path("binance") / path.relative_to(binance_dir)))
    return output_path


def _build_reference_archive(ref_dir: Path, output_path: Path) -> Path:
    """Create reference.tar.gz from bench/data/reference/."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w:gz") as tf:
        for path in sorted(ref_dir.rglob("*")):
            if not path.is_file():
                continue
            tf.add(path, arcname=str(path.relative_to(ref_dir)))
    return output_path


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


def upload(
    repo_id: str = DEFAULT_REPO_ID,
    dry_run: bool = False,
    only: str | None = None,
    custom_dir: Path = DEFAULT_CUSTOM_DATA_DIR,
    delete_legacy_i_tar: bool = False,
    custom_archive_name: str = DEFAULT_CUSTOM_ARCHIVE_NAME,
) -> str | None:
    """Upload all benchmark data to HuggingFace.

    Args:
        repo_id: HuggingFace dataset repo ID.
        dry_run: If True, show what would be uploaded without uploading.
        only: If set, upload only this category ("custom", "normal", "docs",
              "reference", "universe"). None = upload all.
        custom_dir: Local 12-col data directory containing ``binance/``.
        delete_legacy_i_tar: If True, delete the legacy ``I.tar.gz`` artifact.
        custom_archive_name: Target filename for the 12-col archive in HF.
    """
    from huggingface_hub import HfApi

    categories = (
        {only} if only else {"custom", "normal", "docs", "reference", "universe"}
    )

    with tempfile.TemporaryDirectory(prefix="hf_upload_") as tmpdir:
        tmpdir = Path(tmpdir)
        uploads: list[tuple[Path | str, str]] = []

        # --- 12-col custom archive ---
        if "custom" in categories:
            archive = _build_custom_archive(custom_dir, tmpdir / custom_archive_name)
            size_mb = archive.stat().st_size / (1024 * 1024)
            print(f"[custom] {custom_archive_name}: {size_mb:.1f} MiB")
            uploads.append((archive, custom_archive_name))

        # --- Reference archive ---
        if "reference" in categories:
            if not DEFAULT_REFERENCE_DIR.is_dir():
                raise FileNotFoundError(
                    f"Reference dir not found: {DEFAULT_REFERENCE_DIR}"
                )
            ref_count = sum(1 for _ in DEFAULT_REFERENCE_DIR.rglob("*.json"))
            archive = _build_reference_archive(
                DEFAULT_REFERENCE_DIR, tmpdir / "reference.tar.gz"
            )
            size_mb = archive.stat().st_size / (1024 * 1024)
            print(f"[reference] reference.tar.gz: {ref_count} files, {size_mb:.1f} MiB")
            uploads.append((archive, "reference.tar.gz"))

        # --- Docs (folder upload) ---
        if "docs" in categories:
            if not DEFAULT_DOCS_DIR.is_dir():
                raise FileNotFoundError(f"Docs dir not found: {DEFAULT_DOCS_DIR}")
            doc_count = sum(1 for _ in DEFAULT_DOCS_DIR.iterdir() if _.is_file())
            print(f"[docs] {doc_count} files")
            for f in sorted(DEFAULT_DOCS_DIR.iterdir()):
                if f.is_file():
                    uploads.append((f, f"docs/{f.name}"))

        # --- Normal data (folder upload) ---
        if "normal" in categories:
            if not DEFAULT_NORMAL_DIR.is_dir():
                print("[normal] SKIP — directory not found")
            else:
                for subdir_name in ("BDEX", "BDS", "X", "A"):
                    subdir = DEFAULT_NORMAL_DIR / subdir_name
                    if not subdir.is_dir():
                        continue
                    # HF expects BDS not BDEX
                    hf_prefix = "BDS" if subdir_name == "BDEX" else subdir_name
                    file_count = sum(1 for _ in subdir.rglob("*") if _.is_file())
                    print(f"[normal] {hf_prefix}/: {file_count} files")
                    for f in sorted(subdir.rglob("*")):
                        if f.is_file():
                            uploads.append((f, f"{hf_prefix}/{f.relative_to(subdir)}"))

        # --- Universe metadata ---
        if "universe" in categories:
            for local, remote in [
                (DEFAULT_UNIVERSE, "raw/i-series/universe.json"),
                (DEFAULT_STRUCTURED_UNIVERSE, "raw/i-series/universe_structured.json"),
                (
                    DEFAULT_COVERAGE_REPORT,
                    "raw/i-series/benchmark_universe_coverage.json",
                ),
            ]:
                if local.is_file():
                    uploads.append((local, remote))
                    print(f"[universe] {remote}")
                else:
                    print(f"[universe] SKIP — {local} not found")

        # --- Summary ---
        print(f"\nTotal uploads: {len(uploads)} files")
        print(f"HF repo: {repo_id}")
        if delete_legacy_i_tar:
            print("Legacy delete: I.tar.gz")

        if dry_run:
            print("\nDRY RUN — listing all files:")
            for local, remote in uploads:
                size = Path(local).stat().st_size
                print(f"  {remote}  ({size:,} bytes)")
            if delete_legacy_i_tar:
                print("  DELETE I.tar.gz")
            return None

        # --- Execute upload ---
        api = HfApi()
        api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)

        for i, (local_path, remote_path) in enumerate(uploads, 1):
            print(f"  [{i}/{len(uploads)}] {remote_path}")
            api.upload_file(
                path_or_fileobj=str(local_path),
                path_in_repo=remote_path,
                repo_id=repo_id,
                repo_type="dataset",
                commit_message=f"Update benchmark data: {remote_path}",
            )

        if delete_legacy_i_tar:
            print("  [delete] I.tar.gz")
            api.delete_file(
                path_in_repo="I.tar.gz",
                repo_id=repo_id,
                repo_type="dataset",
                commit_message="Remove legacy I.tar.gz after 12-col-only migration",
            )

        latest = api.list_repo_commits(repo_id, repo_type="dataset")[0].commit_id
        print(f"\nUpload complete. Latest revision: {latest}")
        return latest


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo-id",
        default=DEFAULT_REPO_ID,
        help="HuggingFace dataset repo ID (default: %(default)s)",
    )
    parser.add_argument(
        "--only",
        choices=["custom", "normal", "docs", "reference", "universe"],
        help="Upload only this category (default: upload all).",
    )
    parser.add_argument(
        "--custom-dir",
        type=Path,
        default=DEFAULT_CUSTOM_DATA_DIR,
        help="Path to 12-col custom data root containing binance/ (default: %(default)s).",
    )
    parser.add_argument(
        "--custom-archive-name",
        default=DEFAULT_CUSTOM_ARCHIVE_NAME,
        help="Destination filename for the 12-col archive in HF (default: %(default)s).",
    )
    parser.add_argument(
        "--delete-legacy-i-tar",
        action="store_true",
        help="Delete legacy I.tar.gz from the dataset repo after upload.",
    )
    parser.add_argument(
        "--lean-dir",
        default=None,
        help="Deprecated compatibility flag. Ignored by the 12-col-only uploader.",
    )
    parser.add_argument(
        "--universe",
        default=None,
        help="Deprecated compatibility flag. Ignored by the 12-col-only uploader.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded without actually uploading.",
    )
    args = parser.parse_args()

    try:
        revision = upload(
            repo_id=args.repo_id,
            dry_run=args.dry_run,
            only=args.only,
            custom_dir=args.custom_dir,
            delete_legacy_i_tar=args.delete_legacy_i_tar,
            custom_archive_name=args.custom_archive_name,
        )
        if revision:
            print(f"REVISION={revision}")
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
