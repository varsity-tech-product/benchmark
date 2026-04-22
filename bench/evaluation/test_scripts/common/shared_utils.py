"""Shared text/workspace utilities for eval check modules.

Canonical implementations of helpers used by implementation_check,
backtest_engine_check, and strategy_research_check.  Individual check
modules import from here and may re-export or wrap with module-specific
defaults (e.g. max_chars, extra_suffixes, regex flags).
"""

from __future__ import annotations

import csv
import os
import re


def read_text_excerpt(path: str, max_chars: int = 16000) -> str:
    """Read a bounded excerpt from a text file.

    For large files, keep both the head and the tail so late-file
    definitions are not lost entirely.
    """
    with open(path) as fh:
        content = fh.read()
    if len(content) <= max_chars:
        return content
    head = content[: max_chars // 2]
    tail = content[-(max_chars // 2) :]
    return head + "\n...\n" + tail


def collect_artifact_text(
    workspace_path: str,
    tool_logs: list | None = None,
    *,
    extra_suffixes: tuple[str, ...] = (),
) -> str:
    """Collect workspace artifacts and tool traces as lowered text.

    Args:
        extra_suffixes: Additional file extensions to include
            (e.g. ``(".cs",)`` for LEAN C# files).
    """
    _base_suffixes = (".py", ".json", ".txt", ".md", ".csv", ".log")
    suffixes = _base_suffixes + extra_suffixes
    parts: list[str] = []

    for log in tool_logs or []:
        parts.append(str(getattr(log, "name", "")))
        parts.append(str(getattr(log, "args", {})))
        parts.append(str(getattr(log, "result", "") or ""))

    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if not fname.endswith(suffixes):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    parts.append(os.path.relpath(fpath, workspace_path))
                    parts.append(read_text_excerpt(fpath))
                except (IOError, UnicodeDecodeError):
                    continue

    return "\n".join(parts).lower()


def conversation_text(
    conversation: list | None = None,
    role: str | None = None,
) -> str:
    """Collect conversation text, optionally filtered by role."""
    snippets = []
    for turn in conversation or []:
        if role is not None and turn.get("role") != role:
            continue
        snippets.append(str(turn.get("content", "")))
    return "\n".join(snippets).lower()


def has_any(text: str, keywords: list[str]) -> bool:
    """Return True when any keyword is present as a substring."""
    return any(keyword.lower() in text for keyword in keywords)


def has_regex(
    text: str,
    patterns: list[str],
    *,
    multiline: bool = False,
) -> bool:
    """Return True when any regex pattern matches."""
    flags = re.IGNORECASE | (re.MULTILINE if multiline else 0)
    return any(re.search(pattern, text, flags) for pattern in patterns)


def has_metric_numbers(text: str, keyword_groups: list[list[str]]) -> bool:
    """Return True when at least two metric groups have nearby numeric values."""
    matched_groups = 0
    for group in keyword_groups:
        if any(
            re.search(
                rf"{re.escape(keyword)}[^0-9\-]{{0,40}}-?\d+(?:\.\d+)?",
                text,
                re.IGNORECASE,
            )
            for keyword in group
        ):
            matched_groups += 1
    return matched_groups >= 2


def workspace_files(
    workspace_path: str,
    *,
    suffixes: tuple[str, ...] | None = None,
) -> list[str]:
    """Return workspace files optionally filtered by suffix."""
    files: list[str] = []
    if not workspace_path or not os.path.isdir(workspace_path):
        return files
    for root, _, names in os.walk(workspace_path):
        for fname in sorted(names):
            if suffixes and not fname.endswith(suffixes):
                continue
            files.append(os.path.join(root, fname))
    return files


def workspace_csv_headers(workspace_path: str) -> list[tuple[str, list[str]]]:
    """Return CSV header rows for workspace CSV artifacts."""
    headers: list[tuple[str, list[str]]] = []
    for fpath in workspace_files(workspace_path, suffixes=(".csv",)):
        try:
            with open(fpath, newline="") as fh:
                row = next(csv.reader(fh), [])
        except (IOError, UnicodeDecodeError, StopIteration, csv.Error):
            continue
        if row:
            headers.append(
                (
                    os.path.basename(fpath),
                    [str(col).strip().lower() for col in row if str(col).strip()],
                )
            )
    return headers


def workspace_has_csv_columns(
    workspace_path: str,
    required_columns: list[str],
) -> bool:
    """Return True if any workspace CSV contains all required columns."""
    required = {col.lower() for col in required_columns}
    for _, header in workspace_csv_headers(workspace_path):
        if required.issubset(set(header)):
            return True
    return False


def tool_called(tool_logs: list | None, tool_name: str) -> bool:
    """Return True if a tool was called."""
    return any(getattr(log, "name", "") == tool_name for log in tool_logs or [])
