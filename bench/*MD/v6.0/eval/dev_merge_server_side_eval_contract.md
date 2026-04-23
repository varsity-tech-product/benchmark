# Dev Merge Contract: Server-Side Evaluation Boundary

This document is the merge contract for bringing the latest `origin/dev` into
`ewan` and turning the result into the next shared `dev` baseline.

## Goal

The merged branch must become the new division-of-work starting point:

- Ewan can run VPS tasks and scoring validation from the new pipeline.
- Rick can build the Vercel frontend and VPS server architecture on top of the
  same storage and permission contract.

## Non-Negotiable Boundaries

1. Clients may run tasks, request exported results, and read scores.
2. Clients must not trigger scoring through MCP or public REST.
3. Server/operator code decides when scoring runs and returns score state.
4. Storage uses the new in-bundle pipeline layout.
5. No `session_id[:8]` compatibility path is allowed for new scoring.
6. No `run_state.md` is written or required.
7. `manifest.json` and sibling `evaluations/server/...` are not canonical.

## Public Client API

Allowed:

- `POST /session/register`
- `POST /session/{sid}/start`
- `POST /session/{sid}/send`
- `POST /session/{sid}/tool/{name}`
- `GET /session/{sid}/results`
- `GET /session/{sid}/scores`

Forbidden:

- `POST /session/{sid}/evaluate`
- MCP `request_evaluation`
- MCP `get_results`
- MCP `get_scores`

Expected behavior:

- `POST /session/{sid}/evaluate` returns `404`.
- MCP `list_tools` never advertises eval tools.
- `POST /session/{sid}/tool/request_evaluation` must not trigger scoring.

## Operator API

Server/operator-only endpoints:

- `POST /ops/session/{sid}/evaluate`
- `GET /ops/session/{sid}/results`
- `GET /ops/session/{sid}/scores`

All `/ops/*` endpoints require server/admin authorization (`QTB_ADMIN_TOKEN` or
the equivalent admin mechanism from `dev`). These endpoints are not part of the
ordinary client contract.

## Public Result Payload

`GET /session/{sid}/results` returns only export-scope data:

- `session_id`
- `run_id`
- `task_id`
- `public_task_label`
- `persona_id`
- `session_status`
- `termination_reason`
- `timestamp`
- `duration_seconds`
- `conversation`
- `key_results`
- `trace_summary`
- `workspace_files`

It must not return raw `tool_logs`, internal debug histories, owner internals,
judge prompts, raw judge responses, evaluator traces, or cost internals.

## Public Score Payload

`GET /session/{sid}/scores` is read-only and never triggers scoring.

Before scoring:

```json
{"session_id": "...", "status": "pending", "scores": []}
```

While scoring:

```json
{"session_id": "...", "status": "running", "score_id": "score_1"}
```

After scoring:

```json
{
  "session_id": "...",
  "status": "completed",
  "score_id": "score_1",
  "eval_mode": "tutor",
  "scores": {
    "overall_score": 0.82,
    "tutor_scores": {}
  }
}
```

Public score payloads must not expose `cost.json`, judge prompts, raw LLM
responses, preflight debug internals, full evaluator traces, or server stack
traces.

## Canonical Storage Layout

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
    score_2/
      score.json
      cost.json
```

Forbidden as canonical storage:

```text
run_state.md
manifest.json
evaluations/server/{task_id}/{persona_id}/{session_id[:8]}/{eval_run_id}/
```

## Merge Decisions

Take from `origin/dev`:

- Auth, audit, quota, run ownership, run control token, owner filtering.
- Vercel/frontend and REST skill work.
- Agent lifecycle boundary: `COMPLETED` is terminal for MCP/client tools.
- `/ops/session/{sid}/evaluate|results|scores` as the server/operator surface.

Keep from `ewan`:

- `server/eval/contracts/*`
- `server/eval/core/*`
- `server/eval/inputs/*`
- `server/eval/judges/*`
- `server/eval/programmatic/*`
- `server/eval/tracks/*`
- `server/storage/score_store.py`
- `server/scripts/eval_single.py`
- `server/storage/eval_writer.py`
- `server/storage/result_writer.py`
- in-bundle UI score indexing

Reject as canonical:

- `server/evaluator/*`
- `server/storage/bundle.py`
- `server/storage/BUNDLE_SCHEMA.md`
- `server.evaluator.single.score_bundle`
- sibling `evaluations/server/...`
- manifest-based evaluator tests

If a batch/operator driver is needed, it must be a thin wrapper around
`EvalCoordinator` and `score_store`, not a second evaluator architecture.

## Implementation Order

1. Fix current `ewan` so public REST and MCP cannot trigger scoring.
2. Add `/ops/session/{sid}/evaluate` as the only REST scoring trigger and wire it
   to the new score store pipeline.
3. Commit that checkpoint.
4. Merge latest `origin/dev`.
5. Resolve conflicts according to this contract.
6. Run permission, score, UI, and run-owner tests.

## Verification

Required checks after merge:

```bash
python -m pytest bench/tests/api/test_permissions.py
python -m pytest bench/tests/api/test_eval_flow.py
python -m pytest bench/tests/unit/test_eval_architecture_contracts.py
python -m pytest bench/tests/test_server_web_ui.py
python -m pytest bench/tests/test_run_control_token.py bench/tests/test_run_owner_filtering.py
```

Behavior checks:

- Public `POST /session/{sid}/evaluate` returns `404`.
- Public `GET /session/{sid}/scores` reads score state without scoring.
- Operator `POST /ops/session/{sid}/evaluate` starts a score run.
- Score output lands under in-bundle `evaluations/score_n`.
