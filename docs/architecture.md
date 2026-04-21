# QuantTutorBench — Architecture

Cold-start map of the system as it exists today. Read before touching an
unfamiliar subsystem; trust the code over the doc when they disagree, and
update the doc in the same commit when a slice changes the shape.

The repo grew through two eras. The **server era** (`bench/server/`) is the
current code path: an HTTP/MCP service that runs tutoring sessions and
scores them. The **orchestrator era** (`bench/orchestrator/`) is legacy
batch scaffolding that predates the server; the server reuses pieces of it
(agent adapters, runners) but new work goes in `bench/server/`.

## Top-level layout

```
bench/
  server/              HTTP/MCP service: session lifecycle + eval + storage + UI
  orchestrator/        Legacy pre-server orchestrator (agent adapters, runners)
  client/              MCP/REST client adapters for driving sessions externally
  tasks/               Task definitions (JSON) — layer1, layer2/{adversarial,strategy}
  personas/            Student persona profiles (JSON)
  data/                Reference trades, market data, LEAN universe, HF cache
  reference/           Hand-written C# algorithms + signal/summary fixtures
  reference_generator/ LEAN reference-trade generation pipeline
  evaluation/          Legacy eval scripts; new eval lives in server/eval/
  docker/              Sandbox image config + shared lean_config.py
  mcp_servers/         Third-party MCP server stubs
  scripts/             Ad-hoc batch utilities
  tests/               Pytest suite (api/, integration/, unit/, top-level)
docs/                  Architecture + design notes (this file)
vercel-frontend/       Vercel-hosted UI stub; main UI lives in bench/server/web/
```

## Server entrypoints

`bench/server/__main__.py` parses `--docker / --eval-model / --auto-eval`
and calls `bench/server/api/http_app.py::create_app(use_docker, bench_root,
eval_model, auto_eval)`. The factory returns a Starlette app that mounts:

- REST routes under `/session/*` (register, start, send, evaluate, scores, results)
- MCP StreamableHTTP transport at `/mcp`
- UI routes from `bench/server/web/ui_app.py` (`/ui/*`, `/client/runs/*`)

`BenchSessionManager` (`http_app.py`) owns the lifespan: session sweeper,
orphan-job cleanup, eval cancellation on shutdown.

The MCP tool catalogue + REST permission model live in
`bench/server/api/protocol.py`: phase machine
`UNREGISTERED → REGISTERED → IN_SESSION → COMPLETED` plus per-phase tool
allowlists. `check_permission(phase, tool_name)` enforces transitions at
call time. As of issue #46 slice 3 the catalogue is
`register_session / start_session / send_message / get_background` —
COMPLETED is terminal for the agent (`next_allowed: []`). Evaluation
runs out-of-band on the operator surface
(`POST /ops/session/{sid}/evaluate`, `GET /ops/session/{sid}/results`,
`GET /ops/session/{sid}/scores`), gated by
`Authorization: Bearer <QTB_ADMIN_TOKEN>` (no enforcement when the env
var is unset, for local-dev convenience).

## Session lifecycle

`bench/server/core/session.py::TutoringSession` runs the conversation
loop: each `send_message` advances one turn through the student simulator
and the T/C checker. Terminal state is reached when T/C is fully covered,
the deadline expires (sweeper-driven), or `max_turns` is hit.

`bench/server/api/session_api.py::SessionState` holds the live session in
the API layer:

- `task`, `persona`, `session` (TutoringSession), `proxy`, `container_manager`
- `_save_results()` calls `result_writer.save_run_state()` to write the
  bundle when the session reaches COMPLETED. Nothing else fires — scoring
  runs out-of-band (issue #46 slice 4 removed the in-session eval
  thread, the `_eval_*` state machine, and the `auto_eval` flag).
- `restore_from_storage()` rebuilds a `COMPLETED` SessionState from
  `run_state.json` so the operator surface can read disposed sessions.
- `get_run_results()` / `get_eval_scores()` are thin disk readers
  (`run_state.json` and the sibling `evaluations/server/...` tree); they
  back the `/ops/session/{sid}/...` REST routes.

