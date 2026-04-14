"""Data staging utilities for QuantTutorBench.

Creates temporary directories with whitelisted subsets of data and docs
files for Docker container bind-mounts.  Shared by both Exam Server
and Legacy Orchestrator.
"""

import os
import shutil
import tempfile


def create_staged_dirs(
    data_files: list[str],
    docs_available: list[str],
    data_search_dirs: list[str],
    docs_dir: str,
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


def cleanup_staged_dirs(temp_dirs: list[str]) -> None:
    """Remove temporary staged directories."""
    for d in temp_dirs:
        if os.path.exists(d):
            shutil.rmtree(d, ignore_errors=True)
