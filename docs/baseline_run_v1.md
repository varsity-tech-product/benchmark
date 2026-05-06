# Baseline Run v1

Issue #185 defines the first Impl A baseline run for paper validation data.
The run is a reproducible HTTP-driven sweep with generated Bundle v1 alpha
artifacts and tracked aggregate summary output.

## Matrix

| Axis | Value |
|---|---|
| Task corpus | 142 v3 tasks under `bench/tasks/L0`, `bench/tasks/L1`, and `bench/tasks/L2` |
| HTTP-runnable slice | 19 L2 multi-turn tasks exposed through `/client/runs/start` |
| Agent profiles | `claude_haiku_4_5`, `claude_sonnet_4_6` |
| Conditions | `agent`, `direct_answer_baseline` |
| Judge mode | `full` |
| Judge model | `anthropic/claude-haiku-4.5` |
| Judge temperature | `0.0` |
| Protocol | MCP by default; REST is available through `--protocol rest` |
| Storage root | `bench/data/baseline_run_v1/` |

Planned full matrix size: `142 * 2 agents * 2 conditions = 568` cells.
Current HTTP-runnable matrix size: `19 * 2 agents * 2 conditions = 76` cells.

## Storage Policy

`bench/data/` is ignored because run artifacts are large. The baseline driver
writes generated files under `bench/data/baseline_run_v1/`:

| Path | Git policy | Purpose |
|---|---|---|
| `manifest.json` | generated | Full matrix cell manifest |
| `runs.jsonl` | generated | One append-only record per attempted cell |
| `bundles/**/bundle.json` | generated | Bundle v1 alpha exports |
| `client_traces/` | generated | Client-side traces from `bench/client` |
| `summary.json` | tracked | Aggregate run state and paper table input |

## Commands

Generate the matrix manifest and pending summary:

```bash
.venv/bin/python bench/scripts/baseline_run.py plan
```

Run the current HTTP-runnable L2 slice on dedicated infra:

```bash
export QTB_BASELINE_SERVER=http://127.0.0.1:8000
export QTB_CLIENT_API_KEY=<client-api-key>
export OPENROUTER_API_KEY=<openrouter-api-key>

.venv/bin/python bench/scripts/baseline_run.py run \
  --workers 2 \
  --protocol mcp \
  --server-results-root bench/results/server
```

The command keeps the full 142-task denominator in `summary.json`; the runner
executes the HTTP-runnable L2 cells in that matrix.

Regenerate summary tables after a run:

```bash
.venv/bin/python bench/scripts/baseline_run.py summarize
```

Validate exported bundles:

```bash
.venv/bin/python bench/scripts/baseline_run.py validate
```

## Reproducibility Notes

The driver records `cell_id`, task metadata, agent profile, condition,
run/session ids, score summary, bundle path, token/cost totals, timestamps, and
errors in `runs.jsonl`. Re-running `run` skips completed cells by default and
`--force` retries them.

Bundle export uses local server results under `--server-results-root`. Run the
driver on the same machine as the server, or sync `bench/results/server` before
calling `summarize` and `validate`.

L0/L1 tasks are included in the manifest and summary denominator. Their HTTP
execution is queued behind run-token catalog support for single-turn tasks.
