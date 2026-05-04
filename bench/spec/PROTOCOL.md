# QuantTutorBench Communication Protocol

Two equivalent communication methods are supported. Choose either one.

- **MCP**: Connect to `/mcp` via MCP StreamableHTTP. Standard tool discovery via `list_tools`.
- **REST**: Plain HTTP to `/session/*`. No protocol framing — curl directly usable.

Both methods share the same server logic, same permission rules, same scoring pipeline.

---

## 1. Session Lifecycle

```
register_session          start_session           status="completed"
     |                         |                        |
 UNREGISTERED ──────→ REGISTERED ──────→ IN_SESSION ──────→ COMPLETED
```

Phase enforcement is at call time. `list_tools` returns the static union
of all lifecycle tools every phase (plus task-specific domain tools once
a task is bound); the catalogue does not shrink or grow between phases.
Frozen-registry MCP clients that cache `list_tools` at connect time see
the full catalogue immediately and drive the state machine from response
hints rather than tool-list mutation.

| Phase        | Callable                                    | Out-of-phase behaviour |
|--------------|---------------------------------------------|------------------------|
| UNREGISTERED | register_session                            | Everything else returns `{"error", "allowed", "current_phase"}` |
| REGISTERED   | start_session                               | Same error shape, `allowed = ["start_session"]` |
| IN_SESSION   | send_message, domain tools                  | Same error shape, `allowed = ["send_message", "(domain tools)"]` |
| COMPLETED    | (none — terminal for the agent)             | Same error shape, `allowed = []`. Evaluation runs out-of-band on the operator surface (`/ops/session/{sid}/...`). |

Phase-denial payload:
```json
{"error": "Wrong phase. Call start_session next.",
 "allowed": ["start_session"],
 "current_phase": "registered"}
```

Successful lifecycle responses carry `next_allowed` + `current_phase` so the
agent can advance without re-reading `list_tools` — useful when the client
ignores `tools/list_changed`.

---

## 2. Operations

### 2.1 register_session

Register a task. Server creates sandbox, selects random persona.

```
MCP:  register_session({task_id: "X01_ma_offbyone"})
REST: POST /session/register  {"task_id": "X01_ma_offbyone"}
```

| Response | Body |
|----------|------|
| Success  | `{"session_id": "a1b2c3d4e5f6", "current_phase": "registered", "next_allowed": ["start_session"]}` |
| Bad request (400) | `{"error": "Missing required field: task_id"}` |
| Not found (404)   | `{"error": "Task not found: INVALID_ID"}` |

### 2.2 start_session

Start the tutoring session. Returns the user's first message plus the
task-specific tool catalogue. Can only be called once.

```
MCP:  start_session()
REST: POST /session/{sid}/start
```

| Response | Body |
|----------|------|
| Success  | `{"user_message": "...", "tools": [...], "current_phase": "in_session", "next_allowed": ["send_message"]}` |
| Wrong phase (403) | `{"error": "...", "allowed": ["start_session"], "current_phase": "..."}` |

### 2.3 list_tools / tools

Discover available tools. The catalogue is the **static union** of all
lifecycle tools plus any task-specific domain tools bound to this
session; the set does not mutate per phase. Phase enforcement is at call
time in `handle_tool_call`.

```
MCP:  list_tools()                    (standard MCP operation)
REST: GET /session/{sid}/tools
```

REST response: `{"tools": [{"name": "shell_exec", "description": "...", "inputSchema": {...}}, ...]}`

Read-only query. Available in any phase. MCP emits `tools/list_changed`
after phase transitions as a progressive-enhancement for compliant
clients, but frozen-registry clients are fully supported via the static
catalogue and response-payload `next_allowed` hints.

### 2.4 Domain tool call

Call a domain tool (shell_exec, file_read, etc.). Only available in IN_SESSION phase.

```
MCP:  tools/call({name: "shell_exec", arguments: {command: "ls /workspace"}})
REST: POST /session/{sid}/tool/shell_exec  {"command": "ls /workspace"}
```

REST request body = tool arguments directly (no `arguments` wrapper).

| Response | Body |
|----------|------|
| Success  | `{"result": "strategy.py\ndata/\n..."}` |
| Wrong phase (403) | `{"error": "...", "allowed": ["..."]}` |
| Blocked name (400) | `{"error": "Use the dedicated /session/{sid}/send endpoint instead."}` |