## Storage layer (`bench/server/storage/`)

| File | Role |
|------|------|
| `result_writer.py::save_run_state` | Writes `run_state.json` + `run_state.md`, copies workspace to `agent_files/`, then writes `manifest.json` via `bundle.write_manifest`. |
| `bundle.py` | Bundle contract: `BUNDLE_SCHEMA_VERSION`, `write_manifest`, `load_bundle → LoadedBundle`. Owns the producer/consumer interface. |
| `BUNDLE_SCHEMA.md` | Spec for the on-disk bundle: layout, `manifest.json` shape, `run_state.json` fields the evaluator reads. |
| `eval_writer.py` | Owns the shared report writers (`_save_reports`, `_collect_eval_errors`) the evaluator imports back. The pre-slice-4 in-session compat shim is gone — there is no in-session caller. |
| `format_validator.py` | Validates `run_state.json` shape before persistence. |

Bundle layout on disk:

```
results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:8]}/
    manifest.json      (bundle contract + schema version)
    run_state.json     (conversation + tool_logs + metadata)
    run_state.md       (human-readable rendering)
    agent_files/       (workspace snapshot)
```

Evaluator output lives in a sibling tree:

```
evaluations/server/{task_id}/{persona_id}/{session_id[:8]}/{eval_run_id}/
    eval_meta.json     (scores + run config)
    scores.md
    trace.md
    cost.md
```

`server.evaluator.paths.find_latest_eval_dir` is the single resolver and
reads only that tree. Bundles must carry `manifest.json` (any bundle
written since slice 1 does); `score_bundle` rejects bundles that lack
one. The legacy in-bundle `evaluations/` fallback was removed in slice 5.

## Evaluator driver (`bench/server/evaluator/`)

The producer/consumer split introduces a standalone driver that reads
bundles and writes to the sibling tree:

| File | Role |
|------|------|
| `single.py::score_bundle` | Load a bundle, call `evaluate_task`, compute overall score, write reports + `eval_meta.json` to `evaluations/server/.../`. |
| `paths.py` | `eval_run_dir`, `new_eval_run_id`, `find_latest_eval_dir`, `list_eval_history` — single source of truth for eval output locations. |
| `batch.py::run_campaign` | Fan `score_bundle` over many bundles with `ThreadPoolExecutor` dispatch, idempotency via `config_hash`, failure isolation, and a campaign-level summary at `evaluations/campaigns/{campaign_id}/summary.json`. |
| `config_hash.py` | Deterministic 16-char hash over `(judge, eval_mode, tutor_dims, rubric_version, formula_version)`; stamped into `eval_meta.json` so `--skip-scored` can recognise prior runs. |
| `__main__.py` | CLI with two modes: single-bundle (`--bundle <path>`) and batch (`--all-pending`, `--session`, `--task-id`, `--persona`, `--bundles-from`, `--concurrency`, `--skip-scored`/`--force`, `--dry-run`). |

`score_bundle` is the single scoring entry point. The operator REST
handler (`http_app.ops_evaluate`) calls it synchronously via
`asyncio.to_thread`; the CLI calls it directly. All readers (session
API `get_eval_scores`, archived-session REST, UI indexer) go through
`paths.find_latest_eval_dir` so the on-disk layout is decided in one
place. Campaigns write to `evaluations/campaigns/{campaign_id}/summary.json`
(sibling to the per-session tree) for audit.

## Evaluation pipeline (`bench/server/eval/`)

`pipeline.py::evaluate_task(task, persona, workspace_path, conversation,
tool_logs, distractor_names, bench_root, eval_model, ...)` is the
single entry point. It runs three components in parallel via
`ThreadPoolExecutor(max_workers=3)`:

1. **Result Judge** — `ewan_eval/result_judge.py`: LLM scores the
   final result vs. the task's expected outcome.
2. **Process metrics** — `ewan_eval/process_metrics.py` aggregated to
   `quant_process` (uses `tool_usage` + reasoning-quality LLM calls).
