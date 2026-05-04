"""Bundle v1 dataclass: the immutable session artifact scoring reads from.

A Bundle captures everything a scorer needs to evaluate a finished session
without depending on the server runtime: task identity, session metadata,
turn-by-turn conversation with nested tool calls, and a workspace manifest.
The on-disk layout is one ``bundle.json`` per session; raw workspace files
live alongside as ordinary files (referenced by ``workspace_manifest``);
multiple ``score_v*`` files are written into a sibling ``scores/`` directory.

Forward-compat rules live in ``schema_evolution.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"
BENCH_EVAL_VERSION = "1.0.0"


@dataclass
class ToolCall:
    call_id: str
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    result_preview: str = ""
    result_truncated: bool = False
    ts: str = ""
    duration_ms: float = 0.0
    success: bool = True


@dataclass
class AgentMessage:
    text: str
    reasoning: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StudentMessage:
    text: str


@dataclass
class ConversationTurn:
    turn: int
    agent: AgentMessage
    user: StudentMessage | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class WorkspaceFile:
    path: str
    sha256: str
    size: int


@dataclass
class SessionInfo:
    session_id: str
    start_ts: str = ""
    end_ts: str = ""
    termination_reason: str = ""
    turn_count: int = 0


@dataclass
class RuntimeInfo:
    sandbox_image: str = ""
    npc_model: str = ""
    bench_eval_version: str = BENCH_EVAL_VERSION
    seed: int | None = None


@dataclass
class AgentMetadata:
    self_reported_name: str = ""
    self_reported_version: str = ""
    harness: str = ""


@dataclass
class Bundle:
    schema_version: str
    task_id: str
    task_version: str
    task_spec_hash: str
    persona_id: str
    session: SessionInfo
    runtime: RuntimeInfo
    agent_metadata: AgentMetadata
    conversation: list[ConversationTurn] = field(default_factory=list)
    workspace_manifest: list[WorkspaceFile] = field(default_factory=list)
