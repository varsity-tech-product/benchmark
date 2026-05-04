"""Bundle JSON serializer and deserializer with forward-compat reads.

The reader pulls only the fields it knows about from the dict and ignores
anything else. That lets a v1.0 reader open a v1.1 bundle (extra optional
fields appear, get silently dropped) without raising. See
``schema_evolution.md`` for the full rule set.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from .bundle import (
    AgentMessage,
    AgentMetadata,
    Bundle,
    ConversationTurn,
    RuntimeInfo,
    SessionInfo,
    StudentMessage,
    ToolCall,
    WorkspaceFile,
)


class BundleError(RuntimeError):
    """Raised when a bundle cannot be parsed."""


def to_dict(bundle: Bundle) -> dict:
    """Serialize a Bundle to a plain dict suitable for JSON dumping."""
    return asdict(bundle)


def to_json(bundle: Bundle, *, indent: int = 2) -> str:
    return json.dumps(to_dict(bundle), indent=indent, ensure_ascii=False)


def write(bundle: Bundle, path: str | Path) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_json(bundle), encoding="utf-8")
    return p


def from_dict(data: dict) -> Bundle:
    """Build a Bundle from a parsed JSON dict.

    Unknown top-level (and nested-object) fields are dropped. Missing
    optional fields fall back to dataclass defaults. ``schema_version``
    is required at the top level — readers use it to decide whether they
    can handle the bundle at all.
    """
    if not isinstance(data, dict):
        raise BundleError(f"Bundle must be a JSON object; got {type(data).__name__}")
    schema_version = data.get("schema_version")
    if not schema_version:
        raise BundleError("Bundle is missing required field 'schema_version'")
    if not _major_compatible(schema_version):
        raise BundleError(
            f"Bundle schema_version {schema_version!r} is not v1.x; "
            "this reader handles v1 only"
        )

    return Bundle(
        schema_version=schema_version,
        task_id=str(data.get("task_id", "")),
        task_version=str(data.get("task_version", "")),
        task_spec_hash=str(data.get("task_spec_hash", "")),
        persona_id=str(data.get("persona_id", "")),
        session=_session_from_dict(data.get("session") or {}),
        runtime=_runtime_from_dict(data.get("runtime") or {}),
        agent_metadata=_agent_metadata_from_dict(data.get("agent_metadata") or {}),
        conversation=[
            _turn_from_dict(t) for t in (data.get("conversation") or []) if isinstance(t, dict)
        ],
        workspace_manifest=[
            _workspace_file_from_dict(w)
            for w in (data.get("workspace_manifest") or [])
            if isinstance(w, dict)
        ],
    )


def from_json(s: str) -> Bundle:
    return from_dict(json.loads(s))


def read(path: str | Path) -> Bundle:
    p = Path(path)
    return from_json(p.read_text(encoding="utf-8"))


def _major_compatible(version: str) -> bool:
    try:
        major = int(str(version).split(".")[0])
    except (ValueError, IndexError):
        return False
    return major == 1


def _session_from_dict(d: dict) -> SessionInfo:
    return SessionInfo(
        session_id=str(d.get("session_id", "")),
        start_ts=str(d.get("start_ts", "")),
        end_ts=str(d.get("end_ts", "")),
        termination_reason=str(d.get("termination_reason", "")),
        turn_count=int(d.get("turn_count", 0) or 0),
    )


def _runtime_from_dict(d: dict) -> RuntimeInfo:
    kwargs: dict = {}
    if "sandbox_image" in d:
        kwargs["sandbox_image"] = str(d["sandbox_image"])
    if "npc_model" in d:
        kwargs["npc_model"] = str(d["npc_model"])
    if "bench_eval_version" in d:
        kwargs["bench_eval_version"] = str(d["bench_eval_version"])
    if d.get("seed") is not None:
        kwargs["seed"] = int(d["seed"])
    return RuntimeInfo(**kwargs)


def _agent_metadata_from_dict(d: dict) -> AgentMetadata:
    return AgentMetadata(
        self_reported_name=str(d.get("self_reported_name", "")),
        self_reported_version=str(d.get("self_reported_version", "")),
        harness=str(d.get("harness", "")),
    )


def _turn_from_dict(d: dict) -> ConversationTurn:
    return ConversationTurn(
        turn=int(d.get("turn", 0) or 0),
        agent=_agent_message_from_dict(d.get("agent") or {}),
        user=_user_message_from_dict(d.get("user")) if d.get("user") else None,
        tool_calls=[
            _tool_call_from_dict(tc)
            for tc in (d.get("tool_calls") or [])
            if isinstance(tc, dict)
        ],
    )


def _agent_message_from_dict(d: dict) -> AgentMessage:
    attachments = d.get("attachments") or []
    if not isinstance(attachments, list):
        attachments = []
    return AgentMessage(
        text=str(d.get("text", "")),
        reasoning=d.get("reasoning"),
        attachments=[a for a in attachments if isinstance(a, dict)],
    )


def _user_message_from_dict(d: dict | None) -> StudentMessage | None:
    if not isinstance(d, dict):
        return None
    return StudentMessage(text=str(d.get("text", "")))


def _tool_call_from_dict(d: dict) -> ToolCall:
    return ToolCall(
        call_id=str(d.get("call_id", "")),
        tool=str(d.get("tool", "")),
        args=d.get("args") if isinstance(d.get("args"), dict) else {},
        result_preview=str(d.get("result_preview", "")),
        result_truncated=bool(d.get("result_truncated", False)),
        ts=str(d.get("ts", "")),
        duration_ms=float(d.get("duration_ms", 0.0) or 0.0),
        success=bool(d.get("success", True)),
    )


def _workspace_file_from_dict(d: dict) -> WorkspaceFile:
    return WorkspaceFile(
        path=str(d.get("path", "")),
        sha256=str(d.get("sha256", "")),
        size=int(d.get("size", 0) or 0),
    )
