"""Bundle JSON serializer and deserializer with forward-compatible reads."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .bundle import (
    REFERENCE_ARTIFACT_KEY,
    Bundle,
    BundleTimestamps,
    Message,
    ToolCall,
    WorkspaceFile,
    WorkspaceSnapshot,
)


class BundleError(RuntimeError):
    """Raised when a bundle cannot be parsed."""


def to_dict(bundle: Bundle) -> dict[str, Any]:
    """Serialize a Bundle to a plain dict suitable for JSON dumping."""
    payload = asdict(bundle)
    if not payload.get("human_reviews"):
        payload.pop("human_reviews", None)
    return payload


def to_json(bundle: Bundle, *, indent: int = 2) -> str:
    return json.dumps(to_dict(bundle), indent=indent, ensure_ascii=False)


def write(bundle: Bundle, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_json(bundle), encoding="utf-8")
    return p


def from_dict(data: dict[str, Any]) -> Bundle:
    """Build a Bundle from a parsed JSON dict.

    Unknown fields are dropped. Missing optional fields fall back to dataclass
    defaults. ``schema_version`` is required because readers use it for major
    compatibility dispatch.
    """
    if not isinstance(data, dict):
        raise BundleError(f"Bundle must be a JSON object; got {type(data).__name__}")
    schema_version = data.get("schema_version")
    if not schema_version:
        raise BundleError("Bundle is missing required field 'schema_version'")
    if not _major_compatible(str(schema_version)):
        raise BundleError(
            f"Bundle schema_version {schema_version!r} is outside the v1 family"
        )
    if "messages" not in data and "conversation" in data:
        data = _legacy_to_alpha_dict(data)

    return Bundle(
        bundle_id=str(data.get("bundle_id") or ""),
        schema_version=str(schema_version),
        task_id=str(data.get("task_id") or ""),
        timestamps=_timestamps_from_dict(data.get("timestamps") or {}),
        agent_id=str(data.get("agent_id") or ""),
        sandbox_digest=_dict_or_empty(data.get("sandbox_digest")),
        telemetry=_dict_or_empty(data.get("telemetry")),
        messages=[
            _message_from_dict(item)
            for item in (data.get("messages") or [])
            if isinstance(item, dict)
        ],
        tool_calls=[
            _tool_call_from_dict(item)
            for item in (data.get("tool_calls") or [])
            if isinstance(item, dict)
        ],
        artifacts=_dict_or_empty(data.get("artifacts")),
        workspace=_workspace_from_dict(data.get("workspace") or {}),
        human_reviews=[
            item for item in (data.get("human_reviews") or []) if isinstance(item, dict)
        ],
    )


def from_json(s: str) -> Bundle:
    return from_dict(json.loads(s))


def read(path: str | Path) -> Bundle:
    p = Path(path)
    return from_json(p.read_text(encoding="utf-8"))


def validate(data: dict[str, Any]) -> None:
    from .bundle_schema import validate_bundle_dict

    validate_bundle_dict(data)


def validate_path(path: str | Path) -> None:
    from .bundle_schema import validate_bundle_path

    validate_bundle_path(path)


def _major_compatible(version: str) -> bool:
    try:
        major = int(str(version).split(".", 1)[0])
    except (ValueError, IndexError):
        return False
    return major == 1


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _timestamps_from_dict(d: dict[str, Any]) -> BundleTimestamps:
    return BundleTimestamps(
        created_at=str(d.get("created_at") or ""),
        started_at=str(d.get("started_at") or ""),
        completed_at=str(d.get("completed_at") or ""),
        duration_seconds=_float_or_none(d.get("duration_seconds")),
    )


def _message_from_dict(d: dict[str, Any]) -> Message:
    attachments = d.get("attachments") or []
    return Message(
        message_id=str(d.get("message_id") or ""),
        role=str(d.get("role") or ""),
        content=d.get("content", ""),
        created_at=str(d.get("created_at") or ""),
        turn_index=_int_or_none(d.get("turn_index")),
        attachments=[a for a in attachments if isinstance(a, dict)]
        if isinstance(attachments, list)
        else [],
        metadata=_dict_or_empty(d.get("metadata")),
    )


def _tool_call_from_dict(d: dict[str, Any]) -> ToolCall:
    return ToolCall(
        tool_call_id=str(d.get("tool_call_id") or ""),
        tool_name=str(d.get("tool_name") or ""),
        args=d.get("args", {}),
        result=d.get("result"),
        created_at=str(d.get("created_at") or ""),
        duration_ms=_float_or_none(d.get("duration_ms")),
        success=_bool_or_none(d.get("success")),
        turn_index=_int_or_none(d.get("turn_index")),
        metadata=_dict_or_empty(d.get("metadata")),
    )


def _workspace_from_dict(d: dict[str, Any]) -> WorkspaceSnapshot:
    files = d.get("files") or []
    return WorkspaceSnapshot(
        root=str(d.get("root") or ""),
        files=[
            _workspace_file_from_dict(item)
            for item in files
            if isinstance(item, dict)
        ],
        metadata=_dict_or_empty(d.get("metadata")),
    )


def _workspace_file_from_dict(d: dict[str, Any]) -> WorkspaceFile:
    return WorkspaceFile(
        path=str(d.get("path") or ""),
        sha256=str(d.get("sha256") or ""),
        size_bytes=int(d.get("size_bytes", d.get("size", 0)) or 0),
        entry_type=str(d.get("entry_type") or "file"),
        content_ref=str(d.get("content_ref") or ""),
        metadata=_dict_or_empty(d.get("metadata")),
    )


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _legacy_to_alpha_dict(data: dict[str, Any]) -> dict[str, Any]:
    session = data.get("session") if isinstance(data.get("session"), dict) else {}
    runtime = data.get("runtime") if isinstance(data.get("runtime"), dict) else {}
    agent = (
        data.get("agent_metadata")
        if isinstance(data.get("agent_metadata"), dict)
        else {}
    )
    messages: list[dict[str, Any]] = []
    tool_calls: list[dict[str, Any]] = []
    for turn in data.get("conversation") or []:
        if not isinstance(turn, dict):
            continue
        turn_number = int(turn.get("turn") or len(messages) + 1)
        user = turn.get("user") if isinstance(turn.get("user"), dict) else None
        agent_msg = turn.get("agent") if isinstance(turn.get("agent"), dict) else {}
        if user:
            messages.append(
                {
                    "message_id": f"legacy_user_{turn_number}",
                    "role": "user",
                    "content": user.get("text", ""),
                    "turn_index": turn_number - 1,
                }
            )
        messages.append(
            {
                "message_id": f"legacy_agent_{turn_number}",
                "role": "assistant",
                "content": agent_msg.get("text", ""),
                "turn_index": turn_number - 1,
                "attachments": agent_msg.get("attachments") or [],
                "metadata": {"reasoning": agent_msg.get("reasoning")},
            }
        )
        for tc in turn.get("tool_calls") or []:
            if not isinstance(tc, dict):
                continue
            tool_calls.append(
                {
                    "tool_call_id": tc.get("call_id", ""),
                    "tool_name": tc.get("tool", ""),
                    "args": tc.get("args", {}),
                    "result": tc.get("result_preview", ""),
                    "created_at": tc.get("ts", ""),
                    "duration_ms": tc.get("duration_ms"),
                    "success": tc.get("success"),
                    "turn_index": turn_number - 1,
                    "metadata": {"result_truncated": tc.get("result_truncated", False)},
                }
            )

    artifacts = _dict_or_empty(data.get("artifacts"))
    artifacts.setdefault(
        REFERENCE_ARTIFACT_KEY,
        {
            "persona_id": data.get("persona_id", ""),
            "session_id": session.get("session_id", ""),
            "termination_reason": session.get("termination_reason", ""),
            "task_version": data.get("task_version", ""),
            "task_spec_hash": data.get("task_spec_hash", ""),
            "agent_metadata": agent,
        },
    )
    workspace_files = [
        {
            "path": item.get("path", ""),
            "sha256": item.get("sha256", ""),
            "size_bytes": item.get("size", item.get("size_bytes", 0)),
        }
        for item in data.get("workspace_manifest") or []
        if isinstance(item, dict)
    ]
    return {
        "bundle_id": session.get("session_id", ""),
        "schema_version": data.get("schema_version", ""),
        "task_id": data.get("task_id", ""),
        "timestamps": {
            "created_at": session.get("start_ts", ""),
            "started_at": session.get("start_ts", ""),
            "completed_at": session.get("end_ts", ""),
        },
        "agent_id": agent.get("self_reported_name") or agent.get("harness") or "",
        "sandbox_digest": {"image": runtime.get("sandbox_image", "")},
        "telemetry": {
            "npc_model": runtime.get("npc_model", ""),
            "bench_eval_version": runtime.get("bench_eval_version", ""),
            "seed": runtime.get("seed"),
        },
        "messages": messages,
        "tool_calls": tool_calls,
        "artifacts": artifacts,
        "workspace": {"root": "agent_files", "files": workspace_files},
    }
