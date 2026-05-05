"""Dataclasses shared by TaskSuite, NPCProvider, and Evaluator plugins."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


JsonObject = Mapping[str, Any]


@dataclass(frozen=True)
class EvalItem:
    """Task envelope passed across platform and plugin boundaries."""

    task_id: str
    payload: JsonObject = field(default_factory=dict)
    version: str = "0"
    task_type: str = "multi_turn"
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class TranscriptMessage:
    """One visible conversation message."""

    role: str
    content: str
    ts: float | None = None
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class ToolLog:
    """Normalized tool-call record available to NPCs and evaluators."""

    name: str
    args: JsonObject = field(default_factory=dict)
    result: str = ""
    success: bool = True
    duration_ms: float = 0.0
    turn_index: int = 0
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class FileArtifact:
    """Workspace artifact referenced by a sample."""

    path: str
    content: bytes | str | None = None
    sha256: str = ""
    size: int | None = None
    media_type: str = ""
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class EvalSample:
    """Completed run sample consumed by an Evaluator."""

    sample_id: str
    task_id: str
    transcript: tuple[TranscriptMessage, ...] = ()
    tool_logs: tuple[ToolLog, ...] = ()
    files: Mapping[str, FileArtifact] = field(default_factory=dict)
    payload: JsonObject = field(default_factory=dict)
    metadata: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class NPCReply:
    """NPC response plus lifecycle signal."""

    message: str
    terminate: bool = False
    reason: str = ""
    payload: JsonObject = field(default_factory=dict)
    telemetry: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class Score:
    """Evaluator score payload."""

    value: float | None
    status: str = "scored"
    metrics: JsonObject = field(default_factory=dict)
    reason: str = ""
    evidence: tuple[str, ...] = ()
    telemetry: JsonObject = field(default_factory=dict)


@dataclass(frozen=True)
class EvaluatorMetadata:
    """Static evaluator metadata surfaced by the loader and registry."""

    evaluator_id: str
    version: str
    supported_tasks: frozenset[str] = frozenset()
    required_bundle_fields: frozenset[str] = frozenset()
    score_schema: JsonObject = field(default_factory=dict)
    capabilities: frozenset[str] = frozenset()
    metadata: JsonObject = field(default_factory=dict)
