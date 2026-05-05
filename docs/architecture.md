# QuantAgentBench Architecture

This is the current shared architecture after #122 (Pure Quant Agent
redefinition) and #123 (eval package extraction + LLMRunner). When code
and docs disagree, trust the code and update this file in the same change.

The benchmark protocol lives in `BENCHMARK_SPEC.md` (v2.0). This document
covers the implementation/repo layout. The merge contract for the evaluation
boundary lives at `bench/*MD/v6.0/eval/dev_merge_server_side_eval_contract.md`.

**Marketing name** "QuantAgentBench" is the working name pending #122 TBD-4.
HuggingFace dataset `Varsity-Tech/quant-tutor-bench-data` and Docker images
`quant-tutor-env:v2.2` / `quant-tutor-lean:v1.0` retain the v1.0 names for
backward compatibility.

## Repo Map

```text
bench/
  platform_api/         Internal RFC-A v0 plugin contracts + sandbox API
  eval/                Standalone scoring package (no server runtime deps)
  server/              HTTP/MCP service, run control, session storage, UI
  client/              External client adapters for MCP and REST
  orchestrator/        Legacy pre-server batch scaffolding
  tasks/               Active task definitions in L0/, L1/, and L2/
  personas/            User persona profiles
  data/                Market/reference data
  experiments/         Validation experiments and generated report pipelines
  layer1/              Layer 1 (single-turn knowledge) runner + GEval config
  tests/               Unit, API, and integration tests
docs/                  Architecture and agent guidance
vercel-frontend/       Vercel-hosted frontend shell
```

