"""Data manager for downloading and caching HuggingFace datasets.

Usage:
    from scripts.data_manager import ensure_data

    # Before running I-series tasks:
    paths = ensure_data(series="i")
    # paths.lean_data         -> LEAN-format data dir (mount as /lean/Data/)
    # paths.data_search_dirs  -> [hf_cache/I/] for staging universe.json etc.
    # paths.docs              -> hf_cache/docs/

    # Before running non-I tasks (B/D/S/E/X/A):
    paths = ensure_data(series="non_i")
    # paths.data_search_dirs  -> [hf_cache/BDS/, hf_cache/A/]
    # paths.docs              -> hf_cache/docs/
    # paths.student_code      -> hf_cache/X/
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
    lean_data: str | None = None  # hf_cache/I/ (I-series LEAN mount)
    data_search_dirs: list[str] = field(
        default_factory=list
    )  # dirs to search for data_files
    student_code: str | None = None  # hf_cache/X/ (debug tasks)


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


def ensure_data(
    series: str = "i",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    hf_repo: str = HF_REPO_ID,
    revision: str | None = _CFG_REVISION,
) -> DataPaths:
    """Download data from HuggingFace if not cached locally.

    Args:
        series: "i" for I-series (LEAN format), "non_i" for all other series.
        cache_dir: Local directory for caching downloaded data.
        hf_repo: HuggingFace dataset repo ID.
        revision: Optional HF commit hash for reproducible runs.

    Returns:
        DataPaths with local paths to the downloaded data.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    # Docs are shared across all series
    docs_path = _ensure_docs(cache_dir, hf_repo, revision)

    if series == "i":
        i_dir = cache_dir / "I"
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
                tf.extractall(path=str(cache_dir))
            # Remove archive after extraction to save disk space
            os.remove(archive)

        return DataPaths(
            docs=docs_path,
            lean_data=str(i_dir),
            data_search_dirs=[str(i_dir)],
        )

    elif series == "non_i":
        bds_dir = cache_dir / "BDS"

        if not bds_dir.exists():
            snapshot_download(
                repo_id=hf_repo,
                repo_type="dataset",
                allow_patterns=["BDS/**", "X/**", "A/**"],
                local_dir=str(cache_dir),
                revision=revision,
            )

        return DataPaths(
            docs=docs_path,
            data_search_dirs=[
                str(bds_dir),
                str(cache_dir / "A"),
            ],
            student_code=str(cache_dir / "X"),
        )

    else:
        raise ValueError(f"Unknown series: {series!r}. Use 'i' or 'non_i'.")
