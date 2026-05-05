"""Dataclasses shared by TaskSuite, NPCProvider, and Evaluator plugins."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping
from urllib.parse import parse_qs, urlparse


JsonObject = Mapping[str, Any]
SUPPORTED_DATA_URI_SCHEMES = frozenset({"hf", "s3", "file"})
HF_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$", re.IGNORECASE)


def _hf_commit_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    ref = f"{parsed.netloc}{parsed.path}".strip("/")
    if "@" not in ref:
        return ""
    revision = ref.rsplit("@", 1)[1]
    return revision.split("/", 1)[0]


@dataclass(frozen=True)
class DataMount:
    """Task-declared data dependency mounted into a sandbox."""

    uri: str
    target_path: str
    read_only: bool = True

    def __post_init__(self) -> None:
        uri = self.uri.strip()
        target_path = self.target_path.strip()
        if not uri:
            raise ValueError("DataMount.uri is required")
        if not target_path.startswith("/"):
            raise ValueError("DataMount.target_path must be an absolute sandbox path")

        parsed = urlparse(uri)
        scheme = parsed.scheme.lower()
        if scheme not in SUPPORTED_DATA_URI_SCHEMES:
            supported = ", ".join(sorted(SUPPORTED_DATA_URI_SCHEMES))
            raise ValueError(f"Unsupported data URI scheme {scheme!r}; expected {supported}")
        if scheme == "hf":
            commit = _hf_commit_from_uri(uri)
            if not commit or not HF_COMMIT_RE.fullmatch(commit):
                raise ValueError(
                    "hf:// data mounts require an explicit @commit SHA "
                    "(40 hex characters)"
                )
        if scheme == "s3" and not parse_qs(parsed.query).get("versionId"):
            raise ValueError("s3:// data mounts require a versionId query parameter")

        object.__setattr__(self, "uri", uri)
        object.__setattr__(self, "target_path", target_path)


@dataclass(frozen=True)
class SandboxSpec:
    """Sandbox image and resource policy declared by a TaskSuite."""

    image_uri: str
    resource_limits: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        image_uri = self.image_uri.strip()
        if not image_uri:
            raise ValueError("SandboxSpec.image_uri is required")
        object.__setattr__(self, "image_uri", image_uri)


@dataclass(frozen=True)
class EvalItem:
    """Task envelope passed across platform and plugin boundaries."""

    task_id: str
    payload: JsonObject = field(default_factory=dict)
    version: str = "0"
    task_type: str = "multi_turn"
    metadata: JsonObject = field(default_factory=dict)
    data_mounts: tuple[DataMount, ...] = ()
    sandbox_spec: SandboxSpec | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "data_mounts", tuple(self.data_mounts))


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
