"""Server-side evidence normalization for TC checking.

Builds a compact, client-agnostic evidence summary from per-turn tool logs.
The TC checker can read this summary alongside the tutor's free-form text so
coverage judgments depend less on a specific client's writing style.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, is_dataclass
from typing import Any

from eval.tool_filters import NON_SUBSTANTIVE_TOOLS

_MAX_CALLS = 8
_MAX_ARGS_CHARS = 160
_MAX_RESULT_CHARS = 320
_WHITESPACE_RE = re.compile(r"\s+")


def _compact_text(value: Any, max_chars: int) -> str:
    text = "" if value is None else str(value)
    text = _WHITESPACE_RE.sub(" ", text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _log_to_dict(log: Any) -> dict:
    if isinstance(log, dict):
        return dict(log)
    if is_dataclass(log):
        return asdict(log)
    out = {}
    for key in (
        "name",
        "args",
        "result",
        "success",
        "turn_index",
        "duration_ms",
        "output_files",
    ):
        if hasattr(log, key):
            out[key] = getattr(log, key)
    return out


def build_turn_evidence(tool_logs: list, turn_index: int) -> dict:
    """Return a compact evidence digest for one tutor turn.

    Only substantive tools are included. ``send_message`` and benign metadata
    reads are excluded so the digest focuses on actual computational evidence.
    """
    relevant: list[dict] = []
    failed_total = 0

    for raw in tool_logs:
        log = _log_to_dict(raw)
        if log.get("turn_index") != turn_index:
            continue
        name = log.get("name", "")
        if name in NON_SUBSTANTIVE_TOOLS:
            continue

        args = log.get("args", {})
        success = bool(log.get("success"))
        if not success:
            failed_total += 1

        relevant.append(
            {
                "tool": name,
                "success": success,
                "duration_ms": round(float(log.get("duration_ms", 0.0) or 0.0), 1),
                "arg_keys": sorted(args.keys()) if isinstance(args, dict) else [],
                "args_preview": _compact_text(
                    json.dumps(args, default=str, ensure_ascii=False),
                    _MAX_ARGS_CHARS,
                ),
                "result_preview": _compact_text(
                    log.get("result", ""),
                    _MAX_RESULT_CHARS,
                ),
                "output_files": list(log.get("output_files", []) or [])[:3],
            }
        )

    calls_truncated = False
    displayed = relevant
    if len(displayed) > _MAX_CALLS:
        displayed = displayed[:4] + displayed[-4:]
        calls_truncated = True

    return {
        "turn_index": turn_index,
        "substantive_tool_count": len(relevant),
        "failed_tool_count": failed_total,
        "calls_truncated": calls_truncated,
        "calls": displayed,
    }


def serialize_turn_evidence(evidence: dict, max_chars: int = 2600) -> str:
    """Serialize evidence to a prompt-friendly string."""
    text = json.dumps(evidence, ensure_ascii=False, indent=2, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