`bench/eval/` is the scoring engine, decoupled from `bench/server/` per #123:
no `from server.*` imports, can be invoked from CI / notebooks / batch
without starting the server. `bench/server/` is the live runtime that drives
sessions and consumes `eval` as a library. `bench/orchestrator/` is legacy
and should not be used as a new dependency for evaluation orchestration; it
still backs `bench/run_benchmark.py` (the reference harness CLI) and houses
the older DeepEval-based simulator. Issue B (filed after #123 lands)
decommissions this path.

`vercel-frontend/` is a thin static preview package. Its build step copies the
current UI shell from `bench/server/web/templates/index.html` and static assets
from `bench/server/web/static/` into `vercel-frontend/public/`, so Vercel branch
deployments serve branch-local HTML/CSS/JS while backend API routes still proxy
to the production service.

## Platform API v0

`bench/platform_api/` is the internal RFC-A v0 surface introduced by #152 for
plugin-based Stage 1 work. Its import path is `platform_api.*`; this package
name preserves the Python stdlib `platform` module while tests place `bench/`
first on `sys.path`.

- `contracts/` defines the three plugin ABCs: `TaskSuite`, `NPCProvider`, and
  `Evaluator`. Shared dataclasses include `EvalItem`, `EvalSample`,
  `TranscriptMessage`, `ToolLog`, `FileArtifact`, `NPCReply`, `Score`,
  `EvaluatorMetadata`, `DataMount`, and `SandboxSpec`.
- `runtime/` defines `SandboxRuntime`, `SandboxCreateRequest`, `SandboxMount`,
  `SandboxHandle`, `ExecResult`, `ToolRequest`, `ToolResult`, and `ToolRouter`.
  `DockerSandboxRuntime` owns image pulls, container creation with volume,
  CPU/memory, and network flags, process execution through `docker exec`, and
  routed tool calls. `LocalSandboxRuntime` covers unit tests and local
  development flows.
- Stage 1 data-plane declarations use
  `DataMount(uri, target_path, read_only=True)` with
  `hf://owner/dataset@<40-char-commit-sha>`,
  `s3://bucket/key?versionId=...`, and `file://...` URIs. `http://` and
  `https://` sources stay outside Stage 1. Data mounts materialize to a local
  cache or existing file path before bind mounting into the sandbox.
- `SandboxSpec(image_uri, resource_limits)` is the TaskSuite-owned sandbox
  declaration. Stage 1 records `cpu_count` or `cpus`, `memory_mb` or `memory`,
  `wall_timeout_seconds`, and `network_enabled`. Reference base images are the
  supported Stage 1 image policy; forked or untrusted images belong to Stage 2.
- `plugins/loader.py` loads implementation triples from explicit
  `PluginSpec`s, JSON/TOML config files, and Python entry points under
  `quantagentbench.plugins`.
- `telemetry.py` exposes a push hook: `TelemetryRecord`,
  `NullTelemetryHook`, `InMemoryTelemetryHook`, and `TelemetryTimer`. Records
  carry token counts, cost, latency, success, error, and plugin/runtime
  attributes.
- `naming.py` records the current canonical naming table: `NPCProvider` for the
  platform abstract user/NPC boundary, `Session` for platform session runtime,
  `Persona` for reference business payloads, `payload.student_opening` for the
  reference opening field, and `TUTOR_SYSTEM_PROMPT` as reference-internal
  prompt configuration.

## Reference Prompt Boundary

`bench/server/reference/` owns the first concrete platform plugin bundle.
`bundle.json` is loaded through `PluginLoader.load_config(...)` and points to
`ReferenceTaskSuite`, `ReferenceNPCProvider`, and `ReferenceEvaluator`.
`ReferenceTaskSuite` keeps `QuantTutorTask` as the reference business schema
and maps it into `EvalItem` envelopes at the boundary, including `data_files`
to `DataMount` translation and sandbox declarations.
The reference task corpus is indexed only from `bench/tasks/L0/`,
`bench/tasks/L1/`, and `bench/tasks/L2/`; the v2.2 legacy task tree has been
removed from Impl A discovery.

`RefSystemPrompt` builds persona, interaction, and visible tool-log behavior
text for the reference simulator. `ReferenceNPCProvider` delegates runtime user
turns to `UserSimulator` and propagates the persona-emitted `task_end` flag.
`ReferenceEvaluator` handles deterministic L0/L1 scoring and delegates Layer 2
QR/QP scoring to the existing coordinator path. `server.config.prompt_config`
keeps existing server call sites stable through compatibility wrappers.

`server.core.session.build_background()` returns a JSON-able
`platform_background.v1` fact object for sandbox image, resource limits,
systems, mounts, and MCP tool discovery metadata. Agent communication rules
live in MCP tool descriptions such as `send_message`; reference persona
behavior lives in `bench/server/reference/`.

This v0 surface is internal and source-level. Stage 2 owns public SDK/docs,
multi-tenant BYO image security, and formal schema/deprecation policy.

## Server Entrypoint

`bench/server/__main__.py` parses server flags and calls
`server.api.http_app.create_app(...)`. The Starlette app exposes:

- MCP at `/mcp`
- client REST under `/session/*`
- run resume/replay REST under `/api/runs/*`
- operator REST under `/ops/*`
- UI/client run routes from `bench/server/web/ui_app.py`

The `BenchSessionManager` in `http_app.py` owns live sessions, the run store,
quota checks, background tool jobs, cleanup, and restore-from-storage.

## Production Deployment

Production runs on the VPS under Linux user `bench` from
`/home/bench/benchmark`. The systemd unit lives at
`deploy/quanttutor.service` and starts:

```bash
cd /home/bench/benchmark/bench
/home/bench/benchmark/.venv/bin/python -m server --host 127.0.0.1 --port 8000 --docker
```

The unit sets
`QTB_PLUGIN_CONFIG=/home/bench/benchmark/bench/server/reference/bundle.json`,
so production uses the reference plugin bundle as the default platform path.

GitHub Actions deploys through a self-hosted runner installed on the VPS with
labels `production,bench-vps`. The workflow in `.github/workflows/deploy.yml`
syncs the checkout into `/home/bench/benchmark`, maintains the virtualenv,
rebuilds sandbox Docker images when Dockerfiles change, maintains v3 sandbox
aliases (`quant-bench-env:v3.0` and `quant-bench-env:v3.0-lean`), restarts
`quanttutor`, and verifies `/health`.

The `bench` SSH IP allowlist stays in `/etc/security/access.conf`. The
self-hosted runner keeps deployment local to the VPS and preserves that SSH
policy.

## Permission Boundary

Clients can read public task labels, create or claim runs, register/start
sessions, send tutor messages, call allowed workspace tools, and read exported
result or score state.

Clients cannot trigger scoring:

- no MCP `request_evaluation`, `get_results`, or `get_scores`
- no public `POST /session/{sid}/evaluate`
- no scoring through `POST /session/{sid}/tool/{name}`

Scoring is server-owned. Two server-side triggers:

- **auto** — every terminal transition to `completed` (via `send_message`
  or the idle-timeout sweep) enqueues one eval keyed
  `auto:{session_id}`. The client then polls
  `GET /session/{sid}/scores`.
- **operator** — `POST /ops/session/{sid}/evaluate` (admin-token gated)
  for re-evaluation, alternate `eval_mode`, or recovery on bundles whose
  auto-eval failed. Operator reads stay at `GET /ops/session/{sid}/results`
  and `GET /ops/session/{sid}/scores`.

The `/ops/*` surface is gated by the admin-token mechanism in
`server.web.ui_app`. Client read endpoints stay export-scoped and, when run
auth is enabled, require the owning run token.

`GET /client/tasks/catalog/labels` is the API-key catalog endpoint for external
agents before run creation. It returns only v3 public labels such as
`L2_ADV_01_investment_advice`; task category, difficulty, rubrics, and solution
paths stay out of the client catalog.

## Session Lifecycle

`server.api.session_api.SessionState` owns one tutoring session:

1. `register_session` loads task/persona and prepares runtime state.
2. `start_session` returns the user opening and enters `in_session`.
3. `send_message` advances the user simulator and tool trace.
4. Active sessions persist incremental `run_state.json` checkpoints under
   `results/runs/{run_id}/run_state.json`. In Docker mode the server commits
   resumable image layers as `bench-resume:{run_id}-{turn}` every
   `QTB_RESUME_SNAPSHOT_INTERVAL` turns (default 5) and keeps
   `QTB_RESUME_SNAPSHOT_KEEP` recent layers (default 3). The commit path copies
   `/workspace` into a container-internal resume directory before `docker commit`
   because `/workspace` is a bind mount. Suspend also writes a host-side
   `workspace_snapshot/` beside the active checkpoint so local mode and failed
   Docker snapshots still preserve workspace files.
5. Terminal status persists a result bundle, enters `completed`, and
   `_trigger_auto_eval()` enqueues a server-internal eval keyed
   `auto:{session_id}`. The same trigger fires from the idle-timeout sweep
   when a session crosses its deadline. The judge runs in a background
   thread; clients poll `GET /session/{sid}/scores` for the result.
6. `completed` is terminal for MCP/client tools.

`restore_from_storage()` reconstructs enough completed-session state from
`run_state.json` to read results, read scores, or run operator scoring without
restarting the tutoring runtime. Restore does not re-trigger auto-eval; the
score store is the source of truth for whether an eval has run.

`restore_active_from_storage()` reconstructs an active session from the
run-scoped checkpoint. `POST /api/runs/{run_id}/resume` authorizes with the
same run token, starts a new container from the latest resume layer when one is
available, overlays any host-side workspace snapshot, restores conversation/tool
history into `SessionState`, and returns the reattached `session_id` plus
REST/MCP endpoints. `GET /api/runs/{run_id}/replay`
returns read-only conversation and tool logs, and `GET /api/runs/{run_id}/state`
returns `phase`, `turn_count`, and the latest layer tag.

`POST /session/{sid}/retry` (run-token gated) lets the owning agent retry
a session whose `termination_reason` classifies as
`infrastructure_failure` (currently `user_sim_error:*`). The retry calls
`RunService.reset_for_retry()` to rebind the same RunAssignment back to
`claimed`, allocates a fresh session_id, and returns it. Other categories
(`agent_gave_up`, `max_turns_reached`, `terminal_success`, `unknown`) are
not retryable and return 409. The original failed bundle remains on disk
under its session_id as the failure receipt.

## Canonical Storage

Results are stored in-bundle:

```text
bench/results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:12]}/
  .session_id
  run_state.json
  agent_files/
  evaluations/
    index.json
    score_1/
      score.json
      cost.json
```

`run_state.json` is the only required machine-readable run artifact. The server
does not write or require `run_state.md`, bundle manifests, or a sibling
evaluation tree.

Active run checkpoints live beside run assignments:

```text
bench/results/runs/{run_id}/
  run.json
  run_state.json
  workspace_snapshot/
```

The active `run_state.json` contains replay fields (`conversation`, `tool_logs`),
resume metadata (`phase`, `turn_count`, `latest_layer_tag`, `snapshot_tags`),
workspace snapshot metadata, and ownership fields used by run-token
authorization. Completion removes the active checkpoint and workspace snapshot
after the final result bundle is written and deletes the run's resume layers.

Each `score_n/score.json` export carries `judge_reliability` metadata linking
the evaluation report to the selected validated judge-validation run, its
report paths, and the current-model match flag.

`server.storage.result_writer.save_run_state()` writes `run_state.json`,
`.session_id`, and the workspace snapshot. `eval.storage.score_store` owns
`evaluations/index.json` and append-only `score_n` directories (moved out
of server in #123 since score persistence is a scoring concern).

### Bundle v1

`bench/eval/contracts/bundle.py` defines the generic Bundle v1 alpha artifact
that the scoring path reads from. A `bundle.json` carries `bundle_id`,
`schema_version = "1.0.0-alpha"`, `task_id`, `timestamps`, `agent_id`,
`sandbox_digest`, `telemetry`, flat `messages`, flat `tool_calls`,
namespaced `artifacts`, and a `workspace` file snapshot (path + sha256 +
size for every file under `agent_files/`). Reference-harness fields such
as `persona_id`, `session_id`, `termination_reason`, task version/hash, TC
debug data, and scores live under `artifacts.quanttutor`. `contracts/bundle_io.py`
is the JSON serializer; `contracts/bundle_schema.py` validates against
`contracts/bundle_v1_alpha.schema.json`. See
`docs/bundle_v1_schema.md` and `bench/eval/contracts/schema_evolution.md`
for the rules.

`bench/eval/backfill/run_state_to_bundle.py` converts legacy
`run_state.json` artifacts to v1 bundles in place; pass `--recursive` to
walk a results root and `--force` to overwrite. New sessions are still
written by `result_writer.save_run_state()`; the writer-side migration
to bundles directly is future work.

## Evaluation Pipeline

The scoring engine lives at `bench/eval/`:

- `contracts/` — validates scoring requests and output shape.
  `bundle.py` + `bundle_io.py` define the v1 session artifact;
  `schemas.py` holds task/persona pydantic models (moved from
  `bench/server/schemas.py` in #123); `request.py` / `output.py`
  hold `EvalRequest` / `EvalOutput`.
- `core/coordinator.py` — `EvalCoordinator` runs preflight + tracks +
  overall scoring + persistence. The persistence step is gated by a
  `persist=True` flag the standalone path turns off.
- `core/preflight.py` — blocks non-computable tracks before LLM judging.
- `tracks/qr.py` and `tracks/qp.py` — per-track scoring. Per #122 the
  Tutor track has been deleted (no `tracks/tutor.py`).
- `judges/` — LLM-backed result/process judges. Per #122 the Tutor 7D
  judge has been deleted; only QR + QP remain.
- `programmatic/` — code, process, and tool-usage evaluators (no LLM).
  `code_eval.py` Layer C compares agent outputs to the reference as a
  tolerance band (sign-mismatch / pathological → 0; same-regime →
  tiered relative-error). Task-type-specific branches (single / sweep
  / comparison) are gated on
  `bench/data/reference/<task_id>/distribution.json`.
- `inputs/` — task/persona/conversation/reference context builders.
- `rubrics/` — judge rubrics + `rubric_registry.json`. Production scoring
  consumes QR/QP rubrics. Judge-validation experiments consume the frozen
  legacy `rubric_6d.json` for historical tutor transcript scoring. The
  registry carries active QR/QP mappings plus validation mappings for
  historical tutor dimensions.
- `storage/score_store.py` — append-only `evaluations/index.json` plus
  `score_n/score.json` and `cost.json` (moved from `bench/server/` in
  #123).
- `llm/runner.py` — `LLMRunner` (see "LLM Runner + Audit Log").
- `tool_filters.py` — `NON_SUBSTANTIVE_TOOLS` / `PROTOCOL_ONLY_TOOLS`
  shared by storage, evals, and process metrics.
- `llm_config.py` — judge model defaults + OpenRouter helpers used by
  the eval path (separate from the agent-facing `bench/config/llm_config.py`).
- `score.py` — top-level `score(bundle, *, bench_root, ...) → EvalOutput`
  entry for standalone use (see "Standalone Scoring").
- `backfill/run_state_to_bundle.py` — legacy `run_state.json` →
  `bundle.json` v1 conversion CLI.

LLM judge prompts are built through `judges/runtime/conv_geval.py`. Prompt
and output records include rubric ID/version, prompt template version,
judge model, judge temperature, transcript source, dimension, output
schema, context fields, and run timestamp metadata.

Per #122 the scoring path has **two LLM dependencies** (QR judge, QP
judge). Required-tool coverage is computed post-hoc in
`eval/judges/process_metrics.py` from `expected_mcp_tools` and recorded
tool names. The NPC user simulator remains the only LLM in the
conversation runtime; its replies do not enter scoring.

The headline KPI is `pass_rate`: per-task `task_score = 0.60 * QR +
0.40 * QP`, then `task_pass = task_score >= PASS_THRESHOLD`
(placeholder 0.5; freezes after baseline calibration per #122 TBD-1).
Wilson 95% CI on `pass_rate` is exposed alongside per-category pass
rates (sub-headline) and `task_score_mean / std` (diagnostic).

The operator REST endpoint calls `SessionState.request_evaluation()`,
which allocates a `score_n` run and delegates to `EvalCoordinator`. The
server-side CLI entrypoint is:

```bash
python -m server.scripts.eval_single run --session <session_id> --mode full
python -m server.scripts.eval_single get --session <session_id> --history
python -m server.scripts.eval_single list
```

`--mode` accepts `full` (default), `qr`, or `qp`. `tutor` is no longer a
valid mode. Batch drivers should be a thin wrapper around
`EvalCoordinator` (or `eval.score()` for the no-persistence path), not
a second evaluator architecture.

### Standalone Scoring

`eval.score(bundle, *, bench_root, ...) → EvalOutput` runs the same
pipeline without the server runtime. It accepts a loaded `Bundle` or a
path to `bundle.json`, reconstructs the flat `conversation` /
`tool_logs` shape the coordinator expects, materializes a synthetic
`run_state.json` in a scratch directory (preflight requires the file on
disk; the caller's `workspace_path` is never written into), and runs
`EvalCoordinator.run(persist=False)`. No score files are written;
callers receive `EvalOutput` and decide where to store it.

Decoupling guarantee: `bench/eval/` has zero `from server.*` imports.
The CI smoke `bench/tests/unit/test_eval_score_standalone.py` enforces
this with a `grep` assertion plus an end-to-end `score()` invocation.

### LLM Runner + Audit Log

`bench/eval/llm/runner.py` is the single point of contact between eval
and the LLM provider. Within `bench/eval/`,
`chat.completions.create()` appears only at `runner.py:140`; everything
else goes through `LLMRunner.call(call_id, model_id, messages,
prompt_id, prompt_version, ...)`. After #123 there are three call
sites — NPC user simulator, QR judge, QP judge — and all three
emit attributable audit rows.

Each call writes one `LLMCallRecord` to a pluggable `AuditSink`.
Defaults:

- `JsonlAuditSink(path)` — append-only JSONL, thread-safe, queryable
  with `jq` / `grep` / pandas. No schema migrations.
- `NullAuditSink()` — drops everything; used when no log path is set.

The module-level `default_runner()` reads the `QTB_AUDIT_LOG`
environment variable: set it to a file path to capture every call,
unset to silence. One JSONL line carries
`call_id, model_id, prompt_id, prompt_version, prompt_hash, tokens_in,
tokens_out, cost_usd, latency_ms, ts, success, error`. `prompt_id` is
the rubric ID for judges and `"npc.user"` for the NPC; `prompt_version`
tracks the rendered prompt template version (rubric content version
lives in `prompt_id` + `prompt_hash`).

Provider abstraction (issue #123 TBD-A1): v1 is single-provider
OpenRouter (Chat Completions). The seam for a multi-provider rewrite is
`LLMRunner._invoke`; nothing else cares about transport details.

## Judge Validation

`bench/experiments/judge_validation/` is the automated judge reliability gate for
external-agent scoring. It owns a fixed pilot corpus (`pilot_corpus.json`;
v4 = 20 real-run excerpts + 16 synthetic adversarial items, 8 adversarial
good/bad pairs), prompt rendering, repeated same-prompt judge runs,
prompt-format variants, one-factor sensitivity cases, adversarial-pair ranking
checks, multi-judge consistency aggregation, human label artifacts, Google Form
CSV conversion, bilingual and Chinese-only Google Form blueprint exports, blind
reviewer packet export (English and Chinese variants sharing the same English
transcripts), private sample-ID mapping, and Markdown/HTML/JSON reliability
reports.

The automated Stage 3 primary gate (`judge_validation_stats.json`) follows the
PR #99 pattern: categorical correctness through adversarial ranking accuracy
(`adversarial.ranking_pass_rate >= 0.85`) and cross-judge robustness through
per-dimension multi-judge within-one agreement (`multi_judge.by_dimension[*]
>= 0.85`). Repeated-run score delta, pass/fail flip rate, prompt-format score
deltas, sensitivity pass rate, evidence/reason coverage, lightweight
explanation consistency, raw disagreement examples, and residual risks are
diagnostics.

The human-alignment report (`human_alignment_stats.json`) is the absolute-score
diagnostic appendix. It joins
`judge_runs.json` with `human_labels.json` and reports exact agreement,
within-one agreement, mean absolute delta versus human labels, pass/fail
agreement, large disagreement examples, and bias slices by dimension, category,
persona, and transcript source. It also exposes the historical weak-dimension
list under `absolute_alignment_diagnostic.weak_dimensions` and two
multi-reviewer blocks:

- `inter_rater_agreement` — pairwise reviewer-vs-reviewer comparisons on
  `(sample_id, rubric_id, dimension)` groups that have ≥ 2 distinct reviewers.
  Same-reviewer duplicate submissions are excluded from overlap counts and
  pair comparisons. Produces overall + per-dimension + per-reviewer-pair
  agreement metrics plus a list of disagreements ≥ 2.
- `judge_vs_reviewer_mean` — judge delta against the per-reviewer-mean for
  groups with ≥ 2 distinct reviewers. Duplicates from one reviewer are
  collapsed into a per-reviewer mean first so one reviewer's two labels do
  not double-count against another's single label. Reports both
  `sample_dim_groups` (one row per sample/rubric/dimension) and
  `distinct_samples` so consumers do not misread groups as unique samples.

External-agent `score.json` exports carry the selected validation run through
the `judge_reliability` metadata block, populated from
`bench/eval/judge_reliability_reference.json`.

## Human Review Console

`bench/server/web/review_store.py` exposes archived session bundles for human
inspection through `/ui/review/*`. The Vercel-facing shell serves `/review` and
`/review/{bundle_id}` as SPA routes backed by the existing GitHub OAuth cookie.
Any authenticated GitHub reviewer can inspect the task spec, transcript, tutor
tool log, workspace tree, and judge evaluation for a completed bundle.

The isolated frontend treats Session Flow as the live run monitor and Human
Review as the archive-facing session view: active runs render their full
conversation in Session Flow, and completed runs link to `/review/{session_id}`.
New Run hands external agents a copyable REST prompt with a Moltbook-style skill
instruction, `/skill.md`, base URL, and API key.

Reviewer output is stored separately from judge-validation labels:

```text
bench/experiments/human_review/{bundle_id}/{github_user_id}.json
```

Each file follows `human_review_opinions_v1` and contains structured opinion
cards with `section`, optional `target`, `severity`, `comment`, `tags`, reviewer
metadata, and optional judge-disagreement scores. Overlapping fields such as
`sample_id`, `reviewer_id`, `label_version`, and `timestamp` are preserved for
downstream joins with `judge_validation/human_labels*.json`.

## Public Reads

`GET /session/{sid}/results` returns only export-scope run fields such as
session id, run id, public task label, persona id, terminal status,
conversation, key results, trace summary, and workspace file names. It must not
return raw tool logs, owner internals, debug histories, judge prompts, raw judge
responses, evaluator traces, or cost internals.

`GET /session/{sid}/scores` is read-only. It returns the v1 score response
shape from `eval.storage.score_store.build_v1_response()` and strips private
score/cost internals.

The v1 contract has a small public top level — clients should program against
just these fields:

| Field | Type | Notes |
|---|---|---|
| `schema_version` | str | Pinned `"1.0"` for the v1 envelope. |
| `score_id` | str \| null | `score_n` allocated by the score store. |
| `score_status` | enum | One of `pending \| running \| completed_scored \| completed_not_computable \| failed \| interrupted`. |
| `task_score` | float \| null | `0.6 * QR + 0.4 * QP`; null when not yet computable. |
| `task_pass` | bool \| null | `task_score >= PASS_THRESHOLD`; null while `PASS_THRESHOLD_CALIBRATED = False`. |
| `detail` | object | Opaque forward-compat blob — see below. |
| `status` | str | Envelope state from the score store (`pending \| running \| completed \| failed \| partial \| not_found \| history`). `partial` only appears for multi-id `?score_ids=` lookups when requested entries mix terminal and in-flight states. |

Everything else lives in `detail` and is **not part of the public contract**
— clients depending on `detail.dimensions[*].name`, `detail.tracks.{qr,qp}`,
or `detail.judge_reliability` are taking a beta dependency that may shift as
the eval pipeline evolves (multi-judge panel, variable rubric, etc.).
`detail.cost` is omitted from the public response and only surfaced via the
operator path.

Operator reads under `/ops/session/{sid}/results` and
`/ops/session/{sid}/scores` return the same v1 envelope plus the raw
`score`/`cost` blobs and `detail.cost` for audit and debugging.

## Web UI

`bench/server/web/ui_app.py` mounts admin/client UI routes.
`bench/server/web/ui_indexer.py` walks the canonical result tree and reads
in-bundle `evaluations/index.json` plus `score_n/score.json` and `cost.json`.
It also applies owner filtering for private/org/admin views.
`bench/server/web/review_store.py` builds the review read model from the same
result indexer and writes per-reviewer opinion files under
`bench/experiments/human_review/`.

## Tests

Important contract tests:

```bash
python -m pytest bench/tests/api/test_permissions.py
python -m pytest bench/tests/api/test_eval_flow.py
python -m pytest bench/tests/unit/test_eval_architecture_contracts.py
python -m pytest bench/tests/unit/test_eval_score_standalone.py
python -m pytest bench/tests/unit/test_bundle.py bench/tests/unit/test_backfill.py
python -m pytest bench/tests/unit/test_llm_runner.py
python -m pytest bench/tests/test_server_web_ui.py
python -m pytest bench/tests/test_run_control_token.py bench/tests/test_run_owner_filtering.py
```

`test_eval_score_standalone.py` enforces the `bench/eval/` ↔
`bench/server/` decoupling boundary with a static `grep` assertion;
fail it whenever you accidentally add a `from server.*` to the eval
package. `test_bundle.py` + `test_backfill.py` enforce Bundle v1
forward-compatibility and the legacy backfill round-trip.
`test_llm_runner.py` covers the audit log + provider seam.

Run broader REST/run-owner/auth tests when changing client run control,
Vercel-facing routes, or quota behavior.