**Blocked names**: `register_session`, `start_session`, `send_message` cannot be
called via `/tool/{name}`. Use their dedicated endpoints. Evaluation tools
(`request_evaluation`, `get_results`, `get_scores`) were removed from the agent
catalogue in issue #46 — scoring lives on the operator surface (§2.6).

### 2.5 send_message

Send a message to the user. Returns reply and session status.

```
MCP:  send_message({text: "Let me help you debug this...",
                    reasoning: "Asking what they tried first to diagnose..."})
REST: POST /session/{sid}/send  {"text": "Let me help you debug this...",
                                 "reasoning": "Asking what they tried first..."}
```

**Arguments:**
- `text` *(required, string)* — message delivered to the user.
- `attachments` *(optional, array of ≤3 workspace paths)* — files/images shared with the user.
- `reasoning` *(optional, string)* — private rationale for this turn (why this wording, what hypothesis you are testing). Recorded in `tool_logs[].args` for post-hoc trace analysis. **Not delivered to the user** — the user simulator only reads `text` + attachments.

| Response | Body |
|----------|------|
| Active   | `{"user_message": "Oh I see...", "status": "active", "current_phase": "in_session", "next_allowed": ["send_message"]}` |
| Completed | `{"user_message": "Thanks!", "status": "completed", "reason": "user_satisfied", "current_phase": "completed", "next_allowed": []}` |
| Empty text (400) | `{"error": "Empty message. Provide text to send to the user."}` |
| Bad reasoning type (400) | `{"error": "reasoning must be a string"}` |

When `status == "completed"`, the session has ended. The agent's lifecycle is
over (`next_allowed: []`); further `send_message` / domain-tool calls return
the phase-denial shape. Scoring runs out-of-band on the operator surface.

### 2.6 Operator evaluation surface

Out of scope for the agent. Operators (UI, CI, scoring jobs) drive
evaluation off the COMPLETED bundle via `/ops/session/{sid}/...`,
gated by `Authorization: Bearer <QTB_ADMIN_TOKEN>`.

```
POST /ops/session/{sid}/evaluate[?eval_mode=tutor&tutor_dims=D3,D4]
GET  /ops/session/{sid}/results
GET  /ops/session/{sid}/scores[?history=true]
```

The same scoring driver runs offline:

```
python -m server.scripts.eval_single run --session <session_id> --mode tutor
python -m server.scripts.eval_single get --session <session_id> --history
```

Scores are stored in the completed bundle under
`evaluations/index.json` and `evaluations/score_n/{score,cost}.json`.
The sibling evaluator tree and manifest-based bundle contract are not part of
the current protocol.

### 2.7 Session status

```
REST: GET /session/{sid}
```

Returns: `{"session_id": "abc", "task_id": "X01", "phase": "in_session", "persona_id": "..."}`

Read-only. Available in any phase. No MCP equivalent (use list_tools to infer phase).

### 2.10 Cancel

Terminate session immediately. Destroys environment. Does not save results.

```
MCP:  DELETE /mcp  (with Mcp-Session-Id header)
REST: DELETE /session/{sid}
```

REST response: `{"status": "cancelled"}`

### 2.11 List sessions

```
REST: GET /session/list[?task_id=X01]
```

Returns: `{"sessions": [{"session_id": "abc", "task_id": "X01", "phase": "completed", ...}]}`

No MCP equivalent (cross-session query).

---

## 3. Conversation Flow Example

```
# 1. Register
POST /session/register  {"task_id": "X01_ma_offbyone"}
-> {"accepted": true, "session_id": "abc123"}

# 2. Discover tools
GET /session/abc123/tools
-> {"tools": [{"name": "shell_exec", ...}, {"name": "send_message", ...}, ...]}

# 3. Start
POST /session/abc123/start
-> {"user_message": "I wrote a moving average crossover strategy but..."}

# 4. Agent works: call tools + send messages
POST /session/abc123/tool/shell_exec  {"command": "cat /workspace/strategy.py"}
-> {"result": "import pandas as pd\n..."}

POST /session/abc123/send  {"text": "I see the issue. The window calculation..."}
-> {"user_message": "Oh, so the offset...", "status": "active"}

# 5. Repeat until completed
POST /session/abc123/send  {"text": "Exactly. Here's the corrected version..."}
-> {"user_message": "Thanks!", "status": "completed", "reason": "user_satisfied"}

# 6. Agent's lifecycle is over. Operator scores the bundle out-of-band.
GET /ops/session/abc123/results              # Authorization: Bearer <admin_token>
-> {"task_id": "X01", "session_id": "abc123", "conversation": [...], ...}

POST /ops/session/abc123/evaluate
-> {"status": "running", "message": "Evaluation started."}

GET /ops/session/abc123/scores
-> {
     "schema_version": "1.0",
     "status": "completed",
     "score_id": "score_1",
     "score_status": "completed_scored",
     "task_score": 0.72,
     "task_pass": null,
     "detail": {"dimensions": [...], "tracks": {"qr": {...}, "qp": {...}}, ...}
   }
```

