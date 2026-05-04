"""Data staging utilities for QuantTutorBench.

Creates temporary directories with whitelisted subsets of data, docs,
and task-local sample code for Docker container bind-mounts.
"""

import os
import shutil
import tempfile
from pathlib import Path


def create_staged_dirs(
    data_files: list[str],
    docs_available: list[str],
    data_search_dirs: list[str],
    docs_dir: str,
    force_temp_data_dir: bool = False,
) -> tuple[str, str, list[str]]:
    """Create temp directories with copies of only the allowed files.

    Args:
        data_files: List of allowed data file names (e.g. ["BTCUSDT_1h.csv"]).
        docs_available: List of allowed doc file names (e.g. ["moving_averages.md"]).
        data_search_dirs: Directories to search for data files (from HF cache).
        docs_dir: Directory containing reference docs (from HF cache).

    Returns:
        (staged_data_dir, staged_docs_dir, temp_dirs_to_cleanup)
    """
    temp_dirs: list[str] = []

    if data_files:
        staged_data = tempfile.mkdtemp(prefix="qtb_data_")
        temp_dirs.append(staged_data)
        for fname in data_files:
            for search_dir in data_search_dirs:
                src = os.path.join(search_dir, fname)
                if os.path.isfile(src):
                    dst = os.path.join(staged_data, fname)
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
                    break
    elif force_temp_data_dir and data_search_dirs:
        staged_data = tempfile.mkdtemp(prefix="qtb_data_")
        temp_dirs.append(staged_data)
        for search_dir in data_search_dirs:
            if not os.path.isdir(search_dir):
                continue
            for name in os.listdir(search_dir):
                src = os.path.join(search_dir, name)
                dst = os.path.join(staged_data, name)
                if os.path.isdir(src):
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                elif os.path.isfile(src):
                    os.makedirs(os.path.dirname(dst), exist_ok=True)
                    shutil.copy2(src, dst)
    else:
        staged_data = data_search_dirs[0] if data_search_dirs else ""

    if docs_available:
        staged_docs = tempfile.mkdtemp(prefix="qtb_docs_")
        temp_dirs.append(staged_docs)
        for fname in docs_available:
            src = os.path.join(docs_dir, fname)
            if os.path.isfile(src):
                dst = os.path.join(staged_docs, fname)
                shutil.copy2(src, dst)
    else:
        staged_docs = docs_dir

    return staged_data, staged_docs, temp_dirs


def create_staged_sample_code(
    sample_code: str | None,
    *,
    data_search_dirs: list[str] | None = None,
    user_code_dir: str | None = None,
) -> tuple[str | None, list[str]]:
    """Create a task-local sample-code directory with a neutral filename.

    The mounted ``/user_code`` directory should reveal only the current
    task's starter file, not benchmark-internal filenames such as
    ``alpha_conflict.cs``. The staged copy is renamed to
    ``user_code.<ext>`` while preserving file contents.
    """
    if not sample_code:
        return None, []

    data_search_dirs = data_search_dirs or []
    candidates: list[Path] = []

    if sample_code.startswith("user_code/") and user_code_dir:
        rel = sample_code[len("user_code/") :]
        candidates.append(Path(user_code_dir) / rel)
    elif sample_code.startswith("data/"):
        rel = sample_code[len("data/") :]
        candidates.extend(Path(root) / rel for root in data_search_dirs)
    else:
        if user_code_dir:
            candidates.append(Path(user_code_dir) / sample_code)
        candidates.extend(Path(root) / sample_code for root in data_search_dirs)

    source = next((path for path in candidates if path.is_file()), None)
    if source is None:
        raise FileNotFoundError(
            f"Could not resolve sample_code '{sample_code}' from the staged task inputs."
        )

    staged_dir = tempfile.mkdtemp(prefix="qtb_user_")
    suffix = source.suffix or ".txt"
    destination = Path(staged_dir) / f"user_code{suffix}"
    shutil.copy2(source, destination)
    return staged_dir, [staged_dir]


def cleanup_staged_dirs(temp_dirs: list[str]) -> None:
    """Remove temporary staged directories."""
    for d in temp_dirs:
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)
