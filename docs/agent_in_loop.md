# Agent in the loop: driving QuantTutorBench sessions over REST

**Audience.** Any engineer or AI agent (Claude, Codex, Cursor, Gemini, custom)
that wants to drive a QuantTutorBench tutoring session as the **tutor** in
the loop. The student is a server-side LLM persona — you cannot see them,
you only see their text replies. Your job is to drive a real conversation:
read each student message and compose the next reply based on what they
actually said.

This is the **canonical, agent-agnostic reference**. Claude Code agents
working in this repo also see a thin invocation shim at
`.claude/skills/quanttutorbench-agent/SKILL.md` that points back here.

This repo intentionally does not provide a runnable auto-driver that emits
tutor messages. Agent-in-the-loop runs are evaluated as live tutoring
conversations: the tutor must read each `student_message` and decide the next
reply from that message.

Issue #57 captures the design decisions and the empirical evidence behind
the pitfalls below.

---

## Environments — pick the right BASE

There are three URLs for the benchmark. **Default to production unless you
have a specific reason to use local.**

| Where | URL | When to use |
|---|---|---|
| **Production VPS** (canonical) | `https://217-15-165-83.sslip.io` | Real eval runs, batch campaigns, anything you want bundles persisted on the canonical host. **This is the default for any external agent.** |
| Production via Vercel | `https://benchmark-liard.vercel.app` | Same backend; use only if you need to exercise the Vercel rewrites layer (frontend integration testing). |
| Local dev | `http://localhost:8765` | You started the server yourself with `python -m server --port 8765 --docker` for code changes / debugging. **Do not use this in command examples for other agents** — they probably do not have a local server running. |

If you are an agent reading this guide and the user did not tell you which
environment to use, **assume production**:

```bash
BASE="https://217-15-165-83.sslip.io"
```

## When this applies

Use this guide when you want to:

- Drive an I-series / X-series / E-series / D-series / B-series / S-series
  / A-series task on prod or local.
- Run a live tutoring session against the benchmark server with your own LLM
  acting as the tutor.
- Smoke-test the eval pipeline end-to-end with a real LLM in the loop.

This is **not** the right reference for:

- Static availability ping — that's `bench/scripts/prod_smoke.py`.
- Batch evaluation of existing bundles — that's
  `python -m server.evaluator --all-pending` (issue #47).
- Building a new task or persona — those are JSON edits under
  `bench/tasks/` and `bench/personas/`.

---

## The four-call lifecycle

```
POST /client/runs/start    → run_id, token
POST /session/register     → session_id     (Authorization: Bearer <token>)
POST /session/{sid}/start  → background, opening student_message
POST /session/{sid}/send   → student reply, status, reason     (loop)
```

Concrete shell sketch:

```bash
BASE="${BASE:-https://217-15-165-83.sslip.io}"   # production VPS — default; see Environments table
TASK="I01_implement_sma"                          # any bench/tasks/.../*.json id
PERSONA="fullstack_practitioner"                  # one of task.persona_ids

RUN=$(curl -sS -X POST "$BASE/client/runs/start" \
        -H 'content-type: application/json' \
        -d "{\"task\":\"$TASK\",\"mode\":\"agent\"}")
TOKEN=$(echo "$RUN" | jq -r .token)

SID=$(curl -sS -X POST "$BASE/session/register" \
        -H "authorization: Bearer $TOKEN" \
        -H 'content-type: application/json' \
        -d "{\"persona_id\":\"$PERSONA\"}" | jq -r .session_id)

START=$(curl -sS -X POST "$BASE/session/$SID/start")
OPENER=$(echo "$START" | jq -r .student_message)
# ... loop send_message until terminal ...
```

This guide gives the protocol lifecycle only. Do not wrap it in a static
reply queue. If you build a client, its reply function must call a live tutor
model or a human operator on every turn using the latest `student_message`.

---

## The per-turn loop (the only correct pattern)

Each turn:

1. You have the most recent `student_message` (from `/start` on turn 1, or
   from the previous `/send` response on subsequent turns).
2. **You read it carefully and compose the next reply based on what the
   student actually said.** Not a preset list. Not a template. Address the
   specific question or concern in their last message.
3. POST to `/session/{sid}/send` with `{"text": "<your reply>"}`.
4. Response shape (verified against `bench/server/core/session.py::TutoringSession._result`):
   `{"student_message": "...", "status": "active|completed|failed", "reason": "...", "sim_error": {...}?, "current_phase": "...", "next_allowed": [...]}`.
   `reason` is omitted on `active`; `sim_error` only appears on
   `student_sim_error:*` failures. There is **no** `tool_calls` field —
   the student persona has no tools to call.
5. If `status != "active"`, the session is terminal — stop the loop.

The repeat-detector pitfall (below) makes this non-negotiable: you must
*compose* each reply. A static reply queue will eventually trigger
`agent_stuck`.

---

## Termination semantics

The server ends the session when one of these fires:

| `status` | `reason` | What it means |
|---|---|---|
| `completed` | `objectives_met` | T/C checker fired — task objectives covered. Cleanest possible close. |
| `completed` | `max_turns` | Hit `task.max_turns` (e.g. I01 = 15). Natural close. |
| `completed` | `timeout` | `task.timeout_minutes` deadline reached. Still treated as `completed` (see `_is_failed_reason` in `bench/server/core/session.py`). |
| `completed` | `agent_abandoned` | DELETE during an active session. Bundle persists since #54. |
| `failed` | `agent_stuck` | You sent the **same `text` 3× in a row** — repeat-detector. |
| `failed` | `student_sim_error:*` | Student-sim LLM failure. Response also carries `sim_error` with structured failure metadata. Not your fault. |

There is **no "agent declares done" tool** in the protocol. The protocol
terminates only via server-side signals. The natural endpoint is
`max_turns` for most tasks. See `bench/server/api/protocol.py` for the
state machine.

After terminal status, the protocol exposes `next_allowed: []` and the
agent lifecycle ends.

---

## Workspace tools (when to call them)

You also have access to per-session tool endpoints under
`/session/{sid}/tool/<name>`. The full catalogue is at
`/session/{sid}/tools`. Common ones:

- `file_write`, `file_read`, `file_list`, `shell_exec`, `code_exec`
- `fetch_market_data`, `compute_indicator`, `compute_statistics`
- `run_backtest` — Python backtest engine. **Async**: returns HTTP 202
  with `{job_id, status, poll_url}`; poll
  `GET /session/{sid}/tool/jobs/{job_id}` for terminal state.
- `run_lean_backtest` — LEAN C# backtest. **Async**: same 202 +
  `{job_id, status, poll_url}` shape as `run_backtest`. Both belong to
  `HEAVY_TOOLS` (`bench/server/api/limits.py`) and share the
  `QTB_MAX_CONCURRENT_BACKTESTS` semaphore.

These tools run **inside the session's Docker sandbox** with
`cwd=/workspace`. Anything you write to `/workspace` is captured as
`agent_files/` in the final bundle.

**The student cannot call tools.** They are a simulated LLM persona
(`bench/server/core/student_sim.py`) — pure text in, pure text out. If the
student says "I'll run it now", nothing physical happens. If you want a
real backtest result to feature in the session, **you** must call
`run_lean_backtest` yourself and incorporate the output into your next
reply.

When to call workspace tools (not exhaustive):

- **Debug-class tasks (X-series).** Copy `/student_code/foo.cs` into
  `/workspace`, edit, build, backtest, compare to reference.
- **Implementation tasks (I-series)** *if* the student asks for a
  demonstration. By default I-series is verbal guidance — only run code if
  it adds genuine value to the conversation.
- **Validation tasks.** Run the algorithm to verify a specific claim before
  asserting it in your next reply.

---

## Five real pitfalls

Each one is grounded in a live failure mode (sessions `01e84317` and
`42e74d3a`):

1. **Static reply queues trigger `agent_stuck`.** A driver that sends
   pre-written messages indexed by turn number will eventually emit the
   same closing line three times in a row. The server's repeat-detector
   fires `reason=agent_stuck` and the session ends `failed`. Always
   compose each reply from the actual `student_message` you just received.

2. **`/session/{sid}/send` can take 5–15 seconds** per call (student-sim
   LLM round-trip). Use a generous client timeout — `--max-time 900` or
   the equivalent in your HTTP client. Live runs have produced
   individual turns up to ~16 s.

3. **`max_turns` is per-task, not per-session.** I01 = 15. You cannot
   raise it from the agent side. Don't set your own lower cap — let the
   server end the session.

4. **Don't DELETE active sessions to "clean up" early.** It's safe since
   #54 (the bundle persists), but `reason=agent_abandoned` is a worse
   audit trail than `reason=max_turns` or `reason=objectives_met`. Drive to
   natural terminal whenever possible.

5. **Auth.** Only `/ops/*` endpoints need `QTB_ADMIN_TOKEN`. The agent
   surface (`/client/runs/*`, `/session/*`) uses the per-run bearer
   token returned by `/client/runs/start`. Don't ask the user for an
   admin token to drive a session — it's not needed and you shouldn't
   have it.

---

## Bundle persistence

When the session reaches a terminal status, the server automatically:

1. Saves `run_state.json` (full conversation + `tool_logs` + metadata).
2. Saves `run_state.md` (human-readable transcript).
3. Snapshots `/workspace` into `agent_files/` (any code/data the tutor
   wrote).
4. Writes `manifest.json` (v1.0.0 bundle contract from issue #46).

Bundle path:
```
bench/results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:8]}/
```

Print the session id and expected bundle path at the end of your run so
the operator knows where to find it.

Since #54, `DELETE /session/{sid}` also persists the bundle. Natural
terminal is still preferable for cleaner audit metadata.

---

## Eval is out of band

You do **not** trigger evaluation from the agent loop. After the session
terminates, the bundle is ready for offline scoring via:

```bash
# Single-bundle:
python -m server.evaluator --bundle <bundle_dir>

# Or batched against many bundles (issue #47):
python -m server.evaluator --all-pending --concurrency 4
```

The agent's job ends at the terminal `send_message`. Mention this to the
operator when you finish, and point them at the bundle path.

---

## Pre-flight checklist

Before driving a non-trivial session, confirm:

- [ ] `BASE` is set to the right server. **Default is the production VPS:
      `https://217-15-165-83.sslip.io`.** Only use `http://localhost:8765`
      if you started the server yourself for development.
- [ ] `task_id` exists under `bench/tasks/.../*.json` and the persona
      you chose is in that task's `persona_ids`.
- [ ] Your reply strategy calls a live LLM or human operator on every turn.
      If you're an LLM doing this in-session, the strategy is "read
      `student_message`, think, respond".
- [ ] HTTP client timeout ≥ 900 s on `/send`.
- [ ] You're prepared for the session to take many turns (15 for I01,
      higher for E-series). Don't bail early.

---

## Reference files in this repo

- `.claude/skills/quanttutorbench-agent/SKILL.md` — Claude Code skill (thin shim pointing here)
- `bench/spec/PROTOCOL.md` — protocol surface
- `bench/server/api/protocol.py` — phase machine + permission rules
- `bench/server/storage/BUNDLE_SCHEMA.md` — bundle layout and v1.0.0 contract
- `bench/server/evaluator/__main__.py` — batch eval CLI for scoring bundles
- `docs/architecture.md` — overall system map
