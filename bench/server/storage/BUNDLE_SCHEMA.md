# Run bundle schema

Self-contained on-disk artifact a tutoring session produces. Carries every
input the offline evaluator needs to score the run later, without re-running
the session and without touching the live server.

Tracked by `BUNDLE_SCHEMA_VERSION` in `bundle.py` (current: **1.0.0**).
Bump the major when removing fields, renaming files, or changing field
semantics; bump the minor when adding optional fields a 1.x consumer can
ignore.

## Layout

```
results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:8]}/
    manifest.json       # contract + schema version + artifact list
    run_state.json      # conversation + tool_logs + run metadata
    run_state.md        # human-readable rendering (optional for evaluator)
    agent_files/        # workspace snapshot at session completion
```

Evaluator output lives in a parallel tree under
`evaluations/server/{task_id}/{persona_id}/{session_id[:8]}/{eval_run_id}/`.
A raw bundle directory contains zero evaluator-produced files. The in-bundle
`evaluations/` subdirectory the legacy in-session writer used is no longer
read or written — slice 5 of the migration removed the fallback.

## `manifest.json`

```json
{
  "bundle_schema_version": "1.0.0",
  "created_at": "2026-04-21T12:34:56.789012",
  "task_id": "I01",
  "persona_id": "fullstack_practitioner",
  "session_id": "3f0c5ebae6f649ce891ea1004a51f983",
  "run_id": "run_20260421_123456_3f0c5eba",
  "session_status": "completed",
  "termination_reason": "tc_complete",
  "artifacts": {
    "run_state": "run_state.json",
    "run_state_md": "run_state.md",
    "agent_files": "agent_files/"
  }
}
```

`artifacts` lists the files consumers may rely on. Anything else inside the
bundle directory is implementation detail and may move without a version bump.

## `run_state.json` — fields the evaluator reads

Required for `server.eval.pipeline.evaluate_task`:

| Field              | Type           | Notes                                   |
|--------------------|----------------|-----------------------------------------|
| `task_id`          | str            | Looks up the `QuantTutorTask`           |
| `persona_id`       | str            | Looks up the `StudentPersona`           |
| `session_id`       | str            | Identity                                |
| `conversation`     | list[dict]     | `[{role, content}, ...]`                |
| `tool_logs`        | list[dict]     | `ToolCallLog`-shaped dicts              |
| `distractor_names` | list[str]      | Tool names not relevant to the task     |

Producer-side metadata (carried for traceability, not consumed by
`evaluate_task`):

`run_id`, `public_task_label`, `timestamp`, `session_status`,
`termination_reason`, `workspace_files`, `simulator_cost`, `tc_checker_cost`,
`tc_coverage`, `tc_debug_history`, `artifact_debug_history`,
`duration_seconds`, `key_results`, `trace_summary`, `step_count`,
`format_validation`, `evaluation_status`.

`evaluation_status` reflects the legacy in-session evaluator's progress and
will become a stale field once the split lands. Consumers reading post-split
should ignore it and look at the sibling `evaluations/server/...` tree
instead.

## `agent_files/`

The full workspace directory at the moment the session reached terminal
state, copied via `shutil.copytree`. Path on disk is exposed to the
evaluator as `workspace_path` for code-eval and trial-manifest reads.

## Loading

```python
from server.storage.bundle import load_bundle

b = load_bundle("results/server/I01/fullstack_practitioner/20260421_.../")
# b.task_id, b.persona_id, b.conversation, b.tool_logs,
# b.distractor_names, b.workspace_path are ready to feed evaluate_task.
```

`load_bundle` raises:

- `FileNotFoundError` — the bundle directory, `manifest.json`,
  `run_state.json`, or any artifact the manifest declares is missing.
- `ValueError` — the manifest's major schema version is not supported by
  this build, or the manifest is missing `bundle_schema_version`.

`tool_logs` are rehydrated from JSON dicts back to `ToolCallLog` dataclass
instances (defined in `server.core.proxy`) so downstream evaluators —
which use attribute access (`log.name`, `log.args`, `log.result`,
`log.success`) — see the same shape they get from a live in-session
`proxy.get_logs()` call.
