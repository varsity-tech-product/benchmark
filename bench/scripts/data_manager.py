"""Data manager for downloading and caching benchmark datasets.

Usage:
    from scripts.data_manager import ensure_data

    # Before running LEAN tasks (12-col custom data + LEAN metadata):
    paths = ensure_data(series="lean")
    # paths.lean_data         -> runtime_assets/lean/metadata/ (mount as /lean/Data/)
    # paths.custom_data       -> bench/data/custom/ or hf_cache/lean/custom/
    # paths.data_search_dirs  -> [runtime_assets/lean/data/]
    # paths.student_code      -> runtime_assets/lean/student_code/
    # paths.docs              -> hf_cache/docs/

    # Before running normal tasks (B/D/S/E/X/A):
    paths = ensure_data(series="normal")
    # paths.data_search_dirs  -> [hf_cache/normal/BDEX/, hf_cache/normal/A/]
    # paths.student_code      -> hf_cache/normal/X/
    # paths.docs              -> hf_cache/docs/
"""

from __future__ import annotations

import os

# Import pinned defaults from reproducibility config
import sys as _sys
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

_sys.path.insert(0, str(Path(__file__).parent.parent))
from config.benchmark_config import DATASET_REPO_ID as _CFG_REPO_ID  # noqa: E402
from config.benchmark_config import DATASET_REVISION as _CFG_REVISION  # noqa: E402

BENCH_ROOT = Path(__file__).parent.parent
HF_REPO_ID = _CFG_REPO_ID
DEFAULT_CACHE_DIR = BENCH_ROOT / "data" / "hf_cache"
LEAN_RUNTIME_ROOT = BENCH_ROOT / "runtime_assets" / "lean"
LEAN_RUNTIME_DATA_DIR = LEAN_RUNTIME_ROOT / "data"
LEAN_RUNTIME_METADATA_DIR = LEAN_RUNTIME_ROOT / "metadata"
LEAN_RUNTIME_STUDENT_CODE_DIR = LEAN_RUNTIME_ROOT / "student_code"
LOCAL_CUSTOM_DATA_DIR = BENCH_ROOT / "data" / "custom"


@dataclass
class DataPaths:
    docs: str | None = None  # hf_cache/docs/
    lean_data: str | None = None  # runtime_assets/lean/metadata/ (LEAN mount)
    custom_data: str | None = None  # hf_cache/lean/custom/ (12-col mount)
    data_search_dirs: list[str] = field(
        default_factory=list
    )  # dirs to search for data_files
    student_code: str | None = None  # debug task student code dir


def _ensure_docs(
    cache_dir: Path,
    hf_repo: str,
    revision: str | None,
) -> str:
    """Download shared docs if not cached. Returns docs dir path."""
    docs_dir = cache_dir / "docs"
    if not docs_dir.exists():
        snapshot_download(
            repo_id=hf_repo,
            repo_type="dataset",
            allow_patterns=["docs/**"],
            local_dir=str(cache_dir),
            revision=revision,
        )
    return str(docs_dir)


def _ensure_reference(
    cache_dir: Path,
    hf_repo: str,
    revision: str | None,
) -> str:
    """Download reference results if not cached. Returns reference dir path."""
    # Reference data lives alongside hf_cache, not inside it:
    #   bench/data/reference/  (sibling of bench/data/hf_cache/)
    ref_dir = cache_dir.parent / "reference"
    # Use a marker file to detect completed extraction
    marker = ref_dir / ".hf_downloaded"
    if ref_dir.exists() and (marker.exists() or any(ref_dir.glob("I01_*"))):
        return str(ref_dir)
    archive = hf_hub_download(
        repo_id=hf_repo,
        repo_type="dataset",
        filename="reference.tar.gz",
        local_dir=str(cache_dir),
        revision=revision,
    )
    ref_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive, "r:gz") as tf:
        tf.extractall(path=str(ref_dir))
    os.remove(archive)
    # Write marker so we don't re-download
    marker.write_text("downloaded")
    return str(ref_dir)


