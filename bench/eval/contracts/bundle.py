"""Generic Bundle v1 alpha dataclasses.

The bundle is the public artifact contract between capture, scoring, and
downstream research consumers. The v1 alpha shape keeps the envelope generic:
messages, tool calls, arbitrary artifacts, and a workspace file snapshot.
Reference-harness fields live under ``artifacts["quanttutor"]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0.0-alpha"
BENCH_EVAL_VERSION = "1.0.0"
REFERENCE_ARTIFACT_KEY = "quanttutor"


@dataclass
class BundleTimestamps:
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    duration_seconds: float | None = None


@dataclass
class Message:
    message_id: str
    role: str
    content: Any = ""
    created_at: str = ""
    turn_index: int | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    tool_call_id: str
    tool_name: str
    args: Any = field(default_factory=dict)
    result: Any = None
    created_at: str = ""
    duration_ms: float | None = None
    success: bool | None = None
    turn_index: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkspaceFile:
    path: str
    sha256: str
    size_bytes: int
    entry_type: str = "file"
    content_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> int:
        return self.size_bytes


@dataclass
class WorkspaceSnapshot:
    root: str = ""
    files: list[WorkspaceFile] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Bundle:
    bundle_id: str
    schema_version: str
    task_id: str
    timestamps: BundleTimestamps
    agent_id: str
    sandbox_digest: dict[str, Any] = field(default_factory=dict)
    telemetry: dict[str, Any] = field(default_factory=dict)
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    artifacts: dict[str, Any] = field(default_factory=dict)
    workspace: WorkspaceSnapshot = field(default_factory=WorkspaceSnapshot)
    human_reviews: list[dict[str, Any]] = field(default_factory=list)

    @property
    def reference_artifact(self) -> dict[str, Any]:
        value = self.artifacts.get(REFERENCE_ARTIFACT_KEY)
        return value if isinstance(value, dict) else {}

    @property
    def session_id(self) -> str:
        return str(self.reference_artifact.get("session_id") or self.bundle_id)

    @property
    def persona_id(self) -> str:
        return str(self.reference_artifact.get("persona_id") or "")

    @property
    def termination_reason(self) -> str:
        return str(self.reference_artifact.get("termination_reason") or "")

    @property
    def workspace_manifest(self) -> list[WorkspaceFile]:
        return self.workspace.files
