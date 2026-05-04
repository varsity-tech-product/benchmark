"""Artifact visibility digest for user steering.

This stays intentionally shallow. It does not attempt task-specific semantic
coverage. It only answers whether the tutor appears to have generated code or
output artifacts that match the user's latest request but were not pasted
into the chat.
"""

from __future__ import annotations

import re
from typing import Optional

from server.core.workspace_delta import (
    diff_workspace_snapshot,
    is_image_candidate,
    is_text_candidate,
    read_workspace_preview,
    scan_workspace_snapshot,
)

_MAX_TRACKED_ARTIFACTS = 6
_CODE_EXTS = {".py", ".sh", ".bash", ".zsh", ".js", ".ts", ".cs", ".java", ".go", ".rs"}
_OUTPUT_EXTS = {".csv", ".json", ".log"}
_SUMMARY_EXTS = {".md", ".txt"}

_CODE_RE = re.compile(
    r"(^\s*(import |from |def |class |for |while |if |print\(|return |df\[))|```",
    re.MULTILINE,
)
_OUTPUT_RE = re.compile(
    r"(dtype:|^\s*\d+\s+\S+|NaN|DataFrame|GapDays|DayOfWeek|Close|Volume|\|.*\|)",
    re.MULTILINE,
)
_ASK_CODE_RE = re.compile(
    r"(exact code|literal code|copy-?paste|paste .*code|show .*code|code-only|tiny sample.*code)",
    re.IGNORECASE,
)
_ASK_OUTPUT_RE = re.compile(
    r"(exact output|literal output|\boutput\b|show .*output|table output|printed output|what .*output|result table)",
    re.IGNORECASE,
)
_ASK_COMPARISON_RE = re.compile(
    r"(compare|difference|trade-?off|versus|vs\.?|which one|two ways|two options)",
    re.IGNORECASE,
)
_FILE_LIMIT_RE = re.compile(
    r"(can't|cannot|can not|do not|don't|unable to).{0,40}"
    r"(file|files|artifact|artifacts|workspace|open|access|see|read|paste)",
    re.IGNORECASE,
)
_HIDDEN_ARTIFACT_RE = re.compile(
    r"(/workspace|saved to|written to|created (?:a|an)? file|see the file|"
    r"open the file|report file|artifact file|workspace artifact)",
    re.IGNORECASE,
)


def _compact_preview(text: str, max_chars: int = 220) -> str:
    text = " ".join((text or "").split())
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _looks_like_code(ext: str, preview: str) -> bool:
    return ext in _CODE_EXTS or bool(_CODE_RE.search(preview or ""))


def _looks_like_output(ext: str, preview: str) -> bool:
    return ext in _OUTPUT_EXTS or bool(_OUTPUT_RE.search(preview or ""))


def _looks_like_summary(ext: str, preview: str) -> bool:
    if ext in _SUMMARY_EXTS:
        return True
    stripped = (preview or "").strip()
    return stripped.startswith("#") or stripped.startswith("- ")


def classify_user_request(latest_user_text: str) -> dict[str, bool]:
    text = latest_user_text or ""
    return {
        "asks_for_code": bool(_ASK_CODE_RE.search(text)),
        "asks_for_output": bool(_ASK_OUTPUT_RE.search(text)),
        "asks_for_comparison": bool(_ASK_COMPARISON_RE.search(text)),
        "mentions_file_inaccessibility": bool(_FILE_LIMIT_RE.search(text)),
    }


def classify_chat_visibility(latest_agent_text: str) -> dict[str, bool]:
    text = latest_agent_text or ""
    return {
        "assistant_pasted_code": bool(_CODE_RE.search(text)),
        "assistant_pasted_output": bool(_OUTPUT_RE.search(text)),
        "assistant_refers_to_hidden_artifacts": bool(_HIDDEN_ARTIFACT_RE.search(text)),
    }


def build_visible_artifact_digest(
    *,
    workspace_path: Optional[str],
    previous_snapshot: Optional[dict[str, dict]],
    latest_user_text: str,
    latest_agent_text: str,
    shared_filenames: frozenset[str] = frozenset(),
) -> tuple[dict[str, dict], dict]:
    """Return ``(new_snapshot, artifact_digest)`` for the current tutor turn.

    *shared_filenames* contains workspace-relative paths that the agent
    attached this turn.  These files are already visible to the user
    via the file ledger and should not be flagged as hidden artifacts.
    """
    current_snapshot = scan_workspace_snapshot(workspace_path)
    delta = diff_workspace_snapshot(previous_snapshot, current_snapshot)

    # Exclude files the agent explicitly shared via attachments.
    candidate_files = [
        f
        for f in list(delta["created"]) + list(delta["modified"])
        if f["path"] not in shared_filenames
    ]
    artifact_entries: list[dict] = []

    has_new_code_artifact = False
    has_new_output_artifact = False
    has_new_summary_artifact = False
    has_new_image_artifact = False

    for meta in candidate_files[:_MAX_TRACKED_ARTIFACTS]:
        ext = meta.get("ext", "").lower()
        preview = ""
        if is_text_candidate(meta):
            preview = read_workspace_preview(workspace_path, meta["path"])

        entry = {
            "path": meta["path"],
            "ext": ext,
            "size": meta.get("size", 0),
            "change": "created" if meta in delta["created"] else "modified",
            "preview": _compact_preview(preview),
            "signals": [],
        }

        if _looks_like_code(ext, preview):
            entry["signals"].append("code")
            has_new_code_artifact = True
        if _looks_like_output(ext, preview):
            entry["signals"].append("output")
            has_new_output_artifact = True
        if _looks_like_summary(ext, preview):
            entry["signals"].append("summary")
            has_new_summary_artifact = True
        if is_image_candidate(meta):
            entry["signals"].append("image")
            has_new_image_artifact = True

        artifact_entries.append(entry)

    request_signals = classify_user_request(latest_user_text)
    chat_signals = classify_chat_visibility(latest_agent_text)

    artifact_ready_but_not_shown = (
        (
            request_signals["asks_for_code"]
            and has_new_code_artifact
            and not chat_signals["assistant_pasted_code"]
        )
        or (
            request_signals["asks_for_output"]
            and has_new_output_artifact
            and not chat_signals["assistant_pasted_output"]
        )
        or (
            request_signals["mentions_file_inaccessibility"]
            and chat_signals["assistant_refers_to_hidden_artifacts"]
            and (
                has_new_code_artifact
                or has_new_output_artifact
                or has_new_summary_artifact
            )
        )
    )

    digest = {
        "request_signals": request_signals,
        "chat_signals": chat_signals,
        "artifact_signals": {
            "created_count": len(delta["created"]),
            "modified_count": len(delta["modified"]),
            "deleted_count": len(delta["deleted"]),
            "has_new_code_artifact": has_new_code_artifact,
            "has_new_output_artifact": has_new_output_artifact,
            "has_new_summary_artifact": has_new_summary_artifact,
            "has_new_image_artifact": has_new_image_artifact,
        },
        "steering_signals": {
            "artifact_ready_but_not_shown": artifact_ready_but_not_shown,
            "user_should_request_literal_code": (
                request_signals["asks_for_code"]
                and has_new_code_artifact
                and not chat_signals["assistant_pasted_code"]
            ),
            "user_should_request_literal_output": (
                request_signals["asks_for_output"]
                and has_new_output_artifact
                and not chat_signals["assistant_pasted_output"]
            ),
            "avoid_new_branch": artifact_ready_but_not_shown,
        },
        "delta_preview": artifact_entries,
    }
    return current_snapshot, digest
