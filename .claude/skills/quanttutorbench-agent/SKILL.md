---
name: quanttutorbench-agent
description: Drive a QuantTutorBench tutoring session as the agent in the loop via REST. Read each student message and compose the next reply yourself — no static reply scripts. Lets the server terminate the session naturally and persists the bundle for offline eval.
disable-model-invocation: false
allowed-tools: Bash, Read, Write
---

# QuantTutorBench: agent in the loop

You are the **tutor agent** for a QuantTutorBench session driven over REST.
The student is a server-side LLM persona; you cannot see them, you only see
their text replies. Your job is to drive a real tutoring conversation —
read each student message and compose the next reply based on what they
actually said.

## Source of truth

The full reference — REST flow, termination semantics, workspace tools,
bundle persistence, the five real pitfalls, and the pre-flight checklist —
lives at **`docs/agent_in_loop.md`**. Read it before driving a session.

That document is the canonical, agent-agnostic reference (Codex, Cursor,
Gemini, custom agents read it too). This skill exists so that Claude Code
agents in this repo can be auto-invoked into the right behavior; both
artifacts are kept in sync via issue #57.

The runnable reference driver is **`bench/scripts/agent_in_loop_example.py`**
— replace its `compose_reply` stub with your model integration and the rest
works as-is.

## When to use this skill

The user asks you to:

- "Drive an I-series / X-series / E-series / D-series / B-series / S-series
  / A-series task on prod or local"
- "Run a live tutoring session against the benchmark server"
- "Be the agent for a QuantTutorBench eval session"
- "Smoke-test the eval flow with a real LLM in the loop"

Do **not** use this skill for:

- Static availability smoke tests — `bench/scripts/prod_smoke.py`.
- Batch evaluation of existing bundles — `python -m server.evaluator --all-pending`.
- Building a new task or persona — JSON edits under `bench/tasks/` and `bench/personas/`.

## How to drive a session (operational summary)

1. **Read `docs/agent_in_loop.md` first** — every section is load-bearing.
2. Create the run + session via the four-call lifecycle described there.
3. Run the per-turn loop: read `student_message`, compose the next reply
   based on what they actually said, POST to `/session/{sid}/send`. Repeat
   until `status != "active"`.
4. **Never use a static reply queue.** The server's repeat-detector fires
   `agent_stuck` after 3× identical text. Compose every reply.
5. Use HTTP client timeout ≥ 900 s on `/send` (student-sim turns can take
   5–15 s, with outliers around 16 s).
6. Don't DELETE active sessions to "clean up" — let the server terminate
   naturally (max_turns is the usual endpoint).
7. When terminal, print the session id and expected bundle path:
   `bench/results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:8]}/`.
8. Eval is out of band — point the operator at
   `python -m server.evaluator --bundle <bundle_dir>`.

## Default environment

Production VPS is the canonical target for any agent driving a session
unless the user explicitly asks for local dev:

```
BASE="https://217-15-165-83.sslip.io"
```

Use `http://localhost:8765` **only** when the user has confirmed they
started a local server with `python -m server --port 8765 --docker`. Other
agents reading your output expect prod URLs in any commands you produce.

`https://benchmark-liard.vercel.app` proxies to the same backend; use it
only if you specifically need to exercise the Vercel rewrites layer.

## Pre-flight check before you start

- `BASE` defaults to the production VPS above unless the user said otherwise.
- `task_id` and `persona_id` are real (consult `bench/tasks/.../*.json`).
- Your reply strategy reads the latest `student_message` (no static queue).
- HTTP client timeout ≥ 900 s.
- You're prepared for ~15+ turns; don't bail early.

If any of those are unclear, read `docs/agent_in_loop.md` end to end before
starting.
