# RFC-A: QuantAgentBench Platform Contracts (v0)

> **Status**: Stage 1 v0 — internal, unstable. To be promoted to v1 stable at Stage 2 freeze (paper submission).
> **Code**: `bench/platform_api/`
> **Related**: #148 (v3 RFC), #150 (framework paper direction), #152 / #153 / #154 / #155 (delivered Stage 1 issues).

This document describes the platform layer of QuantAgentBench: the contracts that **platform** owns and **business reference implementations** must implement. RFC-B (the v3 reference business logic) is documented separately in the reference impl (Impl A) work.

---

## 1. Scope

The platform layer provides:

- Plugin contracts (`TaskSuite` / `NPCProvider` / `Evaluator`)
- Data models for items, samples, transcripts, tool logs, files, scores
- Sandbox runtime (image pull, volume mount, process isolation, tool routing)
- Plugin loader (explicit specs / config files / entry points)
- Telemetry primitives (push hook)
- Canonical naming registry (legacy → canonical)

**The platform does not predefine**:
- Specific rubrics (QR / QP / weighted vs hard-gating)
- Persona / phase / state-machine semantics
- Multi-turn vs single-turn conversation patterns
- Goodbye detection or termination heuristics
- The set of available tools (the registry is a contract, not a fixed list)
- The data sources or sandbox image (declared per-task by the TaskSuite)

Each of those is a business decision that lives in a reference implementation.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────┐
│  Reference / external impls (business)                         │
│  ┌─────────────┐  ┌───────────────┐  ┌──────────────────┐      │
│  │ TaskSuite   │  │ NPCProvider   │  │ Evaluator        │      │
│  └─────────────┘  └───────────────┘  └──────────────────┘      │
└────────────────────────────────────────────────────────────────┘
                ↓ uses platform API
┌────────────────────────────────────────────────────────────────┐
│  platform_api/ (this document)                                 │
│  ┌────────────┬──────────────┬──────────────┬───────────────┐  │
│  │ contracts  │ runtime      │ plugins      │ telemetry     │  │
│  │ (models +  │ (sandbox +   │ (loader +    │ (push hook +  │  │
│  │  ABCs)     │  tool router)│  bundle)     │  records)     │  │
│  └────────────┴──────────────┴──────────────┴───────────────┘  │
└────────────────────────────────────────────────────────────────┘
```

A complete configuration is a `PluginBundle = (TaskSuite, NPCProvider, Evaluator)` triple, plus optional metadata. The platform loads a bundle, executes sessions against the bundle's TaskSuite, and feeds completed `EvalSample`s to the bundle's Evaluator.

---

## 3. Plugin contracts (`platform_api.contracts.plugins`)

Three abstract base classes. Implementations must subclass and implement the abstract methods.

### 3.1 `TaskSuite`

```python
class TaskSuite(ABC):
    @abstractmethod
    def supported_tasks(self) -> set[str]: ...

    @abstractmethod
    def get_task(self, task_id: str) -> EvalItem: ...

    @abstractmethod
    def required_bundle_fields(self) -> set[str]: ...

    def data_mounts(self, task_id: str | None = None) -> tuple[DataMount, ...]: ...
    def sandbox_spec(self, task_id: str | None = None) -> SandboxSpec | None: ...
```

`data_mounts` and `sandbox_spec` default to reading from the per-task `EvalItem`. Suites with suite-level shared infrastructure can override.

### 3.2 `NPCProvider`

```python
class NPCProvider(ABC):
    @abstractmethod
    def initial_message(self, task: EvalItem) -> str: ...

    @abstractmethod
    def respond(
        self,
        transcript: Sequence[TranscriptMessage],
        tool_logs: Sequence[ToolLog],
        files: Mapping[str, FileArtifact],
        payload: Mapping[str, object],
    ) -> NPCReply: ...
```

The platform invokes `respond` once per agent message until the returned `NPCReply.terminate=True` or the platform-enforced turn limit is reached.

### 3.3 `Evaluator`

```python
class Evaluator(ABC):
    @abstractmethod
    def evaluate(self, item: EvalItem, sample: EvalSample) -> Score: ...

    @abstractmethod
    def metadata(self) -> EvaluatorMetadata: ...
```

Evaluators run offline against the persisted bundle. Multiple evaluators can score the same sample independently.

---

## 4. Data models (`platform_api.contracts.models`)

All data models are immutable dataclasses (`frozen=True`).

### 4.1 Envelope: `EvalItem`

```python
@dataclass(frozen=True)
class EvalItem:
    task_id: str
    payload: JsonObject = {}
    version: str = "0"
    task_type: str = "multi_turn"
    metadata: JsonObject = {}
    data_mounts: tuple[DataMount, ...] = ()
    sandbox_spec: SandboxSpec | None = None
