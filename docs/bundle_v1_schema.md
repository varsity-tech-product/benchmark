# Bundle v1 Schema

Status: Stage 1 v0 alpha
Current `schema_version`: `1.0.0-alpha`
Canonical schema file: `bench/eval/contracts/bundle_v1_alpha.schema.json`
Validator: `python -m eval.contracts.bundle_schema <bundle.json>`

## Goal

Bundle v1 is the public artifact contract for benchmark runs. It must carry
conversation, tool traces, arbitrary task artifacts, and workspace evidence for
reference-harness tutoring runs, FinanceBench-style QA items, and factor-mining
outputs.

## Legacy Field Audit

Observed `run_state.json` fields from `bench/results/server/...`:

| Field | Destination |
|---|---|
| `run_id` | `artifacts.quanttutor.run_id` |
| `public_task_label` | `artifacts.quanttutor.public_task_label` |
| `task_id` | envelope `task_id` |
| `session_id` | envelope `bundle_id`, `artifacts.quanttutor.session_id` |
| `persona_id` | `artifacts.quanttutor.persona_id` |
| `timestamp` | `timestamps.created_at` |
| `session_status` | `artifacts.quanttutor.session_status` |
| `termination_reason` | `artifacts.quanttutor.termination_reason` |
| `conversation[].role/content/ts` | `messages[]` |
| `tool_logs[].name/call_id/args/result/timestamp/duration_ms/success/turn_index` | `tool_calls[]` |
| `distractor_names` | `artifacts.quanttutor.distractor_names` |
| `duration_seconds` | `timestamps.duration_seconds`, `telemetry.duration_seconds` |
| `evaluation_status` | `artifacts.quanttutor.evaluation_status` |
| `format_validation` | `artifacts.quanttutor.format_validation` |
| `key_results` | `artifacts.quanttutor.key_results` |
| `simulator_cost` | `telemetry.simulator_cost` |
| `step_count` | `telemetry.step_count` |
| `tc_checker_cost` | `telemetry.tc_checker_cost` |
| `tc_coverage` | `artifacts.quanttutor.tc_coverage` |
| `tc_debug_history` | `artifacts.quanttutor.tc_debug_history` |
| `artifact_debug_history` | `artifacts.quanttutor.artifact_debug_history` |
| `trace_summary` | `artifacts.quanttutor.trace_summary` |
| `workspace_files` | `artifacts.quanttutor.workspace_files` |

Observed `run_state.json` fields from `bench/results/run-single/...`:

| Field | Destination |
|---|---|
| `task_id`, `persona_id`, `conversation`, `tool_logs`, `distractor_names`, `duration_seconds`, `simulator_cost`, `step_count`, `trace_summary`, `workspace_files` | same destinations as server runs |
| `agent_cost` | `telemetry.agent_cost` |
| `thinking_trace` | `artifacts.quanttutor.thinking_trace` |

Observed `manifest.json` fields from older server bundles:

| Field | Destination |
|---|---|
| `bundle_schema_version` | superseded by envelope `schema_version` |
| `created_at` | `timestamps.created_at` |
| `task_id`, `session_id`, `persona_id`, `run_id`, `session_status`, `termination_reason` | same destinations as server runs |
| `artifacts.agent_files`, `artifacts.run_state`, `artifacts.run_state_md` | `artifacts.quanttutor.legacy_manifest` when migrated later |

Observed score export shape from `eval.storage.score_store` and
`eval.contracts.output`:

| Field | Destination |
|---|---|
| `version` | score artifact-local version |
| `score_id`, `score_status`, `created_at`, `completed_at`, `eval_model`, `eval_mode`, `duration_seconds` | score artifact metadata under `artifacts.<producer>.score` |
| `overall_score`, `qr`, `qp`, `blocking_missing`, `interrupted`, `error`, `preflight` | score artifact payload |
| `judge_reliability` | score artifact payload |
| `cost.json.version`, `eval_cost_usd`, `eval_cost_by_track`, `eval_cost_by_model`, `eval_cost_by_stage_model` | score artifact cost payload |