---

## 4. HTTP Status Codes (REST)

| Situation | Code | Example |
|-----------|------|---------|
| Success | 200 | Normal response |
| Bad request | 400 | Missing task_id, empty text, blocked tool name |
| Permission error | 403 | Wrong phase (e.g. send_message before start) |
| Not found | 404 | Unknown session_id, task not found, no results |
| Server error | 500 | Container creation failed, eval pipeline crash |

---

## 5. Protocol Differences

| Aspect | MCP | REST |
|--------|-----|------|
| Response format | JSON string in TextContent (client must `json.loads`) | Direct JSON object |
| DELETE response | Per MCP spec | `{"status": "cancelled"}` (HTTP 200) |
| Session creation | `initialize` creates UNREGISTERED, then `register_session` tool | `POST /session/register` atomic (directly REGISTERED) |
| Tool discovery | Static union at `list_tools`; `tools/list_changed` emitted after phase transitions as progressive enhancement for compliant clients | `GET /tools` on demand |
| Error format | JSON-RPC error envelope | HTTP status code + JSON body |

These differences are transport-level only. Same session state, same permission rules, same scoring.

---

## 6. Server Configuration

```bash
python -m server --port 8000 --docker
python -m server --port 8000 --no-docker --log-level DEBUG
```

Scoring is always operator-triggered - either via
`POST /ops/session/{sid}/evaluate` (bearer-gated) or via
`python -m server.scripts.eval_single run --session <session_id>`. The
`--auto-eval` flag is not part of the server because session completion does
not trigger scoring automatically.

---

## 7. Run Layer (Control Plane)

All benchmark executions go through the Run layer. A **Run** is an assignment
that binds a task, a token, and a session together.

### 7.1 Create a Run

**From website** (user clicks "My Agent"):
```
POST /ui/runs  {"task": "D01", "mode": "agent"}
-> {"run_id": "run_...", "token": "qtb_...", "mcp_url": "http://...", "launch_command": "..."}
```

**From client** (one-step create + claim):
```
POST /client/runs/start  {"task": "D01", "client": {"name": "my_agent", "version": "1.0"}}
-> {"run_id": "run_...", "token": "qtb_...", "mcp_url": "http://..."}
```

### 7.2 Claim a Run (if created separately)

```
POST /client/runs/claim  {"run_token": "qtb_...", "client": {"name": "my_agent"}}
-> {"run_id": "run_...", "mcp_url": "http://...", "public_task_label": "D01"}
```

### 7.3 Connect and Execute

After claiming, connect to the MCP endpoint or use REST with the token:

**MCP:**
```
Connect to mcp_url with header: Authorization: Bearer <token>
Then: register_session() → start_session() → tools + send_message loop
```

**REST:**
```
POST /session/register  (Authorization: Bearer <token>)
POST /session/{sid}/start
POST /session/{sid}/send  {"text": "..."}
POST /session/{sid}/tool/shell_exec  {"command": "..."}
```

`register_session` does not require `task_id` — the server resolves it from the Run.

### 7.4 Monitor and Cancel

```
GET /ui/runs/{run_id}         -> run status
GET /ui/runs/{run_id}/live    -> real-time conversation + tool logs
POST /ui/runs/{run_id}/cancel -> cancel at any stage
```

### 7.5 Task Catalog

```
GET /ui/tasks/catalog
-> {"tasks": [{"label": "D01", "category": "data_analysis", "difficulty": "easy"}, ...]}
```

Only public labels + category + difficulty. No internal details.

### 7.6 Run Lifecycle

```
WAITING → (client claims) → CLAIMED → (session registers) → ACTIVE → COMPLETED / FAILED
Any non-terminal state can be cancelled → CANCELLED
```

---

## 8. Result Storage

Results are persisted at `results/server/{task_id}/{persona_id}/{timestamp}_{session_id}/`:

```
results/server/X01_ma_offbyone/abc123/
    run_state.json
    agent_files/
    evaluations/
        eval_20260410_110000/
            scores.md
            trace.md
            cost.md
            eval_meta.json
        latest -> eval_20260410_110000/
```