```

- **Envelope (platform-controlled)**: `task_id`, `version`, `task_type`, `data_mounts`, `sandbox_spec`
- **Payload (business-controlled)**: `payload` is opaque to the platform; TaskSuite + NPCProvider + Evaluator within one PluginBundle share its schema

### 4.2 Sample: `EvalSample`

```python
@dataclass(frozen=True)
class EvalSample:
    sample_id: str
    task_id: str
    transcript: tuple[TranscriptMessage, ...] = ()
    tool_logs: tuple[ToolLog, ...] = ()
    files: Mapping[str, FileArtifact] = {}
    payload: JsonObject = {}
    metadata: JsonObject = {}
```

### 4.3 Other models

| Model | Purpose |
|---|---|
| `TranscriptMessage` | One conversation turn (`role`, `content`, `ts`, `metadata`) |
| `ToolLog` | One tool invocation record (`name`, `args`, `result`, `success`, `duration_ms`, `turn_index`, `metadata`) |
| `FileArtifact` | Workspace file reference (`path`, `content`, `sha256`, `size`, `media_type`, `metadata`) |
| `NPCReply` | NPC response (`message`, `terminate`, `reason`, `payload`, `telemetry`) |
| `Score` | Evaluator output (`value`, `status`, `metrics`, `reason`, `evidence`, `telemetry`) |
| `EvaluatorMetadata` | Static evaluator info (`evaluator_id`, `version`, `supported_tasks`, `required_bundle_fields`, `score_schema`, `capabilities`, `metadata`) |

`Score.value` is intentionally `float | None` — not all evaluators produce a single scalar. Multi-criteria evaluators can leave `value=None` and populate `metrics` and `status` (e.g., `"pass"`, `"fail"`, `"partial"`).

---

## 5. Sandbox runtime (`platform_api.runtime`)

### 5.1 `DataMount` — declarative data dependency

```python
@dataclass(frozen=True)
class DataMount:
    uri: str           # hf://dataset@<commit-sha-40> | s3://bucket/key?versionId=... | file://./local
    target_path: str   # absolute path inside sandbox
    read_only: bool = True
```

Supported URI schemes (Stage 1):
- `hf://` — HuggingFace dataset, **must include `@<40-hex commit sha>`**
- `s3://` — S3 object, **must include `versionId` query parameter**
- `file://` — local path (dev only)

**Stage 2 will consider**: `http://` arbitrary URLs (currently rejected; security review needed for BYO).

### 5.2 `SandboxSpec` — image and resource policy

```python
@dataclass(frozen=True)
class SandboxSpec:
    image_uri: str               # e.g., quanttutor/quant-tutor-lean@sha256:...
    resource_limits: JsonObject = {}
```

`resource_limits` is opaque to the contract; runtime implementations interpret keys like `cpu_count`, `memory_mb`, `wall_timeout_s`, `network_policy`. Stage 1 enforces only what the chosen `SandboxRuntime` supports.

### 5.3 Runtime interfaces

| Type | Purpose |
|---|---|
| `SandboxRuntime` (abstract) | Pull image + create sandbox + exec + cleanup |
| `DockerSandboxRuntime` | Production runtime (docker-backed) |
| `LocalSandboxRuntime` | Test runtime (subprocess-backed, no isolation) |
| `SandboxCreateRequest` / `SandboxHandle` / `SandboxMount` / `ExecResult` | Runtime data |
| `ToolRouter` | MCP-style tool dispatch into the sandbox |
| `ToolRequest` / `ToolResult` | Tool invocation envelope |
| `DataMountResolver` | Resolves `DataMount` URIs to local paths the runtime can mount |
| `build_sandbox_digest(SandboxSpec) -> str` | Bundle envelope helper |

The runtime records the `sandbox_digest` into each bundle's envelope so offline evaluators can detect non-standard sandbox runs.

---

## 6. Plugin loader (`platform_api.plugins.loader`)

### 6.1 Three load sources

```python
class PluginLoader:
    DEFAULT_ENTRY_POINT_GROUP = "quantagentbench.plugins"

    def load_spec(self, spec: PluginSpec | Mapping) -> PluginBundle: ...
    def load_config(self, source: str | Path | Mapping) -> list[PluginBundle]: ...
    def load_entry_points(self, *, group=None, names=None) -> list[PluginBundle]: ...
    def load_many(self, *, specs=(), config_files=(), include_entry_points=False, ...) -> list[PluginBundle]: ...
```

