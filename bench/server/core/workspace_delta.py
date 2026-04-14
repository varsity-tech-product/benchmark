"""Workspace delta helpers for server-side artifact visibility signals."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

_TEXT_EXTS = {
    ".py",
    ".txt",
    ".md",
    ".json",
    ".csv",
    ".log",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".sql",
    ".sh",
    ".bash",
    ".zsh",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".java",
    ".cs",
    ".go",
    ".rs",
    ".rb",
}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".svg", ".gif"}
_MAX_SCAN_FILES = 400
_MAX_TEXT_BYTES = 32 * 1024
_MAX_PREVIEW_BYTES = 2048
_MAX_PREVIEW_LINES = 40


def scan_workspace_snapshot(workspace_path: Optional[str]) -> dict[str, dict]:
    """Return a metadata-only snapshot of the workspace."""
    if not workspace_path:
        return {}

    root = Path(workspace_path)
    if not root.is_dir():
        return {}

    snapshot: dict[str, dict] = {}
    count = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel_path = path.relative_to(root).as_posix()
            stat = path.stat()
        except OSError:
            continue

        snapshot[rel_path] = {
            "path": rel_path,
            "size": int(stat.st_size),
            "mtime_ns": int(stat.st_mtime_ns),
            "ext": path.suffix.lower(),
        }
        count += 1
        if count >= _MAX_SCAN_FILES:
            break
    return snapshot


def diff_workspace_snapshot(
    previous: Optional[dict[str, dict]],
    current: dict[str, dict],
) -> dict:
    """Return created/modified/deleted file metadata between snapshots."""
    previous = previous or {}
    created: list[dict] = []
    modified: list[dict] = []
    deleted: list[dict] = []

    for rel_path, meta in current.items():
        old = previous.get(rel_path)
        if old is None:
            created.append(dict(meta))
            continue
        if old.get("mtime_ns") != meta.get("mtime_ns") or old.get("size") != meta.get(
            "size"
        ):
            modified.append(dict(meta))

    for rel_path, meta in previous.items():
        if rel_path not in current:
            deleted.append(dict(meta))

    created.sort(key=lambda item: item.get("mtime_ns", 0), reverse=True)
    modified.sort(key=lambda item: item.get("mtime_ns", 0), reverse=True)
    deleted.sort(key=lambda item: item.get("mtime_ns", 0), reverse=True)

    return {
        "created": created,
        "modified": modified,
        "deleted": deleted,
    }


def read_workspace_preview(workspace_path: Optional[str], rel_path: str) -> str:
    """Read a short preview for a small text file in the workspace."""
    if not workspace_path:
        return ""
    path = Path(workspace_path) / rel_path
    try:
        if not path.is_file():
            return ""
        if path.suffix.lower() in _IMAGE_EXTS:
            return ""
        if path.stat().st_size > _MAX_TEXT_BYTES:
            return ""
        raw = path.read_bytes()[:_MAX_PREVIEW_BYTES]
        text = raw.decode("utf-8", errors="ignore")
    except OSError:
        return ""

    return "\n".join(text.splitlines()[:_MAX_PREVIEW_LINES]).strip()


def is_text_candidate(meta: dict) -> bool:
    return meta.get("ext", "").lower() in _TEXT_EXTS


def is_image_candidate(meta: dict) -> bool:
    return meta.get("ext", "").lower() in _IMAGE_EXTS
