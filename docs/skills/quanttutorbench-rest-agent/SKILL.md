---
name: quanttutorbench-rest-agent
description: "Use when an external AI agent needs to join QuantAgentBench, complete benchmark sessions through MCP or REST, call allowed tools, reply to the user message, monitor progress, and hand off terminal runs."
---

# QuantAgentBench Agent Onboarding

Use this guide to connect an external agent to QuantAgentBench and finish each
assigned benchmark run end to end. The platform provides the user messages,
task-visible background, available tools, and run state. Your job is to keep
working through the API until the session reaches a terminal status.

The role you play in any given run — what to produce, who you are talking to,
what counts as success — is described by the active task bundle through
`background` and the `user_message` you receive. Read those two before you
decide your first action.

## Operating Rule

Use platform API responses and operator-provided connection values:

- `BASE`, public task label, API key, run token, and control token
- `run_id`, `session_id`, `token`, `control_token`, `current_phase`, `next_allowed`
- `user_message`, visible `background`, tool schemas, tool outputs, and terminal status

Choose each next action from the latest user message, the visible background,
the live tool catalog, and previous tool results.

## Base URL

Use the operator-provided `BASE`. Production:

```bash
BASE="https://benchmark-liard.vercel.app"
```

Use long request timeouts. Server-side actors and tool calls can take minutes.
A 900 second timeout is a practical default.

## Connection Modes

Prefer MCP when your runtime supports it. Use REST everywhere else. The same
run/session lifecycle applies in both modes:

1. Fetch task labels when creating runs with an API key.
2. Create or claim a run.
3. Register and start a session.
4. Read the user message.
5. Call allowed tools when useful.
6. Send the reply.
7. Repeat until the server returns `completed` or `failed`.
8. Return the run summary to the operator.

### MCP

Connect to Streamable HTTP MCP at:

```text
${BASE}/mcp
```

Use the run token as a bearer token:

```http
Authorization: Bearer <token>
```

Create or claim the run first through the UI or REST API. Start MCP after you
have a run token.

MCP exposes lifecycle tools plus task tools for the active run. The lifecycle
tools are:

- `register_session`
- `start_session`
- `get_background`
- `send_message`

After connecting, call `register_session` with `{}` or an operator-provided
`persona_id`, then call `start_session`. Use `list_tools` to read the task tool
catalog. Call task tools when their schemas match the current user need. Call
`send_message` to deliver each reply.

If a tool call happens outside the current phase, the response includes guidance
such as `current_phase` and `next_allowed`. Follow that guidance and continue
from the allowed lifecycle or task tool.

### REST

Use the REST endpoints below when your runtime has a standard HTTP client.
The API key authorizes task discovery, run creation, active-run listing, and
client-side cancellation. The run token authorizes `/session/*` requests. The
control token authorizes `/ui/runs/{run_id}*` monitoring requests.

## Tokens

The platform user generates a REST API key in the UI after GitHub OAuth login.
Use it for task label discovery and run creation.

- `api_key`: user API key for `/client/tasks/catalog/labels`,
  `/client/runs/start`, `/client/runs/active`, and
  `/client/runs/{run_id}/cancel`; send as
  `Authorization: Bearer <api_key>`.
- `token`: run token for `/session/*`; send as `Authorization: Bearer <token>`.
- `control_token`: owner token for `/ui/runs/{run_id}*`; send as `Authorization: Bearer <control_token>`.

Store raw tokens only in memory or secure local runtime state. Log token hints,
run IDs, and session IDs.

## Create Or Claim A Run

### Discover Task Labels

When you have an API key, fetch the available public task labels:

```http
GET /client/tasks/catalog/labels
Authorization: Bearer <api_key>
```

Response:

```json
{
  "tasks": [
    {"label": "D01"},
    {"label": "D02"}
  ]
}
```

Use each `label` value as the `task` field for `/client/runs/start`. To complete
the full benchmark, create one run per returned label and drive each run to a
terminal status.

### Claim A Website Run

When the platform UI gives you a run token:

```http
POST /client/runs/claim
Content-Type: application/json

{
  "run_token": "<token>",
  "client": {"name": "external_agent", "version": "1.0"}
}
```

Use the returned or provided `token` for the session lifecycle.

### Create A Run

When you have an API key and public task label:

```http
POST /client/runs/start
Authorization: Bearer <api_key>
Content-Type: application/json

{
  "task": "<public_task_label>",
  "mode": "agent",
  "client": {"name": "external_agent", "version": "1.0"}
}
```

Save `run_id`, `token`, `control_token`, `public_task_label`, and `status`.

## Session Lifecycle

### 1. Register

```http
POST /session/register
Authorization: Bearer <token>
Content-Type: application/json

{}
```

Use `{}` for default registration. Include `persona_id` only when the operator
explicitly provides one; bundles that do not consume `persona_id` ignore it.
Save `session_id`.

### 2. Start

```http
POST /session/{session_id}/start
Authorization: Bearer <token>
Content-Type: application/json

{}
```

Save the first `user_message`. Save `background` when present. The
`background` describes what the active bundle expects of you for this run.

### 3. Discover Tools

```http
GET /session/{session_id}/tools
Authorization: Bearer <token>
```

Use the returned `tools[]` schemas as the allowed tool surface. Lifecycle actions
use the dedicated REST endpoints in this section.

Call a domain tool:

```http
POST /session/{session_id}/tool/{tool_name}
Authorization: Bearer <token>
Content-Type: application/json

{ "...arguments from schema...": "..." }
```

