"""Internal platform API v0 for QuantAgentBench plugins.

This package holds unstable platform contracts while the server monolith is
split into platform-owned runtime pieces and reference-owned business logic.
"""

from platform_api.contracts import (
    DataMount,
    EvalItem,
    EvalSample,
    Evaluator,
    EvaluatorMetadata,
    FileArtifact,
    NPCProvider,
    NPCReply,
    SandboxSpec,
    Score,
    TaskSuite,
    ToolLog,
    TranscriptMessage,
)
from platform_api.plugins import PluginBundle, PluginLoader, PluginLoadError, PluginSpec
from platform_api.runtime import (
    DataMountResolver,
    DockerSandboxRuntime,
    ExecResult,
    LocalSandboxRuntime,
    SandboxCreateRequest,
    SandboxHandle,
    SandboxMount,
    SandboxRuntime,
    SandboxRuntimeError,
    ToolRequest,
    ToolResult,
    ToolRouter,
    build_sandbox_digest,
)
from platform_api.telemetry import (
    InMemoryTelemetryHook,
    NullTelemetryHook,
    TelemetryRecord,
    TelemetryTimer,
)

__all__ = [
    "DataMount",
    "DataMountResolver",
    "DockerSandboxRuntime",
    "EvalItem",
    "EvalSample",
    "Evaluator",
    "EvaluatorMetadata",
    "ExecResult",
    "FileArtifact",
    "InMemoryTelemetryHook",
    "LocalSandboxRuntime",
    "NPCProvider",
    "NPCReply",
    "NullTelemetryHook",
    "PluginBundle",
    "PluginLoadError",
    "PluginLoader",
    "PluginSpec",
    "SandboxCreateRequest",
    "SandboxSpec",
    "SandboxHandle",
    "SandboxMount",
    "SandboxRuntime",
    "SandboxRuntimeError",
    "Score",
    "TaskSuite",
    "TelemetryRecord",
    "TelemetryTimer",
    "ToolLog",
    "ToolRequest",
    "ToolResult",
    "ToolRouter",
    "TranscriptMessage",
    "build_sandbox_digest",
]
