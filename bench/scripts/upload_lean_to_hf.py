#!/usr/bin/env python3
"""Upload the canonical LEAN benchmark archive and frozen metadata to HuggingFace.

Uploads the canonical `I.tar.gz` archive consumed by `data_manager.py`, plus
the frozen universe metadata used to explain the benchmark contract.

HF repo layout produced:
    I.tar.gz
    raw/i-series/universe.json
    raw/i-series/universe_structured.json
    raw/i-series/benchmark_universe_coverage.json

This dataset revision intentionally publishes trade-bar data only.
Quote and margin-interest sidecars are not part of the current contract.

Usage:
    python upload_lean_to_hf.py
    python upload_lean_to_hf.py --lean-dir bench/data/lean --universe bench/data/lean_universe.json
    python upload_lean_to_hf.py --repo-id myorg/myrepo --dry-run
"""

from __future__ import annotations

import argparse
import tarfile
import tempfile
from pathlib import Path

DEFAULT_LEAN_DIR = Path(__file__).resolve().parent.parent / "data" / "lean"
DEFAULT_UNIVERSE = Path(__file__).resolve().parent.parent / "data" / "lean_universe.json"
DEFAULT_STRUCTURED_UNIVERSE = Path(__file__).resolve().parent.parent / "data" / "universe.json"
DEFAULT_COVERAGE_REPORT = Path(__file__).resolve().parent.parent / "data" / "benchmark_universe_coverage.json"
DEFAULT_REPO_ID = "Varsity-Tech/quant-tutor-bench-data"


def build_archive(lean_dir: Path, output_path: Path) -> Path:
    """Create the canonical I.tar.gz archive with top-level I/ members."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output_path, "w:gz") as tf:
        for path in sorted(lean_dir.rglob("*")):
            if not path.is_file():
                continue
            arcname = Path("I") / path.relative_to(lean_dir)
            tf.add(path, arcname=str(arcname))
    return output_path


def upload(
    lean_dir: Path,
    flat_universe: Path,
    structured_universe: Path,
    coverage_report: Path,
    repo_id: str = DEFAULT_REPO_ID,
    dry_run: bool = False,
) -> str | None:
    """Upload the canonical archive and frozen metadata to HuggingFace."""

    from huggingface_hub import HfApi

    if not lean_dir.is_dir():
        raise FileNotFoundError(f"LEAN data directory not found: {lean_dir}")
    if not flat_universe.is_file():
        raise FileNotFoundError(f"Flat universe file not found: {flat_universe}")
    if not structured_universe.is_file():
        raise FileNotFoundError(f"Structured universe file not found: {structured_universe}")
    if not coverage_report.is_file():
        raise FileNotFoundError(f"Coverage report not found: {coverage_report}")

    # Count files for summary
    zip_files = list(lean_dir.rglob("*.zip"))
    all_files = list(lean_dir.rglob("*"))
    file_count = sum(1 for f in all_files if f.is_file())
    print(f"LEAN directory: {lean_dir} ({len(zip_files)} zip files, {file_count} total files)")
    print(f"Flat universe:  {flat_universe}")
    print(f"Structured universe: {structured_universe}")
    print(f"Coverage report: {coverage_report}")
    print(f"HF repo:        {repo_id}")

    with tempfile.TemporaryDirectory(prefix="hf_i_archive_") as tmpdir:
        archive_path = build_archive(lean_dir, Path(tmpdir) / "I.tar.gz")
        archive_size_mb = archive_path.stat().st_size / (1024 * 1024)
        print(f"Archive:        {archive_path} ({archive_size_mb:.1f} MiB)")

        if dry_run:
            print("\nDRY RUN — no files uploaded.")
            return None

        api = HfApi()
        api.create_repo(repo_id=repo_id, repo_type="dataset", exist_ok=True)

        uploads = [
            (archive_path, "I.tar.gz"),
            (flat_universe, "raw/i-series/universe.json"),
            (structured_universe, "raw/i-series/universe_structured.json"),
            (coverage_report, "raw/i-series/benchmark_universe_coverage.json"),
        ]

        for local_path, remote_path in uploads:
            print(f"Uploading {local_path} -> {remote_path}")
            api.upload_file(
                path_or_fileobj=str(local_path),
                path_in_repo=remote_path,
                repo_id=repo_id,
                repo_type="dataset",
                commit_message=f"Update LEAN benchmark data: {remote_path}",
            )

        latest = api.list_repo_commits(repo_id, repo_type="dataset")[0].commit_id

    print(f"\nUpload complete. Latest dataset revision: {latest}")
    return latest


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
        "--structured-universe", type=Path, default=DEFAULT_STRUCTURED_UNIVERSE,
        help="Path to structured frozen universe JSON file (default: %(default)s)",
    )
    parser.add_argument(
        "--coverage-report", type=Path, default=DEFAULT_COVERAGE_REPORT,
        help="Path to the coverage report JSON file (default: %(default)s)",
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
        revision = upload(
            lean_dir=args.lean_dir,
            flat_universe=args.universe,
            structured_universe=args.structured_universe,
            coverage_report=args.coverage_report,
            repo_id=args.repo_id,
            dry_run=args.dry_run,
        )
        if revision:
            print(f"REVISION={revision}")
    except Exception as e:
        print(f"ERROR: {e}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
