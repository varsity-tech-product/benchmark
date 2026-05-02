"""Shared evidence-collection and keyword-matching helpers.

Used by D-series (and potentially other) evaluation scripts that need to
aggregate tool logs + workspace files into a single searchable string.
"""

from __future__ import annotations

import os
import re


def collect_evidence(workspace_path: str, tool_logs: list | None = None) -> str:
    """Aggregate text from tool logs and workspace files.

    Returns a single lowercased string suitable for keyword matching.
    """
    parts: list[str] = []
    for log in tool_logs or []:
        parts.append(log.name)
        parts.append(str(log.args))
        parts.append(str(log.result or ""))
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if fname.endswith((".txt", ".json", ".md", ".csv", ".log")):
                try:
                    with open(os.path.join(workspace_path, fname)) as f:
                        parts.append(f.read()[:2000])
                except (IOError, UnicodeDecodeError):
                    pass
    return " ".join(parts).lower()


def has_keywords(text: str, keywords: list[str]) -> bool:
    """Return True when any keyword appears as a substring."""
    return any(kw in text for kw in keywords)


def has_number(text: str) -> bool:
    """Return True when the text contains a numeric literal."""
    return bool(re.search(r"-?\d+\.?\d*", text))


def checklist_score(checklist: list[dict]) -> float:
    """Compute weighted score from a checklist.

    Each item: ``{"item": str, "weight": float, "passed": bool}``.
    """
    return sum(c["weight"] for c in checklist if c.get("passed", False))


def apply_data_source_cap(
    score: float,
    results: dict,
    tool_logs: list | None,
    data_files: list[str] | None,
) -> float:
    """Apply data-source verification penalty and update results dict.

    Returns the (possibly capped) score.
    """
    if not data_files:
        return score

    # Import here to avoid circular dependency at module load time
    from common.data_source_check import verify_data_source

    ds = verify_data_source(tool_logs or [], data_files)
    results["data_source_verified"] = ds["verified"]
    results["data_source_fraction"] = ds["fraction"]
    if not ds["verified"]:
        score *= max(0.25, ds["fraction"])
    return score