def _ensure_local_lean_runtime_assets() -> tuple[str, str, str]:
    """Validate the tracked LEAN runtime assets required for 12-col mode."""
    required_paths = [
        LEAN_RUNTIME_DATA_DIR / "BTC_UTC.csv",
        LEAN_RUNTIME_DATA_DIR / "E04_compound_bug.cs",
        LEAN_RUNTIME_DATA_DIR / "I05_candidate_pairs.json",
        LEAN_RUNTIME_DATA_DIR / "universe.json",
        LEAN_RUNTIME_METADATA_DIR / "universe.json",
        LEAN_RUNTIME_METADATA_DIR / "market-hours" / "market-hours-database.json",
        LEAN_RUNTIME_METADATA_DIR
        / "symbol-properties"
        / "security-database.csv",
        LEAN_RUNTIME_METADATA_DIR
        / "symbol-properties"
        / "symbol-properties-database.csv",
        LEAN_RUNTIME_STUDENT_CODE_DIR / "alpha_conflict.cs",
        LEAN_RUNTIME_STUDENT_CODE_DIR / "order_type_bug.cs",
        LEAN_RUNTIME_STUDENT_CODE_DIR / "universe_stale.cs",
        LEAN_RUNTIME_STUDENT_CODE_DIR / "warmup_bug.cs",
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing tracked LEAN runtime assets required for 12-col mode: "
            + ", ".join(missing)
        )
    return (
        str(LEAN_RUNTIME_METADATA_DIR),
        str(LEAN_RUNTIME_DATA_DIR),
        str(LEAN_RUNTIME_STUDENT_CODE_DIR),
    )


def _ensure_custom_data(
    cache_dir: Path,
    hf_repo: str,
    revision: str | None,
) -> str:
    """Return a local 12-col data root, downloading the archive if needed."""
    prefer_local_custom = (
        cache_dir.resolve() == DEFAULT_CACHE_DIR.resolve()
        and (LOCAL_CUSTOM_DATA_DIR / "binance").is_dir()
    )
    if prefer_local_custom:
        return str(LOCAL_CUSTOM_DATA_DIR)

    custom_dir = cache_dir / "lean" / "custom"
    if not (custom_dir / "binance").exists():
        custom_archive = hf_hub_download(
            repo_id=hf_repo,
            repo_type="dataset",
            filename="custom_binance_12col.tar.gz",
            local_dir=str(cache_dir),
            revision=revision,
        )
        custom_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(custom_archive, "r:gz") as tf:
            tf.extractall(path=str(custom_dir))
        os.remove(custom_archive)
    return str(custom_dir)


def ensure_data(
    series: str = "i",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    hf_repo: str = HF_REPO_ID,
    revision: str | None = _CFG_REVISION,
) -> DataPaths:
    """Prepare benchmark data for runtime use.

    Args:
        series: "lean" for LEAN tasks (12-col custom data + LEAN metadata),
                "normal" for all other tasks (B/D/S/E/X/A with v2.2).
        cache_dir: Local directory for caching downloaded data.
        hf_repo: HuggingFace dataset repo ID.
        revision: Optional HF commit hash for reproducible runs.

    Returns:
        DataPaths with local paths to the downloaded data.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Docs and reference are shared across all series
    docs_path = _ensure_docs(cache_dir, hf_repo, revision)
    _ensure_reference(cache_dir, hf_repo, revision)

    if series == "lean":
        lean_metadata_dir, lean_runtime_data_dir, student_code_dir = (
            _ensure_local_lean_runtime_assets()
        )
        custom_dir = _ensure_custom_data(cache_dir, hf_repo, revision)

        return DataPaths(
            docs=docs_path,
            lean_data=lean_metadata_dir,
            custom_data=custom_dir,
            data_search_dirs=[lean_runtime_data_dir],
            student_code=student_code_dir,
        )

    elif series == "normal":
        normal_dir = cache_dir / "normal"
        bdex_dir = normal_dir / "BDEX"

        if not bdex_dir.exists():
            snapshot_download(
                repo_id=hf_repo,
                repo_type="dataset",
                allow_patterns=["BDS/**", "X/**", "A/**"],
                local_dir=str(normal_dir),
                revision=revision,
            )
            # Rename BDS → BDEX after download
            bds_downloaded = normal_dir / "BDS"
            if bds_downloaded.exists():
                bds_downloaded.rename(bdex_dir)

        return DataPaths(
            docs=docs_path,
            data_search_dirs=[
                str(bdex_dir),
                str(normal_dir / "A"),
            ],
            student_code=str(normal_dir / "X"),
        )

    else:
        raise ValueError(f"Unknown series: {series!r}. Use 'lean' or 'normal'.")
