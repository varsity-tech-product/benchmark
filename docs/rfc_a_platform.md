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
| **Impl A** — v3 Reference (L0-L2 + 4-persona NPC + QR/QP unified judge) | Stage 1 | Complete — bundle path live in prod since #170; long-tail parity in #175; #168 closed | `bench/server/reference/` |
| **Impl B** — Programmatic-only (no NPC, no LLM judge) | Stage 2 | Pending — no issue filed yet | TBD |
| **Impl C** — FinanceBench replication | Stage 2 | Filed (#156) | Pending |
| **Impl D** — Factor mining (additive validation) | Stage 2 | Filed (#157) | Pending |

See #150 for the validation strategy and #156 / #157 / #160 for Stage 2 trackers.

## 12. Contract gaps observed in Impl A

The first reference bundle integration (#168 → #170 / #175 / #178 / #181) exposed several Stage 2 freeze decisions. Each gap below is recorded with severity (P1 = real contract bypass, P2 = leaky abstraction, P3 = ergonomics) and disposition (where the decision lands).

### 12.1 NPCProvider session-start lifecycle

- **Symptom**: `SessionState.register_session` reflectively calls `build_user_simulator(task, persona, model)` on the reference NPC provider — a method not present on the `NPCProvider` ABC. A non-reference NPC provider that does not expose this method silently runs without a configured `UserSimulator`.
- **Severity**: P2.
- **Why it surfaced**: `NPCProvider` only declares `initial_message(item)` and `respond(transcript, tool_logs, files, payload)`. There is no contract-level "construct per-session state at registration time" hook, so the reference impl invented one.
- **Disposition**: Stage 2 freeze (#160). Either add a `start_session(item) -> SessionStateHandle` method to the ABC, or document that sessions must be stateless and any per-session state must travel through `respond`'s `payload`.

### 12.2 Stateful objects in payload across `respond` calls

- **Symptom**: `Session._generate_user_reply` sets `payload["user_sim"] = self._user_sim` so the live `UserSimulator` instance is carried across turns through `NPCProvider.respond`'s `payload` parameter.
- **Severity**: P2.
- **Why it surfaced**: The `EvalItem.payload` and `NPCReply.payload` dataclass docstrings describe payloads as JSON-shaped envelopes. Passing a Python object works in-process but breaks bundle export, RPC orchestration, and replay-from-bundle.
- **Disposition**: Stage 2 freeze (#160). Tied to 12.1 — once a formal session-state channel exists, payloads can be JSON-restricted by contract.

### 12.3 Reference-only TaskSuite extension methods

- **Symptom**: `SessionState._task_from_eval_item` reflectively calls `task_suite.get_business_task(task_id)` — a `ReferenceTaskSuite`-only method — to extract the underlying `QuantTutorTask`. A non-reference `TaskSuite` would not provide this.
- **Severity**: P2.
- **Why it surfaced**: The bridge stores the business schema under `EvalItem.payload["quant_tutor_task"]`; `SessionState` could read it from there, but the reflective shortcut was simpler. Indicates the contract surface for "reach the underlying business task" has no first-class method.
- **Disposition**: Stage 2. Either standardize `EvalItem.payload[<canonical key>]` as the documented extraction path, or add an optional `TaskSuite.get_business_task` to the ABC with a clear extension-surface annotation.

### 12.4 FileArtifact required-field shape for replay

- **Symptom**: After PR #170, `Session.respond` passes a real `dict[str, FileArtifact]` to NPC providers and `EvalSample.files` carries them to evaluators. The dataclass currently has `path` / `content` / `sha256` / `size` / `media_type` / `metadata`, all optional.
- **Severity**: P2.
- **Why it surfaced**: In-process reference orchestration only uses `path` and `content`. Out-of-process orchestration (RPC, bundle replay) needs a stricter contract — at minimum `sha256` for content addressing, possibly `pre_signed_url` for large blobs.
- **Disposition**: Stage 2 freeze (#160). Define which `FileArtifact` fields are required for round-trip replay; consider whether `content: bytes | str | None` should split into inline-vs-reference variants.

### 12.5 `Score.metrics["summary"]` flatten loses structure

- **Symptom**: `ReferenceEvaluator` returns `Score(value=..., metrics={"summary": <full eval output dict>})`. Per-track results, raw/normalized values, and pass thresholds are nested two levels deep under one synthetic key.
- **Severity**: P3.
- **Why it surfaced**: `Score` envelope has `value` + `metrics` + `evidence` but no first-class fields for raw / normalized / pass_threshold (#105 R3). Reference impl took the path of least resistance.
- **Disposition**: Stage 2 freeze (#160). #160 done-criteria already tracks the R3 decision: promote `raw_value` / `normalized_value` / `pass_threshold` to `Score`, or document a canonical key naming convention for `metrics`.

### 12.6 PluginLoader does not validate `bundle.json` metadata

- **Symptom**: `bundle.json` ships `metadata.business_schema` and `metadata.version` fields. `PluginLoader.load_config` does not validate or surface these — they are documentation-only.
- **Severity**: P3.
- **Why it surfaced**: Loader is permissive by design (forward-compat). But this means a plugin can ship incompatible business schema without surfacing the mismatch.
- **Disposition**: Stage 2 freeze (#160). Decide whether `PluginLoader` should enforce a `metadata.platform_api_version` compatibility check; if so, what the matrix is.

### 12.7 TaskSuite has no contract-level task ID surface declaration

- **Symptom**: PR #167 renamed `bench/tasks/layer2/` → `_legacy_v22_layer2/` without deleting. PR #170's `ReferenceTaskSuite._index_task_paths` used `tasks_dir.rglob("*.json")` and silently picked up legacy task IDs alongside v3 task IDs. PR #178 hardcoded `_TASK_LAYERS = ("L0", "L1", "L2")` in the reference suite to fix the leak.
- **Severity**: P2 (process trap; the leak surfaced in production live test).
- **Why it surfaced**: `TaskSuite.supported_tasks()` is the only declarative contract for which task IDs exist. There is no platform-level mechanism for the reference suite to declare "these are my valid task ID prefixes" beyond returning the full set. Two well-scoped PRs (rename in #167, broad rglob in #170) compounded into a real bug.
- **Disposition**: Stage 2. Consider whether `TaskSuite` should declare a `task_id_pattern: str` (regex or glob) on the ABC so the platform can warn when discovered IDs do not match. Alternative: keep the contract as-is and require reference impls to be explicit in their indexing logic (the current approach after #178).

### 12.8 Bundle export / re-import round-trip not exercised in Stage 1

- **Symptom**: Stage 1 tests exercised `EvalItem` + `EvalSample` in process. #183 adds Bundle v1 JSON round-trip coverage for Impl A across L0 / L1 / L2.
- **Severity**: P1 — the platform's offline-evaluation claim is not verified end-to-end.
- **Why it surfaced**: Stage 1 acceptance targeted live HTTP server health. Bundle v1 alpha schema (#161) shipped fixtures before #183 added full round-trip testing against Impl A.
- **Disposition**: Covered for Impl A in Stage 1.5 by `bench/tests/integration/test_bundle_roundtrip.py`. Stage 2 freeze (#160) extends the same harness to Impl C / Impl D when those implementations land.

### 12.9 Score parity coverage methodology

- **Symptom**: PR #170's first parity test pass had three of four evaluator dispatch tests using `monkeypatch.setattr` to replace `run_evaluation` / `evaluate_tracks`. The actual eval tree was never executed. Surfaced during PR #170 review; addressed by PR #170 continuation + PR #175.
- **Severity**: Process / methodology, not a contract gap.
- **Lesson**: Test count is not test efficacy. For platform contracts, parity tests must traverse the real code path on both sides (legacy + bundle) — mocking the underlying scorer makes parity tautological.

---

## 13. Change log

- **2026-05-05**: Initial RFC-A v0 doc — extracted from delivered code in #152 / #153 / #154 / #155.
- **2026-05-05** (Stage 1 closure): Expanded §12 Contract gaps with severity / disposition structure; added gaps 12.7 (`TaskSuite` ID surface), 12.8 (bundle round-trip), 12.9 (parity methodology). Updated §11 Impl A status to complete (bundle path live in prod via #170 / #175 / #178 / #181; gates 1.2 + 1.4 flipped in #150). Documents Stage 1 → Stage 2 transition.
- **2026-05-06** (#183 Stage 1.5): Added Impl A Bundle v1 round-trip coverage for representative L0 / L1 / L2 tasks and marked §12.8 covered for Impl A.
