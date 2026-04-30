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

Each `score_n/score.json` export carries `judge_reliability` metadata linking
the evaluation report to the selected validated judge-validation run, its
report paths, and the current-model match flag.

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
`bench/server/eval/judge_reliability_reference.json`.

## Human Review Console

`bench/server/web/review_store.py` exposes archived session bundles for human
inspection through `/ui/review/*`. The Vercel-facing shell serves `/review` and
`/review/{bundle_id}` as SPA routes backed by the existing GitHub OAuth cookie.
Any authenticated GitHub reviewer can inspect the task spec, transcript, tutor
tool log, workspace tree, and judge evaluation for a completed bundle.

The isolated frontend treats Human Review as the archive-facing session view:
completed runs from Session Flow link to `/review/{session_id}`. New Run hands
external agents a copyable REST prompt with a public task label, API key, browser
skill URL, and raw Markdown skill URL for `curl`.

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
`bench/server/web/review_store.py` builds the review read model from the same
result indexer and writes per-reviewer opinion files under
`bench/experiments/human_review/`.

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
