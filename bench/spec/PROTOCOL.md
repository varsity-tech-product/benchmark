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

| Phase        | Allowed                              | Rejected                                      |
|--------------|--------------------------------------|-----------------------------------------------|
| UNREGISTERED | register_session                     | start, send, tools, evaluate, results, scores |
| REGISTERED   | start_session, list_tools            | register, send, tools, evaluate               |
| IN_SESSION   | send_message, domain tools           | register, start, evaluate                     |
| COMPLETED    | request_evaluation, get_results, get_scores | register, start, send, domain tools    |

Rejected calls return: `{"error": "description", "allowed": ["permitted_operations"]}`

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
| Success  | `{"accepted": true, "session_id": "a1b2c3d4e5f6"}` |
| Bad request (400) | `{"accepted": false, "error": "Missing required field: task_id"}` |
| Not found (404)   | `{"accepted": false, "error": "Task not found: INVALID_ID"}` |

### 2.2 start_session

Start the tutoring session. Returns the student's first message. Can only be called once.

```
MCP:  start_session()
REST: POST /session/{sid}/start
```

| Response | Body |
|----------|------|
| Success  | `{"student_message": "I wrote a moving average crossover strategy but..."}` |
| Wrong phase (403) | `{"error": "...", "allowed": ["start_session"]}` |

### 2.3 list_tools / tools

Discover available tools. Tool set varies by task and changes with phase.

```
MCP:  list_tools()                    (standard MCP operation)
REST: GET /session/{sid}/tools
```

REST response: `{"tools": [{"name": "shell_exec", "description": "...", "inputSchema": {...}}, ...]}`

Read-only query. Available in any phase.

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

**Blocked names**: `register_session`, `start_session`, `send_message`, `request_evaluation`,
`get_results`, `get_scores` cannot be called via `/tool/{name}`. Use their dedicated endpoints.

### 2.5 send_message

Send a message to the student. Returns reply and session status.

```
MCP:  send_message({text: "Let me help you debug this..."})
REST: POST /session/{sid}/send  {"text": "Let me help you debug this..."}
```

| Response | Body |
|----------|------|
| Active   | `{"student_message": "Oh I see...", "status": "active"}` |
| Completed | `{"student_message": "Thanks!", "status": "completed", "reason": "objectives_met"}` |
| Empty text (400) | `{"error": "Empty message. Provide text to send to the student."}` |

When `status == "completed"`, the session has ended. Stop calling tools and send_message.

### 2.6 request_evaluation

Request scoring. Only available after session completes.

```
MCP:  request_evaluation()
REST: POST /session/{sid}/evaluate[?force=true]
```

| Response | Body |
|----------|------|
| Started  | `{"status": "running", "message": "Evaluation started."}` |
| In progress | `{"status": "running", "message": "Evaluation in progress."}` |
| Done     | `{"status": "completed", "scores": {"overall": 0.72, ...}}` |
| Failed   | `{"status": "failed", "error": "..."}` |

`?force=true` (REST) resets and re-runs evaluation.

### 2.7 get_results

Return session run_state (conversation, tool_logs, metrics). Only available after session completes.

```
MCP:  get_results()
REST: GET /session/{sid}/results
```

Returns the full `run_state.json` content.

### 2.8 get_scores

Return evaluation scores. Only available after session completes.

```
MCP:  get_scores({history: false})
REST: GET /session/{sid}/scores[?history=true]
```

Default: latest evaluation result. `history=true`: all evaluation runs.

### 2.9 Session status

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
-> {"student_message": "I wrote a moving average crossover strategy but..."}

# 4. Agent works: call tools + send messages
POST /session/abc123/tool/shell_exec  {"command": "cat /workspace/strategy.py"}
-> {"result": "import pandas as pd\n..."}

POST /session/abc123/send  {"text": "I see the issue. The window calculation..."}
-> {"student_message": "Oh, so the offset...", "status": "active"}

# 5. Repeat until completed
POST /session/abc123/send  {"text": "Exactly. Here's the corrected version..."}
-> {"student_message": "Thanks!", "status": "completed", "reason": "objectives_met"}

# 6. Get results and request evaluation
GET /session/abc123/results
-> {"task_id": "X01", "session_id": "abc123", "conversation": [...], ...}

POST /session/abc123/evaluate
-> {"status": "running", "message": "Evaluation started."}

GET /session/abc123/scores
-> {"status": "completed", "scores": {"overall": 0.72, ...}}
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
| Tool discovery | `list_tools` + automatic `tools/list_changed` notification | `GET /tools` on demand; phase change visible in response semantics |
| Error format | JSON-RPC error envelope | HTTP status code + JSON body |

These differences are transport-level only. Same session state, same permission rules, same scoring.

---

## 6. Server Configuration

```bash
python -m server --port 8000 --docker                    # default: auto_eval off
python -m server --port 8000 --docker --auto-eval        # auto-evaluate on completion
python -m server --port 8000 --no-docker --log-level DEBUG
```

`--auto-eval` is a server-side setting. When enabled, evaluation starts automatically
when a session completes. Clients can still explicitly call `request_evaluation`.

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
