"""Data manager for downloading and caching HuggingFace datasets.

Usage:
    from scripts.data_manager import ensure_data

    # Before running LEAN tasks (I/E/X-series with LEAN engine):
    paths = ensure_data(series="lean")
    # paths.lean_data         -> hf_cache/lean/I/ (mount as /lean/Data/)
    # paths.data_search_dirs  -> [hf_cache/lean/I/, hf_cache/lean/E/, hf_cache/lean/X/]
    # paths.student_code      -> hf_cache/lean/X/
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

HF_REPO_ID = _CFG_REPO_ID
DEFAULT_CACHE_DIR = Path(__file__).parent.parent / "data" / "hf_cache"


@dataclass
class DataPaths:
    docs: str | None = None  # hf_cache/docs/
    lean_data: str | None = None  # hf_cache/lean/I/ (LEAN mount)
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


def ensure_data(
    series: str = "i",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    hf_repo: str = HF_REPO_ID,
    revision: str | None = _CFG_REVISION,
) -> DataPaths:
    """Download data from HuggingFace if not cached locally.

    Args:
        series: "lean" for LEAN tasks (I/E/X with v2.2-lean),
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
        lean_dir = cache_dir / "lean"
        i_dir = lean_dir / "I"
        # Check for a key marker file, not just directory existence,
        # to handle partial downloads from interrupted runs.
        if not (i_dir / "universe.json").exists():
            # Download single archive (1 HTTP request) instead of 8000+ individual files
            archive = hf_hub_download(
                repo_id=hf_repo,
                repo_type="dataset",
                filename="I.tar.gz",
                local_dir=str(cache_dir),
                revision=revision,
            )
            with tarfile.open(archive, "r:gz") as tf:
                tf.extractall(path=str(lean_dir))
            # Remove archive after extraction to save disk space
            os.remove(archive)

        return DataPaths(
            docs=docs_path,
            lean_data=str(i_dir),
            data_search_dirs=[
                str(i_dir),
                str(lean_dir / "E"),
                str(lean_dir / "X"),
            ],
            student_code=str(lean_dir / "X"),
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
