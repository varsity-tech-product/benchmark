---
name: quanttutorbench-rest-agent
description: "Use when an external AI agent needs to run QuantTutorBench through the REST API only: create or claim a run, register/start a session, read student messages, send tutor replies, call allowed workspace tools, monitor status, and hand off completed runs. Keep guidance task-agnostic and use only public task labels plus client-visible API responses."
---

# QuantTutorBench REST Agent

Use this skill to connect an external tutor agent to QuantTutorBench through REST.
Teach platform mechanics only: authentication, run/session lifecycle, tool calls,
turn handling, monitoring, and completion handoff.

## Neutrality Boundary

Use only state returned by public/client-visible REST responses:

- Public task labels such as `D01`, `X09`, or an operator-provided label.
- `run_id`, `session_id`, `token`, `control_token`, `current_phase`, `next_allowed`.
- `student_message`, optional `background`, live tool schemas, tool results, and terminal status.

Keep these out of agent prompts, student replies, examples, and documentation:

- Hidden task IDs, ground truth, termination criteria, expected tools, convenient tools, distractor labels, judge rubrics, reference traces, and solution paths.
- Task-category heuristics such as tool choices inferred from a label prefix.
- Any suggested answer, strategy, code shape, or analysis direction for a specific task.

Choose actions from the latest `student_message`, the live tool catalog, and the
visible API state.

## Base URL

Use the operator-provided `BASE`. For the public production UI:

```bash
BASE="https://benchmark-liard.vercel.app"
```

Use a JSON-capable HTTP client with long request timeouts. Student simulation and
heavy tools can take minutes. A 900 second client timeout is a practical default.

## Tokens

The platform user generates their REST API key in the UI after GitHub OAuth
login. Use that key only to create runs. Use two run-specific token types after
run creation:

- `api_key`: user API key for `/client/runs/start`. Send as `Authorization: Bearer <api_key>`.
- `token`: run token for `/session/*` requests. Send as `Authorization: Bearer <token>`.
- `control_token`: owner token for `/ui/runs/{run_id}*` monitor/cancel endpoints. Send as `Authorization: Bearer <control_token>`.

Store raw tokens only in memory or secure local runtime state. Log token hints,
run IDs, and session IDs.

## Create Or Claim A Run

### Existing Website Run

When the platform UI gives the agent a run token:

```http
POST /client/runs/claim
Content-Type: application/json

{
  "run_token": "<token>",
  "client": {"name": "external_rest_agent", "version": "1.0"}
}
```

Use the same `token` for the session lifecycle.

### REST One-Step Run

When the agent creates the run itself from a public task label:

```http
POST /client/runs/start
Authorization: Bearer <api_key>
Content-Type: application/json

{
  "task": "<public_task_label>",
  "mode": "agent",
  "client": {"name": "external_rest_agent", "version": "1.0"}
}
```

Expected fields include `run_id`, `token`, `control_token`,
`public_task_label`, and `status`.

## Session Lifecycle

### 1. Register

The server resolves the task from the run token.

```http
POST /session/register
Authorization: Bearer <token>
Content-Type: application/json

{}
```

Use `{}` for persona auto-selection. Include `persona_id` only when the operator
explicitly provides one.

Save `session_id` from the response.

### 2. Start

```http
POST /session/{session_id}/start
Authorization: Bearer <token>
Content-Type: application/json

{}
```

Save the first `student_message`. Also save `background` when present; treat it
as client-visible task context.

### 3. Discover Tools

```http
GET /session/{session_id}/tools
Authorization: Bearer <token>
```

Use the returned `tools[]` schemas as the allowed action surface. Domain tools
are called through:

```http
POST /session/{session_id}/tool/{tool_name}
Authorization: Bearer <token>
Content-Type: application/json

{ "...tool arguments from schema...": "..." }
```

For asynchronous heavy-tool responses with `202`, poll the provided `poll_url`
or:

```http
GET /session/{session_id}/tool/jobs/{job_id}
Authorization: Bearer <token>
```

Continue polling until `status` is `completed` or `failed`.

### 4. Turn Loop

Each turn starts with the latest `student_message`. Compose a fresh tutor reply
from that message and visible context.

```http
POST /session/{session_id}/send
Authorization: Bearer <token>
Content-Type: application/json

{
  "text": "<message delivered to the student>",
  "attachments": ["optional_workspace_path"],
  "reasoning": "optional private turn rationale"
}
```

Use `attachments` for up to three workspace paths that should be shared with the
student. Use `reasoning` for concise private rationale recorded in the trace;
the student receives `text` and attachments.

Handle the response:

- `status: "active"`: save the returned `student_message` and continue.
- `status: "completed"`: record `reason`, stop the session loop, and hand off.
- `status: "failed"`: record `reason` or `error`, stop the session loop, and hand off.

Repeated identical tutor text can trigger `agent_stuck`, so every reply should
reflect the latest student message.

## Monitoring

When `control_token` is available:

```http
GET /ui/runs/{run_id}
Authorization: Bearer <control_token>
```

```http
GET /ui/runs/{run_id}/live
Authorization: Bearer <control_token>
```

Use live monitoring for status, conversation, and recent tool logs. Use cancel
only when the operator requests it:

```http
POST /ui/runs/{run_id}/cancel
Authorization: Bearer <control_token>
```

## Completion Handoff

At terminal status, return this to the operator:

- `run_id`
- `session_id`
- terminal `status`
- terminal `reason` or `error`
- visible task label
- result link when available: `${BASE}/#/results/{session_id}`

Evaluation is operator-owned. The external agent completes its job at terminal
session status.

## REST Skeleton

```python
import httpx

BASE = "https://benchmark-liard.vercel.app"
TIMEOUT = httpx.Timeout(900.0)

def post_json(client, path, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = client.post(path, json=payload or {}, headers=headers)
    r.raise_for_status()
    return r.json()

with httpx.Client(base_url=BASE, timeout=TIMEOUT) as client:
    run = post_json(client, "/client/runs/start", {
        "task": "<public_task_label>",
        "mode": "agent",
        "client": {"name": "external_rest_agent", "version": "1.0"},
    }, token="<api_key_from_ui>")
    token = run["token"]

    reg = post_json(client, "/session/register", {}, token=token)
    sid = reg["session_id"]

    start = post_json(client, f"/session/{sid}/start", {}, token=token)
    latest_student = start["student_message"]

    while True:
        tutor_text = compose_reply_from_visible_state(latest_student)
        reply = post_json(
            client,
            f"/session/{sid}/send",
            {"text": tutor_text},
            token=token,
        )
        status = reply.get("status")
        if status != "active":
            break
        latest_student = reply["student_message"]
```