| Source | When to use |
|---|---|
| `PluginSpec` (explicit) | Tests, programmatic loading, dev workflow |
| Config file (JSON / TOML) | Operator deploys a fixed set of impls |
| Entry points (`quantagentbench.plugins`) | Third-party packages publish themselves via `pyproject.toml` |

Imports use `module:attribute` form (`module.path:ClassName`). Classes are instantiated with no args; callables are invoked once.

### 6.2 `PluginBundle` shape

```python
@dataclass(frozen=True)
class PluginBundle:
    name: str
    task_suite: TaskSuite
    npc_provider: NPCProvider
    evaluator: Evaluator
    metadata: Mapping[str, Any] = {}
```

Validation rejects bundles whose plugins do not satisfy the corresponding ABC.

---

## 7. Telemetry (`platform_api.telemetry`)

### 7.1 Record + push hook

```python
@dataclass(frozen=True)
class TelemetryRecord:
    namespace: str
    event: str
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    count: int = 1
    success: bool = True
    error: str | None = None
    attributes: dict[str, Any] = {}
    ts: float

class TelemetryHook(Protocol):
    def emit(self, record: TelemetryRecord) -> None: ...
```

### 7.2 Helpers

- `NullTelemetryHook` — no-op sink
- `InMemoryTelemetryHook` — accumulating sink with `totals()` for tests
- `TelemetryTimer` — context manager that emits latency + error on exit
- `emit_telemetry(...)` — one-shot emission

Plugins should pass through their own LLM cost telemetry as `TelemetryRecord` so the platform can aggregate per-session totals across plugin and runtime events.

---

## 8. Canonical naming (`platform_api.naming`)

The platform exposes a registry of legacy → canonical names so reference impls and migrations can target the new identifiers consistently.

| Existing name | Canonical name | Owner |
|---|---|---|
| `StudentSimulator` | `NPCProvider` (abstract) / `RefPersonaNPC` (impl) | `platform_api.contracts` / reference |
| `TutoringSession` | `Session` | platform runtime |
| `StudentPersona` | `Persona` | reference implementation |
| `student_opening` | `payload.student_opening` | reference task payload |
| `TUTOR_SYSTEM_PROMPT` | reference internal constant | reference implementation |

Reference impls (Impl A's master cleanup, #145) consume this registry to keep migrations in lockstep.

---

## 9. Stage 1 boundaries

### 9.1 In scope (delivered)

- All contracts, data models, sandbox runtime, plugin loader, telemetry, naming registry above
- `bundle_v1_alpha` JSON schema (`bench/eval/contracts/bundle_v1_alpha.schema.json`) with Impl A / C / D fixtures
- `build_background()` platform/business split (#155)

### 9.2 Out of scope (Stage 2 or later)

- BYO multi-tenant security (running untrusted external images)
- Formal deprecation policy with cross-version compatibility window
- Public docs / SDK release
- Bundle schema freeze to `1.0.0` stable (tracked in #160)
- HTTP arbitrary-URL data sources
- Custom sandbox image build pipeline

---

## 10. Forward-compat policy

**Stage 1 (now)**: All API surfaces are unstable. Internal callers may pin to `bundle_v1_alpha`, but the schema and dataclass shapes are subject to additive and breaking change without notice.

**Stage 2 (paper submission)**:
- Bundle schema promotes to `1.0.0` stable
- Plugin contract dataclass shapes freeze
- `TaskSuite` / `NPCProvider` / `Evaluator` ABC method signatures freeze
- A migration window of N=2 prior versions is committed for additive changes

**Post-paper**: Breaking changes follow a deprecation cycle of one minor release with `DeprecationWarning`, then removal.

---

## 11. Reference implementations

| Impl | Stage | Status | Code path |
|---|---|---|---|
| **Impl A** — v3 Reference (L0-L3 + 4-persona NPC + QR/QP unified judge) | Stage 1 | In progress (ewan, 6-8 wk) | `bench/server/reference/` (started; full extraction pending) |
| **Impl B** — Programmatic-only (no NPC, no LLM judge) | Stage 1 (after A) | Pending | TBD |
| **Impl C** — FinanceBench replication | Stage 2 | Filed (#156) | Pending |
| **Impl D** — Factor mining (additive validation) | Stage 2 | Filed (#157) | Pending |

See #150 for the validation strategy and #156 / #157 / #160 for Stage 2 trackers.

---

## 12. Change log

- **2026-05-05**: Initial RFC-A v0 doc — extracted from delivered code in #152 / #153 / #154 / #155.