Observed workspace tree fields:

| Field | Destination |
|---|---|
| relative file path | `workspace.files[].path` |
| sha256 digest | `workspace.files[].sha256` |
| byte length | `workspace.files[].size_bytes` |
| file type hints | `workspace.files[].metadata` |

## Generic Envelope

Top-level fields in `1.0.0-alpha`:

| Field | Type | Semantics |
|---|---|---|
| `bundle_id` | string | Stable bundle identifier. Reference backfill uses `session_id`. |
| `schema_version` | string | Current alpha stamp: `1.0.0-alpha`. |
| `task_id` | string | Producer task identifier. |
| `timestamps` | object | `created_at`, `started_at`, `completed_at`, `duration_seconds`. |
| `agent_id` | string | Producer/harness/model identifier. |
| `sandbox_digest` | object | Sandbox image URI, image digest when present, resource limits, data mounts, and runtime policy metadata. |
| `telemetry` | object | Generic counters, costs, and timing metadata. |
| `messages` | array | Generic conversation messages. |
| `tool_calls` | array | Generic tool calls with arbitrary args/result JSON. |
| `artifacts` | object | Producer-specific payloads keyed by namespace. |
| `workspace` | object | Workspace file tree snapshot. |

## Message Shape

Each message has:

- `message_id`: producer-scoped stable id;
- `role`: producer role string, such as `user` or `assistant`;
- `content`: arbitrary JSON;
- `created_at`: timestamp string;
- `turn_index`: zero-based turn grouping;
- `attachments`: array of producer attachment objects;
- `metadata`: arbitrary producer metadata.

## Tool Call Shape

Each tool call has:

- `tool_call_id`: producer-scoped stable id;
- `tool_name`: tool identifier;
- `args`: arbitrary JSON;
- `result`: arbitrary JSON;
- `created_at`: timestamp string;
- `duration_ms`: numeric duration;
- `success`: boolean outcome;
- `turn_index`: zero-based turn grouping;
- `metadata`: arbitrary producer metadata.

Reference backfill preserves `send_message` entries in `tool_calls` and marks
them with `metadata.conversation_transport = true`.

## Artifacts

`artifacts` is an open object keyed by producer namespace. Current fixtures use:

- `quanttutor`: reference harness metadata, task hash/version, scores, TC data;
- `financebench`: QA pair and score payload;
- `factor_mining`: factor formula, signal series, and IC matrix references.

Score payloads remain producer artifacts during alpha. A task can include
`task_score`, exact-match results, IC metrics, or richer evaluator output without
changing the envelope.

## Workspace Snapshot

`workspace.files[]` records relative file paths, type, sha256, byte size, an
optional `content_ref`, and metadata. The Stage 1 policy is path plus sha256 plus
size. Inline content belongs in namespaced `artifacts` for alpha fixtures. Stage
2 will decide long-term inline payload and external-reference rules.

## TBD Decisions Resolved For Stage 1

| Issue TBD | Stage 1 decision |
|---|---|
| JSON Schema vs Pydantic vs custom DSL | JSON Schema is canonical for public validation. Python dataclasses are the in-repo typed convenience layer. |
| Workspace reference policy | Use relative path + sha256 + size in `workspace.files[]`; keep optional `content_ref` for later external stores. |
| Compatibility fixtures | Ship three schema fixtures: Impl A reference harness, Impl C FinanceBench QA, Impl D factor mining. |
| Score openness | Scores live under namespaced `artifacts`; the envelope requires zero score fields. |

## Migration Placeholder

Stage 2 freeze will define:

- conversion from `1.0.0-alpha` to `1.0.0`;
- supported historical read window;
- deprecation notice cadence;
- score artifact stability contract;
- workspace inline/external content policy.

## Validation

Run focused validation:

```bash
python -m eval.contracts.bundle_schema bench/eval/contracts/fixtures/bundle_v1_alpha/impl_a_ref_harness.json
python -m pytest bench/tests/unit/test_bundle.py bench/tests/unit/test_backfill.py
```