3. **Tutor 7D rubric** — `ewan_eval/tutor_conv_geval.py`: per-dimension
   LLM scoring (D1–D7) against persona-specific rubrics.

Around that:

- **Programmatic Quant Result** — runs `task.ground_truth.quant_validation.eval_script`
  (a per-task Python script under `bench/tasks/.../eval_*.py`).
- **Code-execution QR** — `code_eval.py::evaluate_code_combined` runs the
  agent's code (LEAN backtest if applicable) and diffs trades vs. reference.
- **QR blending** — sigmoid-dampened weighted average of programmatic +
  code-eval + LLM-judge scores; weights shift toward the LLM judge as
  programmatic/judge divergence grows (`pipeline.py` lines 343–367).
- **Tool usage** — `ewan_eval/tool_usage.py`: scored against expected /
  convenient / distractor tool lists declared on the task.

`scoring.py::compute_task_score` blends the per-component scores into the
overall task score (default weights: 0.70 quant agent, 0.30 tutor; quant
agent splits 0.50 result + 0.50 process). `eval_helpers.py` shapes the
eval-results dict for report generation. `enrichment.py` /
`trace_utils.py` build the tool-enriched conversation views the LLM
judges consume.

`reference_store.py` loads ground-truth references from
`bench/data/reference/` (per-task trade JSON). Reports are rendered by
`reports/{score_report,trace_report,cost_report}.py`.

## Tasks, personas, data

Tasks live under `bench/tasks/layer*/...`. Each task is a JSON document
with fields that map to `QuantTutorTask`: `task_id`, `persona_ids`,
`student_openings`, `environment` (sandbox image, data files, available
docs, MCP tools, distractors), `ground_truth` (`expected_outcome`,
`quant_validation.eval_script`, `termination_criteria`), `max_turns`,
`timeout_minutes`, `category`, `difficulty`, `requires_code`.

Personas live at `bench/personas/{id}.json` and map to `StudentPersona`:
`knowledge_level`, `known_concepts` / `unknown_concepts` (finance + code),
`emotional_profile`, `behavioral_rules`. Persona drives both the student
simulator and the persona-specific tutor rubrics.

`bench/data/` holds the ground-truth and dataset surface:

- `data/reference/` — per-task reference trades / signals / summaries
- `data/raw/i-series/` — raw market data sources (tier1 daily, tier2 hourly,
  tier3 minute, funding rates)
