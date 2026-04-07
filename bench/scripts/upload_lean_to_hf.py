#!/usr/bin/env python3
"""Upload all benchmark data to HuggingFace.

Uploads four categories of data consumed by ``data_manager.py``:
  1. LEAN market data  → I.tar.gz  (I/E/X series crypto futures)
  2. Normal data       → docs/**, BDS/**, X/**, A/**  (uploaded as folder)
  3. Reference results → reference.tar.gz  (eval reference JSONs)
  4. Universe metadata → raw/i-series/universe*.json

HF repo layout produced::

    I.tar.gz                                    # LEAN market data
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
    python upload_lean_to_hf.py --only lean     # upload only LEAN archive
    python upload_lean_to_hf.py --only reference  # upload only reference
"""

from __future__ import annotations

import argparse
import tarfile
import tempfile
from pathlib import Path

_BENCH_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = _BENCH_ROOT / "data"

DEFAULT_LEAN_DIR = _DATA_DIR / "hf_cache" / "lean" / "I"
DEFAULT_LEAN_E_DIR = _DATA_DIR / "hf_cache" / "lean" / "E"
DEFAULT_LEAN_X_DIR = _DATA_DIR / "hf_cache" / "lean" / "X"
DEFAULT_I05_PAIRS = (
    _BENCH_ROOT / "reference" / "Implementation" / "result" / "I05_candidate_pairs.json"
)

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


def _build_lean_archive(
    lean_dir: Path,
    output_path: Path,
    lean_e_dir: Path = DEFAULT_LEAN_E_DIR,
    lean_x_dir: Path = DEFAULT_LEAN_X_DIR,
    i05_pairs_path: Path = DEFAULT_I05_PAIRS,
) -> Path:
    """Create I.tar.gz with top-level I/E/X members."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w:gz") as tf:
        for path in sorted(lean_dir.rglob("*")):
            if not path.is_file():
                continue
            tf.add(path, arcname=str(Path("I") / path.relative_to(lean_dir)))

        if i05_pairs_path.is_file():
            tf.add(i05_pairs_path, arcname="I/I05_candidate_pairs.json")

        for extra_dir, top_level in ((lean_e_dir, "E"), (lean_x_dir, "X")):
            if not extra_dir.is_dir():
                continue
            for path in sorted(extra_dir.rglob("*")):
                if not path.is_file():
                    continue
                tf.add(path, arcname=str(Path(top_level) / path.relative_to(extra_dir)))
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
) -> str | None:
    """Upload all benchmark data to HuggingFace.

    Args:
        repo_id: HuggingFace dataset repo ID.
        dry_run: If True, show what would be uploaded without uploading.
        only: If set, upload only this category ("lean", "normal", "docs",
              "reference", "universe"). None = upload all.
    """
    from huggingface_hub import HfApi

    categories = {only} if only else {"lean", "normal", "docs", "reference", "universe"}

    with tempfile.TemporaryDirectory(prefix="hf_upload_") as tmpdir:
        tmpdir = Path(tmpdir)
        uploads: list[tuple[Path | str, str]] = []

        # --- LEAN archive ---
        if "lean" in categories:
            if not DEFAULT_LEAN_DIR.is_dir():
                raise FileNotFoundError(f"LEAN dir not found: {DEFAULT_LEAN_DIR}")
            archive = _build_lean_archive(DEFAULT_LEAN_DIR, tmpdir / "I.tar.gz")
            size_mb = archive.stat().st_size / (1024 * 1024)
            print(f"[lean] I.tar.gz: {size_mb:.1f} MiB")
            uploads.append((archive, "I.tar.gz"))

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

        if dry_run:
            print("\nDRY RUN — listing all files:")
            for local, remote in uploads:
                size = Path(local).stat().st_size
                print(f"  {remote}  ({size:,} bytes)")
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
        choices=["lean", "normal", "docs", "reference", "universe"],
        help="Upload only this category (default: upload all).",
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
        )
        if revision:
            print(f"REVISION={revision}")
    except Exception as e:
        print(f"ERROR: {e}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
