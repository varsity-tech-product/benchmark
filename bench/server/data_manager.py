"""Server-local data manager for downloading and caching benchmark datasets."""

from __future__ import annotations

import os
import shutil
import tarfile
from dataclasses import dataclass, field
from pathlib import Path

from huggingface_hub import hf_hub_download, snapshot_download

from server.config.benchmark_config import DATASET_REPO_ID, DATASET_REVISION

BENCH_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = BENCH_ROOT / "data" / "hf_cache"
LEAN_RUNTIME_ROOT = BENCH_ROOT / "runtime_assets" / "lean"
LEAN_RUNTIME_DATA_DIR = LEAN_RUNTIME_ROOT / "data"
LEAN_RUNTIME_METADATA_DIR = LEAN_RUNTIME_ROOT / "metadata"
LEAN_RUNTIME_USER_CODE_DIR = LEAN_RUNTIME_ROOT / "user_code"
LOCAL_CUSTOM_DATA_DIR = BENCH_ROOT / "data" / "custom"
_REVISION_MARKER = ".hf_revision"


@dataclass
class DataPaths:
    docs: str | None = None
    lean_data: str | None = None
    custom_data: str | None = None
    data_search_dirs: list[str] = field(default_factory=list)
    user_code: str | None = None


def _revision_matches(target_dir: Path, revision: str | None) -> bool:
    if revision is None:
        return True
    marker = target_dir / _REVISION_MARKER
    if not marker.exists():
        return False
    try:
        return marker.read_text(encoding="utf-8").strip() == revision
    except OSError:
        return False


def _write_revision_marker(target_dir: Path, revision: str | None) -> None:
    if revision is None:
        return
    (target_dir / _REVISION_MARKER).write_text(revision, encoding="utf-8")


def _ensure_docs(
    cache_dir: Path,
    hf_repo: str,
    revision: str | None,
) -> str:
    docs_dir = cache_dir / "docs"
    if docs_dir.exists() and not _revision_matches(docs_dir, revision):
        shutil.rmtree(docs_dir, ignore_errors=True)
    if not docs_dir.exists():
        snapshot_download(
            repo_id=hf_repo,
            repo_type="dataset",
            allow_patterns=["docs/**"],
            local_dir=str(cache_dir),
            revision=revision,
        )
        _write_revision_marker(docs_dir, revision)
    return str(docs_dir)


def _ensure_reference(
    cache_dir: Path,
    hf_repo: str,
    revision: str | None,
) -> str:
    ref_dir = cache_dir.parent / "reference"
    marker = ref_dir / ".hf_downloaded"
    if ref_dir.exists() and not _revision_matches(ref_dir, revision):
        shutil.rmtree(ref_dir, ignore_errors=True)
    if (
        ref_dir.exists()
        and _revision_matches(ref_dir, revision)
        and (marker.exists() or any(ref_dir.glob("I01_*")))
    ):
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
    marker.write_text("downloaded", encoding="utf-8")
    _write_revision_marker(ref_dir, revision)
    return str(ref_dir)


def _ensure_local_lean_runtime_assets() -> tuple[str, str, str]:
    """Validate the tracked LEAN runtime assets required for standalone mode."""
    required_paths = [
        LEAN_RUNTIME_DATA_DIR / "BTC_UTC.csv",
        LEAN_RUNTIME_DATA_DIR / "E04_compound_bug.cs",
        LEAN_RUNTIME_DATA_DIR / "I05_candidate_pairs.json",
        LEAN_RUNTIME_DATA_DIR / "universe.json",
        LEAN_RUNTIME_METADATA_DIR / "universe.json",
        LEAN_RUNTIME_METADATA_DIR / "market-hours" / "market-hours-database.json",
        LEAN_RUNTIME_METADATA_DIR / "symbol-properties" / "security-database.csv",
        LEAN_RUNTIME_METADATA_DIR
        / "symbol-properties"
        / "symbol-properties-database.csv",
        LEAN_RUNTIME_USER_CODE_DIR / "alpha_conflict.cs",
        LEAN_RUNTIME_USER_CODE_DIR / "order_type_bug.cs",
        LEAN_RUNTIME_USER_CODE_DIR / "universe_stale.cs",
        LEAN_RUNTIME_USER_CODE_DIR / "warmup_bug.cs",
    ]
    missing = [str(path) for path in required_paths if not path.exists()]
    if missing:
        raise FileNotFoundError(
            "Missing tracked LEAN runtime assets required for standalone mode: "
            + ", ".join(missing)
        )
    return (
        str(LEAN_RUNTIME_METADATA_DIR),
        str(LEAN_RUNTIME_DATA_DIR),
        str(LEAN_RUNTIME_USER_CODE_DIR),
    )


def _resolve_custom_data_root(base_dir: Path) -> Path | None:
    """Return the directory that directly contains the Binance folders.

    Some dataset archives extract into ``custom/binance/...`` while others
    extract directly into ``binance/...``. Accept both layouts so cached data
    remains usable across archive revisions.
    """
    if (base_dir / "binance").is_dir():
        return base_dir
    nested_root = base_dir / "custom"
    if (nested_root / "binance").is_dir():
        return nested_root
    return None


def _ensure_custom_data(
    cache_dir: Path,
    hf_repo: str,
    revision: str | None,
) -> str:
    """Return the 12-col custom-data root, downloading it if needed."""
    if cache_dir.resolve() == DEFAULT_CACHE_DIR.resolve():
        local_custom_root = _resolve_custom_data_root(LOCAL_CUSTOM_DATA_DIR)
        if local_custom_root is not None:
            return str(local_custom_root)

    custom_dir = cache_dir / "lean" / "custom"
    if custom_dir.exists() and not _revision_matches(custom_dir, revision):
        shutil.rmtree(custom_dir, ignore_errors=True)

    custom_root = _resolve_custom_data_root(custom_dir)
    if custom_root is None:
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
        _write_revision_marker(custom_dir, revision)
        custom_root = _resolve_custom_data_root(custom_dir)

    if custom_root is None:
        raise FileNotFoundError(
            "custom_binance_12col archive extracted, but no Binance data root "
            f"was found under {custom_dir}"
        )

    return str(custom_root)


def ensure_data(
    series: str = "normal",
    cache_dir: str | Path = DEFAULT_CACHE_DIR,
    hf_repo: str = DATASET_REPO_ID,
    revision: str | None = DATASET_REVISION,
    need_reference: bool = False,
) -> DataPaths:
    """Download benchmark data from HuggingFace if not cached locally."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    docs_path = _ensure_docs(cache_dir, hf_repo, revision)
    if need_reference:
        _ensure_reference(cache_dir, hf_repo, revision)

    if series == "lean":
        lean_metadata_dir, lean_runtime_data_dir, user_code_dir = (
            _ensure_local_lean_runtime_assets()
        )
        custom_dir = _ensure_custom_data(cache_dir, hf_repo, revision)

        return DataPaths(
            docs=docs_path,
            lean_data=lean_metadata_dir,
            custom_data=custom_dir,
            data_search_dirs=[lean_runtime_data_dir],
            user_code=user_code_dir,
        )

    if series == "normal":
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
            bds_downloaded = normal_dir / "BDS"
            if bds_downloaded.exists():
                bds_downloaded.rename(bdex_dir)

        return DataPaths(
            docs=docs_path,
            data_search_dirs=[str(bdex_dir), str(normal_dir / "A")],
            user_code=str(normal_dir / "X"),
        )

    raise ValueError(f"Unknown series: {series!r}. Use 'lean' or 'normal'.")