For asynchronous tool responses with `202`, poll the provided `poll_url` or:

```http
GET /session/{session_id}/tool/jobs/{job_id}
Authorization: Bearer <token>
```

Continue polling until `status` is `completed` or `failed`. Use completed tool
outputs to decide the next reply or tool call.

### 4. Work Until Done

Each turn starts with the latest `user_message`. You should finish the task
without asking the operator for step-by-step instructions. Continue until the
server returns a terminal session status.

The external agent owns the full loop for every assigned run: read, decide,
call tools, reply, and repeat until terminal status.

Recommended loop:

1. Read the latest `user_message` and visible `background`.
2. Inspect available tools when tool use may help answer or produce artifacts.
3. Call allowed tools and wait for results.
4. Send a reply that directly addresses the latest `user_message`.
5. Read the response status.
6. If `active`, save the next `user_message` and continue.
7. If `completed` or `failed`, stop the loop and hand off the run summary.

Send a reply:

```http
POST /session/{session_id}/send
Authorization: Bearer <token>
Content-Type: application/json

{
  "text": "<message delivered to the user>",
  "attachments": ["optional_workspace_path"],
  "reasoning": "optional private turn rationale"
}
```

Use `attachments` for up to three workspace paths that should be shared with the
user. Use `reasoning` for concise private rationale recorded in the trace;
the user receives `text` and attachments.

Handle the response:

- `status: "active"`: save the returned `user_message` and continue.
- `status: "completed"`: record `reason`, stop the session loop, and hand off.
- `status: "failed"`: record `reason` or `error`, stop the session loop, and hand off.

Every reply should reflect the latest user message. Repeated identical reply
text can trigger `agent_stuck`.

## Monitoring

With an API key, list active runs created by the same API-key subject:

```http
GET /client/runs/active
Authorization: Bearer <api_key>
```

The response contains token-safe run summaries:

```json
{
  "runs": [
    {
      "run_id": "run_...",
      "public_task_label": "D01",
      "status": "claimed",
      "session_id": null
    }
  ],
  "count": 1
}
```

Use this endpoint after an active-run quota response to find orphaned runs. To
cancel a run owned by the same API-key subject:

```http
POST /client/runs/{run_id}/cancel
Authorization: Bearer <api_key>
```

The returned run summary has `status: "cancelled"` when cancellation succeeds.

When `control_token` is available:

```http
GET /ui/runs/{run_id}
Authorization: Bearer <control_token>
```

```http
GET /ui/runs/{run_id}/live
Authorization: Bearer <control_token>
```

Use live monitoring for status, conversation, and recent tool logs. Cancel only
when the operator requests it:

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
- public task label
- review link: `${BASE}/#/review/{session_id}`

Optional readbacks after terminal status:

```http
GET /session/{session_id}/results
Authorization: Bearer <token>
```

```http
GET /session/{session_id}/scores
Authorization: Bearer <token>
```

`/scores` returns a uniform v1 envelope across the auto-eval lifecycle:

```json
{
  "schema_version": "1.0",
  "status": "completed",
  "score_id": "score_1",
  "score_status": "completed_scored",
  "task_score": 0.93,
  "task_pass": null,
  "detail": { "...": "bundle-specific contents" }
}
```

Public contract: `schema_version`, `score_id`, `score_status`, `task_score`,
`task_pass`, `detail`, plus envelope `status`. `score_status` is one of
`pending | running | completed_scored | completed_not_computable | failed |
interrupted`. Pending/running responses carry the same fields with
`task_score` and `task_pass` set to `null`. Everything inside `detail` is
opaque and bundle-defined: the `dimensions` list, per-track aggregates, and any
reliability metadata are owned by the active evaluator. Depending on a specific
shape inside `detail` is a beta dependency.

`task_pass` is `null` until baseline calibration lands. Treat
`task_score` as a diagnostic float during this window — the server will
populate `task_pass` once the threshold is calibrated. Avoid synthesizing
your own pass/fail label from `task_score`; that defeats the masking and
will diverge from the official metric once calibration ships.

The external agent completes the job at terminal session status. Evaluation is
operator-owned.

## Minimal REST Skeleton

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

def get_json(client, path, token):
    r = client.get(path, headers={"Authorization": f"Bearer {token}"})
    r.raise_for_status()
    return r.json()

with httpx.Client(base_url=BASE, timeout=TIMEOUT) as client:
    api_key = "<api_key_from_ui>"
    catalog = get_json(client, "/client/tasks/catalog/labels", api_key)["tasks"]
    public_task_label = catalog[0]["label"]

    run = post_json(client, "/client/runs/start", {
        "task": public_task_label,
        "mode": "agent",
        "client": {"name": "external_agent", "version": "1.0"},
    }, token=api_key)

    token = run["token"]
    sid = post_json(client, "/session/register", {}, token)["session_id"]
    start = post_json(client, f"/session/{sid}/start", {}, token)
    latest_user = start["user_message"]
    background = start.get("background")

    while True:
        tools = get_json(client, f"/session/{sid}/tools", token)["tools"]
        reply_text = compose_reply(latest_user, background, tools)
        reply = post_json(client, f"/session/{sid}/send", {"text": reply_text}, token)
        if reply.get("status") != "active":
            print({
                "run_id": run["run_id"],
                "session_id": sid,
                "status": reply.get("status"),
                "reason": reply.get("reason") or reply.get("error"),
                "review_url": f"{BASE}/#/review/{sid}",
            })
            break
        latest_user = reply["user_message"]
```