- `data/lean/` — LEAN-formatted zip data + custom symbol-properties +
  market-hours overrides (auto-mounted into the sandbox to override
  LEAN's built-in DBs)
- `data/universe_full.json`, `data/symbol_dates.json`,
  `data/lean_universe.json` — universe + listing-date metadata

## Reference generator

`bench/reference_generator/generate_lean_reference.py` is the entrypoint.
It locates the C# algorithm under `bench/reference/{Implementation,
end_to_end,debug}/algorithms/`, builds it into a DLL inside the
`quantconnect/lean:latest` Docker image, runs the backtest with the
shared `bench/docker/lean_config.py::apply_session_overrides` patcher,
parses LEAN's lowercase-keyed result JSON, and writes
`bench/data/reference/{task_id}_reference_trades.json`. Multi-run tasks
(I06 sweep, I08/I09/I10 grid) use `TASK_RUN_CONFIGS` + a
`ThreadPoolExecutor`.

A second runner — `bench/docker/run_backtest.sh` — executes the *same*
LEAN image during live sessions but invokes `dotnet run --no-build` from
a different path. Both runners share `lean_config.py` for config patching;
divergence between the two paths caused issue #33.

## Web UI

`bench/server/web/ui_app.py` mounts `/ui/*` (admin) and `/client/runs/*`
(client-facing). Read-only result browsing, task catalog, and run
management; static assets served via `NoCacheStaticFiles` so dashboards
pick up rebuilds immediately. `ui_indexer.py` walks the results tree to
build the listing index. Token auth (bearer) gates write endpoints.

## Tests (`bench/tests/`)

`conftest.py` is the shared mocking surface: stubs the `mcp` SDK, replaces
`require_ewan_model` / `resolve_ewan_model` with a `FakeLLMModel`, swaps
`ensure_data` for a tmpdir-backed `DataPaths`, and provides an `app`
fixture that boots `create_app(use_docker=False, auto_eval=False)`. Tests
run without network, API keys, or Docker.

Notable suites: session lifecycle (`test_server_session_runtime.py`),
phase machine (`test_run_state_transitions.py`), bundle contract
(`test_bundle_schema.py`), LEAN code-eval discrimination
(`test_lean_code_eval*.py`), REST transport jobs
(`test_rest_transport_jobs.py`), student-simulator determinism
(`test_student_determinism_v2.py`).

## In-progress: producer/consumer split (issue #46)

Today the session both writes the bundle and runs evaluation in an
in-session background thread. That couples three concerns — what the agent
did, when we grade it, what we grade against — and has bitten us
(forgotten `request_evaluation` calls, judge crashes blocking sessions,
re-scoring requiring re-tutoring). The split moves to:

- **Producer**: session writes a self-contained bundle and stops.
- **Consumer**: separate evaluator process (`bench/server/evaluator/`)
  reads bundles and writes scores to a sibling
  `evaluations/server/{task_id}/{persona_id}/{session_id[:8]}/{eval_run_id}/`
  tree.

Slice 1 (landed): `bundle.py` + `manifest.json` + `BUNDLE_SCHEMA.md` +
`load_bundle` + smoke tests pin the contract.

Slice 2 (landed): `bench/server/evaluator/` extracted with
`score_bundle` + CLI; output cut over to the
`evaluations/server/...` sibling tree; in-session `run_evaluation` now
delegates to the new driver; readers go through
`paths.find_latest_eval_dir` with legacy-fallback for old runs.

Slice 3 (landed): agent catalogue cut — `request_evaluation` /
`get_results` / `get_scores` removed from MCP `tools/list` and the agent
REST surface. Their REST routes moved to `/ops/session/{sid}/...` with
operator-token auth. COMPLETED is now terminal from the agent's
perspective (`next_allowed: []`). Internal-only break — no external
agents existed at the time.

Slice 4 (landed): in-session eval machinery deleted. `_eval_lock`,
`_eval_status`, `_eval_results`, `_eval_error`, `_eval_mode`,
`_tutor_dims`, `_run_evaluation` thread, the `auto_eval` flag (CLI +
manager + state), and the `eval_writer.run_evaluation` compat shim are
all gone. `ops_evaluate` calls `score_bundle` synchronously; `get_eval_scores`
re-reads the bundle's eval tree on every call. Restore-from-storage no
longer hydrates eval state.

Slice 5 (landed): legacy in-bundle fallback removed from
`paths.find_latest_eval_dir` / `list_eval_history`, plus the manifest
backfill in `score_bundle`. Bundle reads/writes are now exclusively
in the sibling `evaluations/server/...` tree, and bundles must carry
`manifest.json` — `load_bundle` is the single source of truth for
"what's in a bundle". The producer/consumer split is complete.

Issue #47 slice P1 (landed): `batch.py` + `config_hash.py` layered on
top of `score_bundle` with idempotent dispatch, failure isolation, and
a campaign summary.

Issue #47 slice P2 (landed): budget gating, checkpoint/resume, and
time-window filters.

- `--max-cost-usd` runs the worker pool in lazy-dispatch mode: pull the
  next bundle only when below the cap, mark the rest skipped with
  `error="budget_exceeded"` so the summary still audits them.
- `--resume CAMPAIGN_ID` reuses the original campaign dir and replays
  `checkpoint.jsonl` to skip bundles already scored. Every outcome is
  appended to that checkpoint as it completes so an interrupted run
  has something to restart from.
- `--since` / `--until` filter bundles by manifest `created_at`;
  unparseable timestamps pass so legacy producers don't silently drop.
- Per-bundle cost comes from the `_eval_cost` signals the pipeline
  already emits (tutor rubric + process metrics + result judge). The
  total lands in `CampaignSummary.cost_usd` and is stamped into
  `eval_meta.json` for downstream audit.
