"""Shared helpers for S-series research eval scripts."""

from __future__ import annotations

import os
import re


def _read_text_excerpt(path: str, max_chars: int = 12000) -> str:
    """Read a bounded excerpt from a text file.

    For large files, keep both the head and the tail so late-file signal
    definitions do not disappear entirely.
    """
    with open(path) as f:
        content = f.read()
    if len(content) <= max_chars:
        return content
    head = content[: max_chars // 2]
    tail = content[-(max_chars // 2) :]
    return head + "\n...\n" + tail


def collect_evidence_text(
    workspace_path: str,
    tool_logs: list | None = None,
    conversation: list | None = None,
) -> str:
    """Collect tool, workspace, and conversation evidence as a single string."""
    parts: list[str] = []

    for log in tool_logs or []:
        parts.append(str(getattr(log, "name", "")))
        parts.append(str(getattr(log, "args", {})))
        parts.append(str(getattr(log, "result", "") or ""))

    for turn in conversation or []:
        parts.append(str(turn.get("role", "")))
        parts.append(str(turn.get("content", "")))

    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if not fname.endswith((".py", ".json", ".txt", ".md", ".csv", ".log")):
                    continue
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, workspace_path)
                try:
                    parts.append(rel_path)
                    parts.append(_read_text_excerpt(fpath))
                except (IOError, UnicodeDecodeError):
                    pass

    return "\n".join(parts).lower()


def conversation_text(conversation: list | None = None, role: str | None = None) -> str:
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


def has_regex(text: str, patterns: list[str]) -> bool:
    """Return True when any regex pattern matches."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def count_keyword_groups(text: str, keyword_groups: list[list[str]]) -> int:
    """Count how many conceptual groups appear in the evidence."""
    return sum(1 for group in keyword_groups if has_any(text, group))


def has_signal_definition(text: str) -> bool:
    """Detect whether a formal signal definition likely exists."""
    patterns = [
        r"df\[[\"']signal[\"']\]\s*=",
        r"[a-z_][a-z0-9_]*\[[\"']signal[\"']\]\s*=",
        r"\b(?:alpha_signal|trend_signal|reversion_signal|microstructure_signal|cross_asset_signal|composite_signal)\s*=",
        r"signal column",
        r"signal definition",
        r"composite signal",
    ]
    return has_regex(text, patterns)


def has_metric_evidence(text: str) -> bool:
    """Detect signal-evaluation metrics or the new evaluate_signal tool."""
    metric_terms = [
        "evaluate_signal",
        "information coefficient",
        "ic decay",
        "ic_ir",
        "spearman",
        "quantile",
        "turnover",
        "hit rate",
        "signal_evaluation",
    ]
    return has_any(text, metric_terms)


def has_pnl_evidence(text: str) -> bool:
    """Detect rough PnL or return diagnostics."""
    pnl_terms = [
        "sharpe",
        "total return",
        "annualized return",
        "strategy_return",
        "rough pnl",
        "max drawdown",
        "cumulative return",
        "equity",
        "pnl",
    ]
    return has_any(text, pnl_terms)
