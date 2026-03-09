"""Data manager for downloading and caching HuggingFace datasets.

Usage:
    from scripts.data_manager import ensure_data

    # Before running I-series tasks:
    paths = ensure_data(series="i")
    # paths.lean_data  -> local path to LEAN-format data (mount as /lean/Data/)
    # paths.universe   -> local path to universe.json (mount as /data/universe.json)

    # Before running S/B-series tasks:
    paths = ensure_data(series="sb")
    # paths.frozen_data -> local path to frozen CSVs (mount as /data/)
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from huggingface_hub import snapshot_download

HF_REPO_ID = "quant-tutor-bench/quant-tutor-bench-data"
DEFAULT_CACHE_DIR = Path(__file__).parent.parent / "data" / "hf_cache"


@dataclass
class DataPaths:
    lean_data: str | None = None       # LEAN-format data dir (for I-series)
    frozen_data: str | None = None     # Raw frozen CSVs (for S/B-series)
    universe: str | None = None        # universe.json path


def ensure_data(
    series: str = "i",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    hf_repo: str = HF_REPO_ID,
    revision: str | None = None,
) -> DataPaths:
    """Download data from HuggingFace if not cached locally.

    Args:
        series: "i" for I-series (LEAN format), "sb" for S/B-series (raw CSVs).
        cache_dir: Local directory for caching downloaded data.
        hf_repo: HuggingFace dataset repo ID.
        revision: Optional HF commit hash for reproducible runs.

    Returns:
        DataPaths with local paths to the downloaded data.
    """
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    if series == "i":
        lean_dir = cache_dir / "lean"
        universe_path = cache_dir / "universe.json"

        if not lean_dir.exists() or not universe_path.exists():
            snapshot_download(
                repo_id=hf_repo,
                repo_type="dataset",
                allow_patterns=["lean/**", "raw/i-series/universe.json"],
                local_dir=str(cache_dir),
                revision=revision,
            )
            # Move universe.json to expected location
            src = cache_dir / "raw" / "i-series" / "universe.json"
            if src.exists() and not universe_path.exists():
                src.rename(universe_path)

        return DataPaths(
            lean_data=str(lean_dir),
            universe=str(universe_path),
        )

    elif series == "sb":
        frozen_dir = cache_dir / "raw" / "sb-series"

        if not frozen_dir.exists():
            snapshot_download(
                repo_id=hf_repo,
                repo_type="dataset",
                allow_patterns=["raw/sb-series/**"],
                local_dir=str(cache_dir),
                revision=revision,
            )

        return DataPaths(frozen_data=str(frozen_dir))

    else:
        raise ValueError(f"Unknown series: {series!r}. Use 'i' or 'sb'.")
