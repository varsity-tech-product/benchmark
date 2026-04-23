# QuantTutorBench Architecture

This is the current shared architecture after the dev-to-ewan merge plan.
When code and docs disagree, trust the code and update this file in the same
change.

The merge contract for the evaluation boundary lives at
`bench/*MD/v6.0/eval/dev_merge_server_side_eval_contract.md`.

## Repo Map

```text
bench/
  server/              HTTP/MCP service, run control, storage, scoring, UI
  client/              External client adapters for MCP and REST
  orchestrator/        Legacy pre-server batch scaffolding
  tasks/               Task definitions
  personas/            Student persona profiles
  data/                Market/reference data
  experiments/         Validation experiments and generated report pipelines
  tests/               Unit, API, and integration tests
docs/                  Architecture and agent guidance
vercel-frontend/       Vercel-hosted frontend shell
```

`bench/server/` is the active path. `bench/orchestrator/` is legacy and should
not be used as a new dependency for evaluation orchestration.

## Server Entrypoint

`bench/server/__main__.py` parses server flags and calls
`server.api.http_app.create_app(...)`. The Starlette app exposes:

- MCP at `/mcp`
- client REST under `/session/*`
- operator REST under `/ops/*`
- UI/client run routes from `bench/server/web/ui_app.py`

The `BenchSessionManager` in `http_app.py` owns live sessions, the run store,
quota checks, background tool jobs, cleanup, and restore-from-storage.

## Permission Boundary

Clients can create or claim runs, register/start sessions, send tutor messages,
call allowed workspace tools, and read exported result or score state.

Clients cannot trigger scoring:

- no MCP `request_evaluation`, `get_results`, or `get_scores`
- no public `POST /session/{sid}/evaluate`
- no scoring through `POST /session/{sid}/tool/{name}`

Scoring is server/operator-owned:

- `POST /ops/session/{sid}/evaluate`
- `GET /ops/session/{sid}/results`
- `GET /ops/session/{sid}/scores`

The `/ops/*` surface is gated by the admin-token mechanism in
`server.web.ui_app`. Client read endpoints stay export-scoped and, when run
auth is enabled, require the owning run token.

## Session Lifecycle

`server.api.session_api.SessionState` owns one tutoring session:

1. `register_session` loads task/persona and prepares runtime state.
2. `start_session` returns the student opening and enters `in_session`.
3. `send_message` advances the student simulator and tool trace.
4. Terminal status persists a result bundle and enters `completed`.
5. `completed` is terminal for MCP/client tools.

`restore_from_storage()` reconstructs enough completed-session state from
`run_state.json` to read results, read scores, or run operator scoring without
restarting the tutoring runtime.

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

`server.storage.result_writer.save_run_state()` writes `run_state.json`,
`.session_id`, and the workspace snapshot. `server.storage.score_store` owns
`evaluations/index.json` and append-only `score_n` directories.

## Evaluation Pipeline

The active evaluation pipeline is under `bench/server/eval/`:

- `contracts/` validates scoring requests and output shape.
- `core/coordinator.py` is the scoring coordinator used by REST and CLI.
- `core/preflight.py` blocks non-computable tracks before LLM judging.
- `tracks/qr.py`, `tracks/qp.py`, and `tracks/tutor.py` run track scoring.
- `judges/` contains LLM-backed result/process/tutor judges.
- `programmatic/` contains code, process, and tool-usage evaluators.
- `inputs/` builds task/persona/conversation/reference context.
- `rubrics/` stores judge rubrics plus `rubric_registry.json`, the first-class
  registry of judged dimensions and stable rubric IDs.

LLM judge prompts are built through `judges/runtime/conv_geval.py`. Prompt and
output records include rubric ID/version, prompt template version, judge model,
judge temperature, transcript source, dimension, output schema, context fields,
and run timestamp metadata.

The operator REST endpoint calls `SessionState.request_evaluation()`, which
allocates a `score_n` run and delegates to `EvalCoordinator`. The CLI entrypoint
is:

```bash
python -m server.scripts.eval_single run --session <session_id> --mode tutor
python -m server.scripts.eval_single get --session <session_id> --history
python -m server.scripts.eval_single list
```

If a batch driver is needed, it should be a thin wrapper around
`EvalCoordinator` and `score_store`, not a second evaluator architecture.

## Judge Validation

`bench/experiments/judge_validation/` is the automated judge reliability gate for
external-agent scoring. It owns a fixed pilot corpus, prompt rendering, repeated
same-prompt judge runs, prompt-format variants, one-factor sensitivity cases,
adversarial-pair ranking checks, human label artifacts, Google Form CSV
conversion, bilingual Google Form blueprint export, blind reviewer packet
export, private sample-ID mapping, and Markdown/HTML/JSON reliability reports.
The automated report tracks mean absolute score delta, within-one score rate,
pass/fail flip rate, prompt-format score deltas, sensitivity pass rate,
adversarial ranking pass rate, evidence/reason coverage, lightweight
explanation consistency, raw disagreement examples, and residual risks. The
human-alignment report joins `judge_runs.json` with `human_labels.json` and
reports exact agreement, within-one agreement, mean absolute delta versus human
labels, pass/fail agreement, large disagreement examples, and bias slices by
dimension, category, persona, and transcript source.

## Public Reads

`GET /session/{sid}/results` returns only export-scope run fields such as
session id, run id, public task label, persona id, terminal status,
conversation, key results, trace summary, and workspace file names. It must not
return raw tool logs, owner internals, debug histories, judge prompts, raw judge
responses, evaluator traces, or cost internals.

`GET /session/{sid}/scores` is read-only. It returns pending/running/completed
score state from `score_store` and strips private score/cost internals.

Operator reads under `/ops/session/{sid}/results` and
`/ops/session/{sid}/scores` return full server-side payloads for audit and
debugging.

## Web UI

`bench/server/web/ui_app.py` mounts admin/client UI routes.
`bench/server/web/ui_indexer.py` walks the canonical result tree and reads
in-bundle `evaluations/index.json` plus `score_n/score.json` and `cost.json`.
It also applies owner filtering for private/org/admin views.

## Tests

Important contract tests:

```bash
python -m pytest bench/tests/api/test_permissions.py
python -m pytest bench/tests/api/test_eval_flow.py
python -m pytest bench/tests/unit/test_eval_architecture_contracts.py
python -m pytest bench/tests/test_server_web_ui.py
python -m pytest bench/tests/test_run_control_token.py bench/tests/test_run_owner_filtering.py
```

Run broader REST/run-owner/auth tests when changing client run control,
Vercel-facing routes, or quota behavior.
