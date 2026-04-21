# QuantTutorBench v6.0 Run / Hosted Web Architecture Plan

> Date: 2026-04-20
> Author: Codex + Claude review
> Status: Tier 1+2 shipped on master · deployment live · Tier 3/4 deferred
> Scope: `bench/server`, `bench/client`, `bench/server/web`
> Deployment target: frontend on Vercel, backend on VPS
> Live: frontend `https://benchmark-liard.vercel.app`, backend `https://217-15-165-83.sslip.io`

---

## Implementation Status (2026-04-20)

Hosted MVP is live on the single-VPS setup and has been externally verified end-to-end.

### What shipped on master

| Scope | Commit / PR | Notes |
|---|---|---|
| Vercel edge rewrites (`/session`, `/ui`, `/static`, `/mcp`) | `c2c85e1` (prior) | Makes browser-origin calls same-origin — architecturally replaces Step 1 CORS and Step 2 `apiFetch` helper. |
| Vercel rewrites extended to `/health`, `/client/*` | PR #22 | External uptime monitors and CLI clients can now target the canonical domain. |
| Label-only Run catalog + run state transition hardening + `control_token` | `3b0b48f` (PR #20, via #18/#17) | Implements plan Step 3 + Step 5. |
| GHA deploy workflow (rsync + systemd restart + smoke) | `2f7a6b4`, `5ac2b65`, `176ac8c` | VPS auto-deploys on push to master. |
| `/health` deep probe (docker, LEAN image, disk) + backtest semaphore cap | `5e5da19` (PR #20) | Deploy smoke gate + OOM protection. `QTB_MAX_CONCURRENT_BACKTESTS` (default 2). |
| Async job pattern for heavy tools (202 + `/tool/jobs/{job_id}`) | `c4d6fa5` (PR #20) | Removes 10-minute open-HTTP exposure for `run_lean_backtest` / `run_backtest`; restart-safe via `mark_orphans_failed`. |
| External availability smoke (`bench/scripts/prod_smoke.py`) | PR #22 | T0–T7 single-flow smoke over 22 endpoints. |

### External verification (2026-04-20)

`python bench/scripts/prod_smoke.py --base-url https://217-15-165-83.sslip.io` → **22/22 PASS** on `master@7ed0f99`. Every public HTTP endpoint reachable and non-5xx inside the per-request deadline: liveness, public metadata, run creation (authed via `control_token`), session lifecycle, sync tool dispatch, async 202 + poll, results/scores, UI read paths, clean cancel + DELETE teardown. Covers the Section 1.4 auth matrix for the currently-enforced tier.

### Where the plan deviates from what shipped

- **Step 1 (CORS) and Step 2 (`apiFetch`/`config.js`)**: not implemented and no longer needed. Vercel edge rewrites make the browser see a same-origin API, so there's no CORS preflight and the frontend uses relative URLs only. The outcome the plan wanted (browser can call the VPS from the hosted frontend) is achieved via `vercel-frontend/vercel.json` instead.
- **Heavy-tool transport**: the original plan did not specify how long-running backtests would behave end-to-end through a Vercel edge proxy. The shipped design returns 202 + `job_id` in under a second and the client polls, avoiding edge idle-timeout entirely. See `bench/server/run/jobs.py` and `_execute_tool_job` in `http_app.py`.

### Tier 3/4 still deferred

Scope for Tier 3 (session-endpoint `run_token` enforcement, MCP `register_session` body-task-id handling, frontend Run UI `control_token` wiring) and Tier 4 (trace upload auth, `/client/runs/start` admin gate, Phase 5 hardening) is unchanged. See Sections 8.3–8.4 and the checklist in Section 9.

---

## 0. Executive Summary

The current Run/Web implementation works for single-machine development but has five classes of issues that block Vercel/VPS deployment:

1. Frontend assumes same-origin API calls.
2. Run control endpoints are public by `run_id` alone.
3. Client trace is local-only; hosted Results UI cannot see it.
4. Run state transitions can diverge from Session state.
5. MCP transport layer has its own HTTP handling that sits outside Starlette middleware.

Out of scope:

- `GoalChecker` / TC coverage for A-class tasks — fixed separately in task data.
- Full user account system — not needed for next phase.

---

## 1. Current Implementation Snapshot

### 1.1 Backend execution surfaces

| Layer | Current files | Current role |
|------|---------------|--------------|
| Run control plane | `bench/server/run/models.py`, `store.py`, `service.py`, `catalog.py` | Creates RunAssignment, maps public labels to internal task ids, persists run state under `results/runs/{run_id}/run.json` |
| Session protocol plane | `bench/server/api/http_app.py`, `session_api.py`, `protocol.py` | Handles `/mcp` and `/session/*`, creates containers, runs student simulation, saves server results |
| Web/API read layer | `bench/server/web/ui_app.py`, `ui_indexer.py` | Serves Results UI data, Run creation/status/live endpoints, client trace upload endpoint |
| Client runner | `bench/client/runner.py`, `transports/*`, `trace_writer.py` | Claims/attaches to a run, executes via MCP/REST, saves client trace locally |
| Frontend | `bench/server/web/static/js/app.js`, `run-agent.js` | Results UI is mature; Run UI has My Agent flow and Human harness pieces |

### 1.2 Current Run lifecycle

```text
POST /ui/runs or /client/runs/start
  -> RunAssignment(status=waiting or claimed, token_hash stored)

POST /client/runs/claim
  -> waiting -> claimed

MCP /mcp or REST /session/register with Authorization: Bearer <run_token>
  -> creates SessionState
  -> claimed -> active

start_session / send_message / tools
  -> SessionState manages real work

session completed
  -> saves results/server/...
  -> active -> completed
```

### 1.3 Allowed state transitions (complete matrix)

```text
WAITING  -> CLAIMED    (claim_run)
WAITING  -> FAILED     (token expired via sweeper)
WAITING  -> CANCELLED  (cancel_run)

CLAIMED  -> ACTIVE     (bind_session)
CLAIMED  -> FAILED     (idle timeout via sweeper, or bind failure rollback)
CLAIMED  -> CANCELLED  (cancel_run)

ACTIVE   -> COMPLETED  (normal completion, timeout force-completion)
ACTIVE   -> FAILED     (unrecoverable error)
ACTIVE   -> CANCELLED  (cancel_run)

COMPLETED -> (terminal, no transitions)
FAILED    -> (terminal, no transitions)
CANCELLED -> (terminal, no transitions)
```

Any transition not listed above is illegal. `mark_completed`, `mark_failed`, `cancel_run` must enforce this matrix.

### 1.4 Per-endpoint auth requirements (complete table)

| Endpoint | Current auth | Target auth | Token type |
|----------|-------------|-------------|------------|
| `POST /ui/runs` | none | none (creates tokens) | — |
| `GET /ui/runs` | none | admin_token | admin |
| `GET /ui/runs/{id}` | none (by run_id) | control_token | control |
| `GET /ui/runs/{id}/live` | none (by run_id) | control_token | control |
| `POST /ui/runs/{id}/cancel` | none (by run_id) | control_token | control |
| `POST /client/runs/claim` | run_token (in body) | run_token (in body) | run |
| `POST /client/runs/start` | none | admin_token or env gate | admin |
| `POST /client/runs/{id}/trace` | none | run_token | run |
| `POST /mcp` (new session) | run_token (Bearer) | run_token (Bearer) | run |
| `POST /mcp` (continuation) | mcp-session-id header | mcp-session-id header (treat as bearer secret) | — |
| `POST /session/register` | run_token (Bearer) | run_token (Bearer) | run |
| `POST /session/{sid}/start` | session_id only | run_token (Bearer) | run |
| `POST /session/{sid}/send` | session_id only | run_token (Bearer) | run |
| `POST /session/{sid}/tool/{name}` | session_id only | run_token (Bearer) | run |
| `POST /session/{sid}/evaluate` | session_id only | control_token or run_token | control/run |
| `POST /session/{sid}/results` | session_id only | control_token or run_token | control/run |
| `GET /ui/results/*` | none | none (public archived data) | — |

Session endpoint auth (run_token on mutable endpoints) is Phase 5 hardening. Until then, session_id remains the key, same as current behavior.

---

## 2. Design Principles

### 2.1 Hosted-first, local-second

Local execution uses the same API and token flow as hosted execution. Local development points `API_BASE_URL` to `http://localhost:8000`. No local-only fallbacks that bypass Run or Session ownership.

Exception: `/client/runs/start` remains available in local mode (gated by `QTB_ALLOW_DIRECT_RUN=1`, default true in dev, false in hosted). This preserves existing test/CI workflows without violating the hosted-first principle.

### 2.2 Run is the public control object

Browser and client interact only with:

- `public_task_label`: e.g. `D01`
- `run_id`: opaque identifier
- tokens: opaque credentials

The server resolves `D01 -> D01_load_inspect_ohlcv` internally. The field name `task_id` must not appear in any public API response or request body. Use `task` or `public_task_label` instead.

### 2.3 Client trace is optional

- If uploaded, Results merges reasoning/content blocks/cost.
- If absent, Results shows server conversation, tool logs, workspace, and evaluation.
- Upload must be explicit (`--upload-trace` flag or `QTB_UPLOAD_TRACE=1` env).
- Trace upload is only accepted when the run is in `COMPLETED` state or within a 5-minute grace window after completion. This eliminates the race condition where `session_id` might not yet be bound to RunAssignment.

### 2.4 Token model

| Token | Format | Holder | Purpose | Stored as |
|-------|--------|--------|---------|-----------|
| `run_token` | `qtb_{token_urlsafe(24)}` | external agent/client | claim, connect, register, run, upload trace | `token_hash` (SHA-256) in RunAssignment |
| `control_token` | `qtc_{token_urlsafe(24)}` | browser owner | poll status, live data, cancel | `control_token_hash` (SHA-256) in RunAssignment |
| `admin_token` | `QTB_ADMIN_TOKEN` env var | server operator | list all runs, direct run start, cleanup | env var, never in RunAssignment |

Key constraints:

- `run_token` and `control_token` use different prefixes (`qtb_` vs `qtc_`) so `find_by_token_hash()` can distinguish them without ambiguity.
- `RunStore.find_by_token_hash()` must accept a `token_type` parameter (`"run"` or `"control"`) and check the corresponding hash field. Never match a run_token against control_token_hash or vice versa.
- `control_token` does not expire with `run_token`. It remains valid until the run reaches a terminal state plus a 30-minute review window.
- `public_dict()` must exclude both `token_hash` and `control_token_hash`.
- Frontend stores tokens keyed by `run_id` (not a single global variable) to support multiple concurrent runs in different tabs.

---

## 3. Implementation Steps

Steps are strictly ordered. Each step produces a shippable, testable state. If a step is interrupted, the system remains functional at the previous step's state.

### Step 1: Backend CORS (pure additive, no breaking changes)

**Problem**: No CORS headers. Vercel frontend cannot call VPS API.

**Changes**:

File: `bench/server/api/http_app.py`
- Add `CORSMiddleware` to the Starlette app:
  ```python
  from starlette.middleware.cors import CORSMiddleware
  app.add_middleware(
      CORSMiddleware,
      allow_origins=os.environ.get("QTB_ALLOWED_ORIGINS", "http://localhost:8000,http://localhost:3000").split(","),
      allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
      allow_headers=["Authorization", "Content-Type", "Mcp-Session-Id"],
      expose_headers=["Mcp-Session-Id"],
  )
  ```

File: `bench/server/api/http_app.py` — `handle_mcp_request()`
- The MCP endpoint uses `StreamableHTTPServerTransport` which writes its own HTTP responses, bypassing Starlette middleware. CORS headers must be injected directly in `handle_mcp_request()`:
  ```python
  CORS_HEADERS = {
      "Access-Control-Allow-Origin": _get_allowed_origin(request),
      "Access-Control-Allow-Headers": "Authorization, Content-Type, Mcp-Session-Id",
      "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
      "Access-Control-Expose-Headers": "Mcp-Session-Id",
  }
  # For OPTIONS preflight:
  if request.method == "OPTIONS":
      return Response(status_code=204, headers=CORS_HEADERS)
  # For all MCP responses: merge CORS_HEADERS into response headers
  ```
- `_get_allowed_origin(origin)`: check request `Origin` header against `QTB_ALLOWED_ORIGINS` allowlist. Return the matched origin or reject.

**Test**: `curl -H "Origin: http://localhost:3000" -X OPTIONS http://localhost:8000/mcp` returns 204 with correct CORS headers.

**Rollback**: Remove middleware and MCP CORS handler. No data model changes.

---

### Step 2: Frontend API base URL helper (pure additive)

**Problem**: All frontend fetch calls use relative paths.

**Changes**:

File: `bench/server/web/static/js/app.js`
- Add at the top:
  ```js
  window.QTB = window.QTB || {};
  window.QTB.apiBaseUrl = (window.QTB_CONFIG && window.QTB_CONFIG.apiBaseUrl) || '';

  window.QTB.apiFetch = function(path, options) {
      return fetch(window.QTB.apiBaseUrl + path, options);
  };
  ```
- Replace all `fetch('/ui/...')`, `fetch('/session/...')`, `fetch('/client/...')` calls with `QTB.apiFetch(...)`.
- Same for `run-agent.js`.

File: `bench/server/web/static/config.js` (new, optional)
- Loaded before `app.js` in `index.html`:
  ```js
  window.QTB_CONFIG = { apiBaseUrl: "" };
  ```
- For Vercel deployment, this file is overwritten at build time with the VPS URL.
- For local dev, empty string means same-origin (no change in behavior).

**Test**: Existing Results UI and Run UI continue to work with empty `apiBaseUrl`.

**Rollback**: Revert JS changes. No backend impact.

---

### Step 3: Run state transition hardening (backend correctness)

**Problem**: `mark_completed()` can overwrite terminal states. Sweeper and bind failure can leave Run/Session diverged.

**Changes**:

File: `bench/server/run/service.py`

- `mark_completed(run_id, result_dir)`:
  ```python
  def mark_completed(self, run_id: str, result_dir: str) -> RunAssignment:
      assignment = self._get_or_raise(run_id)
      if assignment.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
          if assignment.status == RunStatus.COMPLETED and assignment.result_dir == result_dir:
              return assignment  # idempotent
          raise ValueError(f"Cannot complete run in terminal state: {assignment.status.value}")
      if assignment.status != RunStatus.ACTIVE:
          raise ValueError(f"Cannot complete run in state: {assignment.status.value}")
      # ... set COMPLETED
  ```

- `mark_failed(run_id, error)`:
  ```python
  def mark_failed(self, run_id: str, error: str) -> RunAssignment:
      assignment = self._get_or_raise(run_id)
      if assignment.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
          return assignment  # idempotent for FAILED, no-op for other terminals
      # ... set FAILED
  ```

- `cancel_run(run_id)`:
  ```python
  def cancel_run(self, run_id: str) -> RunAssignment:
      assignment = self._get_or_raise(run_id)
      if assignment.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
          raise ValueError(f"Cannot cancel run in terminal state: {assignment.status.value}")
      # ... set CANCELLED
  ```

- `bind_session(run_id, session_id)` — add rollback on failure:
  ```python
  def bind_session(self, run_id: str, session_id: str) -> RunAssignment:
      assignment = self._get_or_raise(run_id)
      if assignment.status != RunStatus.CLAIMED:
          raise ValueError(f"Cannot bind session to run in state: {assignment.status.value}")
      try:
          assignment.session_id = session_id
          assignment.status = RunStatus.ACTIVE
          self._store.save(assignment)
          return assignment
      except Exception:
          # Rollback: leave run as CLAIMED so client can retry
          assignment.session_id = None
          assignment.status = RunStatus.CLAIMED
          self._store.save(assignment)
          raise
  ```

File: `bench/server/api/http_app.py` — `_session_sweeper()`
- In the IN_SESSION timeout branch, after force-completing the session and saving results, call the Run completion callback:
  ```python
  # After session.save_results(result_dir):
  if state.run_id:
      try:
          self._run_service.mark_completed(state.run_id, result_dir)
      except ValueError:
          pass  # Run already in terminal state (e.g. cancelled during timeout)
  ```

**Tests** (add to `bench/tests/unit/`):
- `test_mark_completed_rejects_cancelled_run`
- `test_mark_completed_rejects_failed_run`
- `test_mark_completed_idempotent_same_result_dir`
- `test_cancel_rejects_terminal_states`
- `test_bind_session_rollback_on_error`
- `test_sweeper_timeout_marks_run_completed`

**Rollback**: Revert service.py and sweeper changes. State machine was already permissive, so reverting just removes guards.

---

### Step 4: control_token on RunAssignment and RunService

**Problem**: Run endpoints are public by `run_id` alone.

**Changes**:

File: `bench/server/run/models.py`
- Add fields to `RunAssignment`:
  ```python
  control_token_hash: str = ""
  control_token_hint: str = ""  # first 8 chars of raw token
  ```
- `public_dict()`: exclude `control_token_hash` (already excludes `token_hash`).
- `from_dict()` / `to_dict()`: handle new fields with empty-string defaults for backward compatibility with existing `run.json` files.

File: `bench/server/run/service.py`
- `create_run()`:
  ```python
  control_token = f"qtc_{secrets.token_urlsafe(24)}"
  assignment.control_token_hash = hashlib.sha256(control_token.encode()).hexdigest()
  assignment.control_token_hint = control_token[:12]
  # return both tokens to caller
  return assignment, raw_token, control_token
  ```
- Add `verify_control_token(run_id, raw_control_token) -> RunAssignment`:
  ```python
  def verify_control_token(self, run_id: str, raw_control_token: str) -> RunAssignment:
      assignment = self._get_or_raise(run_id)
      expected = assignment.control_token_hash
      actual = hashlib.sha256(raw_control_token.encode()).hexdigest()
      if not expected or not hmac.compare_digest(expected, actual):
          raise PermissionError("Invalid control token")
      return assignment
  ```
- `find_by_token_hash()` — add `token_type` parameter:
  ```python
  def find_by_token_hash(self, token_hash: str, token_type: str = "run") -> Optional[RunAssignment]:
      field = "token_hash" if token_type == "run" else "control_token_hash"
      # scan and compare against the correct field
  ```

File: `bench/server/run/store.py`
- `find_by_token_hash(token_hash, token_type="run")` — pass through to match the correct field.

File: `bench/server/web/ui_app.py`
- `POST /ui/runs`: return `control_token` in response (shown once).
- `GET /ui/runs/{run_id}`: require `Authorization: Bearer <control_token>`, call `verify_control_token()`. Return 401 on failure.
- `GET /ui/runs/{run_id}/live`: same control_token check.
- `POST /ui/runs/{run_id}/cancel`: same control_token check.
- `GET /ui/runs`: require `Authorization: Bearer <admin_token>` where admin_token is `os.environ.get("QTB_ADMIN_TOKEN")`. Return 401/403 if missing or wrong. If `QTB_ADMIN_TOKEN` is not set, allow (local dev convenience).

File: `bench/server/web/static/js/run-agent.js`
- After `POST /ui/runs`, store `{ run_id, control_token }` in a JS Map keyed by `run_id`.
- On page navigation within SPA, persist the Map to `sessionStorage` as `JSON.stringify(Object.fromEntries(map))` keyed by `"qtb_run_tokens"`.
- On page load, restore the Map from `sessionStorage`.
- All subsequent fetch calls to `/ui/runs/{run_id}`, `/live`, `/cancel` include `Authorization: Bearer <control_token>`.
- Handle 401 response: show "Run access expired or token lost. Cannot resume monitoring." instead of silently failing.

**Tests**:
- `test_create_run_returns_control_token`
- `test_get_run_without_control_token_returns_401`
- `test_get_run_with_wrong_control_token_returns_401`
- `test_get_run_with_correct_control_token_succeeds`
- `test_cancel_requires_control_token`
- `test_list_runs_requires_admin_token`
- `test_find_by_token_hash_distinguishes_run_and_control`

**Rollback**: Remove control_token fields and checks. Existing run.json files will have empty control_token_hash, which means `verify_control_token` always fails — so rollback must also remove the auth checks on endpoints.

---

### Step 5: Label-only Run catalog

**Problem**: `TaskCatalog.list_public()` returns category and difficulty, which should not be shown in exam/run flow.

**Changes**:

File: `bench/server/run/catalog.py`
- Add `list_labels_only()`:
  ```python
  def list_labels_only(self) -> list[dict]:
      return [{"label": e.public_label} for e in sorted(self._entries.values(), key=lambda e: e.public_label)]
  ```

File: `bench/server/web/ui_app.py`
- Add `GET /ui/tasks/catalog/labels` that calls `list_labels_only()`.
- Existing `GET /ui/tasks` remains unchanged (used by Results UI, not Run UI).

File: `bench/server/web/static/js/run-agent.js`
- Change task catalog fetch from `/ui/tasks/catalog` to `/ui/tasks/catalog/labels`.

**Tests**:
- `test_list_labels_only_returns_no_category_or_difficulty`

**Rollback**: Remove new endpoint and revert JS fetch URL.

---

### Step 6: Client trace upload alignment

**Problem**: Trace upload endpoint exists but is unauthenticated. Client runner does not call it. Race condition if upload happens before session is bound.

**Changes**:

File: `bench/server/web/ui_app.py` — `POST /client/runs/{run_id}/trace`
- Require `Authorization: Bearer <run_token>`.
- Reject if run status is not `COMPLETED` (eliminates race condition with unbound session_id).
- Allow upload within 5 minutes of `completed_at` timestamp.
- Reject if body `session_id` does not match `assignment.session_id`.
- Add 10MB max body size check.
- Do not use field name `task_id` in the request body. Use `task_label` instead.

File: `bench/client/runner.py` — `run_via_attach()`
- Add `upload_trace: bool` parameter (default `False`).
- After `save_client_trace()`, if `upload_trace` is True:
  ```python
  async def _upload_trace(server_url, run_id, token, trace_data):
      async with httpx.AsyncClient() as client:
          resp = await client.post(
              f"{server_url}/client/runs/{run_id}/trace",
              headers={"Authorization": f"Bearer {token}"},
              json=trace_data,
              timeout=30.0,
          )
          if resp.status_code != 200:
              logger.warning("Trace upload failed: %s", resp.text)
          # Never raise — trace upload failure does not fail the benchmark
  ```

File: `bench/client/__main__.py`
- Add `--upload-trace` flag to `attach` subcommand.
- Add `QTB_UPLOAD_TRACE` env var support.

**API body** (corrected — no `task_id` field):

```json
{
  "session_id": "...",
  "task_label": "D01",
  "timestamp": "...",
  "duration_seconds": 123.4,
  "agent_cost": {},
  "thinking_trace": [],
  "content_blocks": {}
}
```

**Tests**:
- `test_trace_upload_requires_run_token`
- `test_trace_upload_rejects_non_completed_run`
- `test_trace_upload_rejects_mismatched_session_id`
- `test_trace_upload_succeeds_with_valid_token_and_session`
- `test_run_completes_without_trace_upload`

**Rollback**: Remove auth check and upload call. Endpoint remains but unauthenticated (same as current).

---

### Step 7: Gate `/client/runs/start` for hosted mode

**Problem**: Direct run creation via client bypasses the web Run flow. But existing tests and CI depend on it.

**Changes**:

File: `bench/server/web/ui_app.py` — `POST /client/runs/start`
- Check `os.environ.get("QTB_ALLOW_DIRECT_RUN", "1")`.
- If `"0"` or `"false"`: require `Authorization: Bearer <admin_token>`. Return 403 if missing/wrong.
- If `"1"` or `"true"` (default): allow without auth (current behavior, local dev).
- Add response header `X-QTB-Warning: direct-run` to signal this is a dev convenience.

File: `bench/client/__main__.py`
- Reorder help text: `attach` listed first as primary flow, `run` listed second with "(dev/local)" annotation.

**No changes to existing tests or CI** — they run with default `QTB_ALLOW_DIRECT_RUN=1`.

**Rollback**: Remove env check. No data model changes.

---

### Step 8: MCP register_session ignores body task_id

**Problem**: MCP `register_session` tool still accepts `task_id` in arguments. With run_token flow, server should resolve task from RunAssignment, not trust the client-provided value.

**Changes**:

File: `bench/server/api/http_app.py` — MCP `register_session` handler
- If the request has a valid `run_token` (Bearer header) and the resolved RunAssignment has a `task_id`:
  - Use `assignment.task_id` regardless of what `task_id` argument the client passes.
  - Log a warning if client-provided `task_id` differs from assignment's.
- If no run_token (legacy / local flow): continue using `task_id` from arguments (backward compatible).

File: `bench/server/api/protocol.py`
- `register_session` tool definition: mark `task_id` as optional (it already is — confirm).

**Tests**:
- `test_mcp_register_with_run_token_ignores_body_task_id`
- `test_mcp_register_without_run_token_uses_body_task_id`

**Rollback**: Remove the override logic. MCP always uses body task_id (current behavior).

---

### Step 9: Frontend Run UI updates (depends on Steps 2, 4, 5)

**Problem**: `run-agent.js` needs to use API base helper, control_token, and label-only catalog.

**Changes** (all in `bench/server/web/static/js/run-agent.js`):

- All fetch calls use `QTB.apiFetch()`.
- Create run: `POST /ui/runs` → store `{ run_id, control_token, run_token }` in per-run-id Map.
- Task selector: fetch from `/ui/tasks/catalog/labels`, show only labels like `D01`.
- Connection card: display `mcp_url`, `run_token`, and launch command from create response.
- Status polling: `GET /ui/runs/{run_id}` with `Authorization: Bearer <control_token>`.
- Live polling: `GET /ui/runs/{run_id}/live` with `Authorization: Bearer <control_token>`.
- Cancel: `POST /ui/runs/{run_id}/cancel` with `Authorization: Bearer <control_token>`.
- Handle 401 on any poll: show "Session expired" message, stop polling.
- On completion: link to Results page by `session_id`.
- Missing client trace: show "Server-side replay only. Client trace not uploaded." in Results detail.

**Do not remove** `persona_policy` from `POST /ui/runs` request body. It is a server-internal parameter used for testing different persona strategies. The frontend should not expose it as a user-facing dropdown — hardcode `"auto"` in the JS. If needed for testing, pass via URL query param `?persona_policy=fixed`.

**Smoke tests** (manual):
- Create run from Vercel-hosted page → connection card appears.
- Copy launch command → run client locally → live view updates.
- Cancel button works during active run.
- Refresh page → polling resumes (sessionStorage restored).
- Open two runs in two tabs → each has its own control_token.

**Rollback**: Revert JS. Backend changes from prior steps remain valid independently.

---

## 4. Pre-Launch Hardening (Phase 5, after Steps 1-9 are stable)

These are required before public launch but do not block the next implementation steps.

### 4.1 Session endpoint ownership

After REST registration, `/session/{sid}/start`, `/send`, `/tool/{name}` are keyed only by `session_id`. Before public launch:

- Require `Authorization: Bearer <run_token>` on all mutable session endpoints.
- Server validates that the token's run_id matches the session's bound run_id.
- Read-only endpoints (`/results`, `/scores`) remain public for archived data.

### 4.2 MCP session continuation security

`mcp-session-id` is treated as a bearer secret:
- Never return it in any `/ui/*` or `/client/*` API response.
- The value is only visible to the MCP client that received it during initialization.
- If `mcp-session-id` leaks, the attacker can send messages to the session. This is acceptable for the next phase because `mcp-session-id` is random and unguessable, but should be reviewed before public launch.

### 4.3 Admin endpoints

- `GET /ui/runs` requires `QTB_ADMIN_TOKEN`.
- Future admin endpoints (cleanup, bulk operations) also require admin token.
- Admin token is never embedded in Vercel static JS. Admin operations use a separate tool (curl, admin CLI, or Vercel serverless proxy with secret env vars).

### 4.4 Rate limiting

Before public launch, add rate limits:

| Endpoint | Limit |
|----------|-------|
| `POST /ui/runs` | 10/min per IP |
| `POST /client/runs/claim` | 20/min per IP |
| `POST /session/register` | 20/min per IP |
| `POST /session/{sid}/tool/*` | 60/min per session |
| `POST /client/runs/{id}/trace` | 5/min per run |

Max body size: 10MB for trace upload, 1MB for all other JSON endpoints.

### 4.5 Storage layer

For single-process VPS launch, JSON file storage is acceptable with:
- Atomic writes via `tempfile + os.rename` (add to `RunStore.save()`).
- Single ASGI worker (enforce via deployment config).
- Monitor file count; alert if > 10k run directories.

SQLite migration deferred to multi-worker or multi-instance phase.

### 4.6 Client trace privacy

- Upload is opt-in only (`--upload-trace`).
- Results UI labels trace as "Client-provided trace data".
- Upload consent text: "Trace may include model reasoning, tool inputs, and cost data."
- Admin cleanup endpoint to delete uploaded traces.

### 4.7 Concurrent runs and resource limits

- Each active session spawns a Docker container. Set `QTB_MAX_CONCURRENT_RUNS` env var (default: 5).
- `POST /ui/runs` and `/client/runs/start` return 503 if active run count >= limit.
- Sweeper already handles cleanup of idle/timed-out sessions.

### 4.8 Crash recovery

On server restart:
- `RunStore` persists all run state to disk — no data loss.
- In-memory `_sessions` dict is lost. Runs in ACTIVE state have no live session.
- Sweeper should detect ACTIVE runs with no in-memory session and mark them FAILED with error "Server restarted during active session".
- Add to `BenchSessionManager.__aenter__()`:
  ```python
  # Recover orphaned active runs
  for assignment in self._run_store.list_runs(status=RunStatus.ACTIVE):
      if assignment.run_id not in self._sessions:
          self._run_service.mark_failed(assignment.run_id, "Server restarted during active session")
  ```

---

## 5. Proposed API Shape

### 5.1 Create a browser-owned run

```http
POST /ui/runs
Content-Type: application/json

{
  "task": "D01",
  "mode": "agent"
}
```

Response:

```json
{
  "run_id": "run_...",
  "public_task_label": "D01",
  "status": "waiting",
  "run_token": "qtb_...",
  "control_token": "qtc_...",
  "token_expires_at": "...",
  "mcp_url": "https://api.example.com/mcp",
  "rest_base_url": "https://api.example.com",
  "launch_command": "python -m client attach --server https://api.example.com --run-token qtb_..."
}
```

Notes:

- `run_token` displayed once. Copied into external client.
- `control_token` kept in browser state. Stored in sessionStorage keyed by `run_id`.
- Response does not include `task_id`.

### 5.2 Browser owner polls status

```http
GET /ui/runs/{run_id}
Authorization: Bearer <control_token>
```

Response:

```json
{
  "run_id": "run_...",
  "public_task_label": "D01",
  "status": "active",
  "session_id": "...",
  "eval_status": "pending",
  "created_at": "...",
  "updated_at": "..."
}
```

### 5.3 Browser owner polls live replay

```http
GET /ui/runs/{run_id}/live
Authorization: Bearer <control_token>
```

Response:

```json
{
  "run_status": "active",
  "session_phase": "in_session",
  "turn": 4,
  "conversation": [],
  "send_message_events": [],
  "recent_tool_logs": []
}
```

Note: Do not expose raw reasoning unless explicitly intended for owner-only debug.

### 5.4 External client claims a run

```http
POST /client/runs/claim
Content-Type: application/json

{
  "run_token": "qtb_...",
  "client": {"name": "baseline_client", "version": "0.1.0"}
}
```

Response:

```json
{
  "run_id": "run_...",
  "mcp_url": "https://api.example.com/mcp",
  "rest_base_url": "https://api.example.com",
  "public_task_label": "D01",
  "status": "claimed"
}
```

### 5.5 External client connects to session

MCP:

```http
POST /mcp
Authorization: Bearer <run_token>
```

REST:

```http
POST /session/register
Authorization: Bearer <run_token>
```

Body does not need `task_id` in hosted Run mode. Server resolves from RunAssignment.

### 5.6 Optional trace upload

```http
POST /client/runs/{run_id}/trace
Authorization: Bearer <run_token>
Content-Type: application/json

{
  "session_id": "...",
  "task_label": "D01",
  "timestamp": "...",
  "duration_seconds": 123.4,
  "agent_cost": {},
  "thinking_trace": [],
  "content_blocks": {}
}
```

Response:

```json
{
  "status": "uploaded",
  "has_client_trace": true
}
```

Constraints:

- Run must be in `COMPLETED` state.
- `session_id` must match `assignment.session_id`.
- Max body size: 10MB.
- No filesystem paths in response.
- Upload allowed within 5 minutes of `completed_at`.

---

## 6. Test Plan

### 6.1 Unit tests (added with each Step)

| Test | Step |
|------|------|
| `test_mark_completed_rejects_cancelled_run` | 3 |
| `test_mark_completed_rejects_failed_run` | 3 |
| `test_mark_completed_idempotent_same_result_dir` | 3 |
| `test_cancel_rejects_terminal_states` | 3 |
| `test_bind_session_rollback_on_error` | 3 |
| `test_sweeper_timeout_marks_run_completed` | 3 |
| `test_create_run_returns_control_token` | 4 |
| `test_find_by_token_hash_distinguishes_run_and_control` | 4 |
| `test_list_labels_only_returns_no_category_or_difficulty` | 5 |
| `test_trace_upload_requires_run_token` | 6 |
| `test_trace_upload_rejects_non_completed_run` | 6 |
| `test_trace_upload_rejects_mismatched_session_id` | 6 |
| `test_trace_upload_succeeds_with_valid_token_and_session` | 6 |
| `test_run_completes_without_trace_upload` | 6 |
| `test_mcp_register_with_run_token_ignores_body_task_id` | 8 |
| `test_mcp_register_without_run_token_uses_body_task_id` | 8 |

### 6.2 API tests (added with each Step)

| Test | Step |
|------|------|
| `test_cors_preflight_on_starlette_routes` | 1 |
| `test_cors_preflight_on_mcp_endpoint` | 1 |
| `test_get_run_without_control_token_returns_401` | 4 |
| `test_get_run_with_wrong_control_token_returns_401` | 4 |
| `test_get_run_with_correct_control_token_succeeds` | 4 |
| `test_cancel_requires_control_token` | 4 |
| `test_list_runs_requires_admin_token` | 4 |
| `test_direct_run_start_blocked_in_hosted_mode` | 7 |

### 6.3 Frontend smoke tests (Step 9)

- Vercel-hosted page can call VPS API via configured base URL.
- My Agent create page shows only public labels (no category/difficulty).
- Connection card displays `mcp_url` and launch command.
- Live view uses control_token and stops with error message on 401.
- Refresh page resumes polling (sessionStorage).
- Two concurrent runs in separate tabs each poll independently.
- Missing client trace shows server-only replay message.

### 6.4 Regression tests to keep

- Session lifecycle tests.
- Protocol phase permission tests.
- Results UI indexer and workspace preview tests.
- `send_message` split from domain tool replay tests.

---

## 7. Decisions Made (formerly Open Questions)

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | API_BASE_URL in JS or Vercel rewrites? | Explicit `API_BASE_URL` in JS via `config.js` | Makes deployment boundary visible in code. Vercel rewrites hide the boundary and complicate debugging. |
| 2 | control_token expiry? | Stays valid until terminal state + 30min review window. Independent of run_token expiry. | Browser owner needs to monitor after agent finishes. run_token expiry only affects claim/connect. |
| 3 | Trace upload token? | Reuse `run_token`. | Adding a third token type increases complexity for no security gain at this stage. run_token already identifies the authorized client. |
| 4 | Archived Results public? | Public by session_id. | Results data does not contain secrets. Restricting access adds complexity without clear threat model. Revisit before public launch if needed. |
| 5 | `/client/runs/start` gating? | Env flag `QTB_ALLOW_DIRECT_RUN` (default `1` in dev, `0` in hosted). | Preserves existing test/CI workflows. No code changes needed in tests. |
| 6 | `mcp-session-id` as secret? | Treat as bearer secret. Never expose in API responses. | Simpler than adding bearer auth to MCP continuation. MCP session-id is random and unguessable. |
| 7 | RunStore: JSON or SQLite? | JSON for now. Atomic writes via temp+rename. Single worker. | Sufficient for single-VPS launch. SQLite migration planned for multi-worker phase. |

---

## 8. Implementation Schedule and Risk Assessment

The schedule is driven by one hard constraint: **changes must not break the existing task-running and scoring workflow**. The two critical flows are:

1. `python -m client run --task D01` → `/client/runs/start` → `create_and_claim()` → MCP/REST session → completion → results saved
2. `pytest bench/tests/` → `helpers.create_run()` calls `/client/runs/start` → session lifecycle → evaluation

Any step that alters endpoint contracts, changes `RunService` return signatures, or adds mandatory auth to endpoints used by these flows is deferred until Vercel deployment is imminent.

### 8.1 Tier 1 — Do now (zero risk to existing flows)

These are pure additive changes. No existing endpoint behavior changes. No model changes. Run `pytest bench/tests/ -x` after each step to confirm.

| Step | What changes | Why zero risk | Effort |
|------|-------------|---------------|--------|
| **Step 1: CORS** | Add Starlette `CORSMiddleware` + MCP endpoint CORS headers in `http_app.py` | CORS logic only activates when request has `Origin` header. Client CLI and pytest never send `Origin`. No existing response is modified. | ~10 min |
| **Step 2: Frontend API base** | Add `QTB.apiFetch()` in `app.js`/`run-agent.js`, create `config.js` | Empty `apiBaseUrl` = same-origin = identical behavior. Pure JS change, no backend touched. | ~20 min |
| **Step 5: Label-only catalog** | Add `list_labels_only()` to `catalog.py`, add `GET /ui/tasks/catalog/labels` to `ui_app.py` | New method + new endpoint. Existing `list_public()` and `GET /ui/tasks` untouched. Frontend not switched yet. | ~10 min |

**Verification after Tier 1:**
```bash
pytest bench/tests/ -x -v
# All existing tests must pass. No new tests required (these are additive).
```

### 8.2 Tier 2 — Do soon (safe but needs full test verification)

State machine hardening. Changes internal guards on `RunService` methods. Existing flows do not intentionally make illegal transitions, but edge cases in sweeper timing could surface.

| Step | What changes | Risk point | Why it is safe |
|------|-------------|------------|----------------|
| **Step 3: State transition hardening** | Terminal guards on `mark_completed`/`mark_failed`/`cancel_run`; `bind_session` rollback; sweeper timeout calls run completion | Sweeper calls `mark_failed()` on WAITING/CLAIMED runs. If the run was already marked terminal by another path, the guard could throw. | `mark_failed` is designed **idempotent on terminal states** — it returns silently if the run is already FAILED/COMPLETED/CANCELLED. Only `mark_completed` and `cancel_run` throw on illegal transitions, and neither is called by the sweeper on WAITING/CLAIMED runs. |

Detailed safety analysis of each guard:

```
mark_failed(run_id, error):
  WAITING/CLAIMED/ACTIVE → FAILED     ✓ (normal)
  FAILED                 → return     ✓ (idempotent, no-op)
  COMPLETED              → return     ✓ (no-op, already done)
  CANCELLED              → return     ✓ (no-op, already cancelled)
  → sweeper calls mark_failed, so this MUST be idempotent. Never throws.

mark_completed(run_id, result_dir):
  ACTIVE                 → COMPLETED  ✓ (normal)
  COMPLETED + same dir   → return     ✓ (idempotent)
  COMPLETED + diff dir   → throw      ✓ (bug detection)
  FAILED/CANCELLED       → throw      ✓ (correct — should not happen)
  WAITING/CLAIMED        → throw      ✓ (correct — should not happen)
  → called by session completion callback and sweeper timeout path.
    Sweeper timeout: force_complete session → mark_completed run.
    If run was cancelled during timeout window, catch ValueError and ignore.

cancel_run(run_id):
  WAITING/CLAIMED/ACTIVE → CANCELLED  ✓ (normal)
  COMPLETED/FAILED/CANCELLED → throw  ✓ (correct — UI should not show cancel for terminal)
  → called by UI cancel button. Frontend should disable cancel for terminal states.
```

**Verification after Tier 2:**
```bash
# Full test suite
pytest bench/tests/ -x -v

# Manual end-to-end (run one task through completion)
python -m client run --server http://localhost:8000 --task X01

# Manual edge case (start a run, let it timeout via sweeper, check run.json)
# 1. Create run via POST /ui/runs
# 2. Don't claim it
# 3. Wait > token TTL
# 4. Check results/runs/{run_id}/run.json shows status=failed
```

### 8.3 Tier 3 — Do before Vercel deployment (touches core models and endpoint contracts)

These steps change `RunAssignment` fields, `RunService.create_run()` return signature, and add mandatory auth to UI endpoints. They should be implemented as **one atomic batch** because they depend on each other and the frontend must be updated simultaneously.

| Step | What changes | What breaks if done alone |
|------|-------------|--------------------------|
| **Step 4: control_token** | `RunAssignment` gets `control_token_hash`/`control_token_hint`. `create_run()` returns 3-tuple `(assignment, run_token, control_token)`. `GET /ui/runs/{id}`, `/live`, `/cancel` require `Authorization: Bearer <control_token>`. `GET /ui/runs` requires admin token. | `POST /ui/runs` handler in `ui_app.py` must be updated to destructure 3-tuple. If only `service.py` is changed without `ui_app.py`, the handler crashes with `ValueError: too many values to unpack`. Manual browser debugging of run status becomes impossible without token. |
| **Step 8: MCP register ignores body task_id** | `handle_mcp_request` adds branch: if run_token present, use `assignment.task_id` instead of body value. | Safe in isolation (legacy flow unchanged), but the logic shares the token resolution path with Step 4. Easier to review and test together. |
| **Step 9: Frontend Run UI** | `run-agent.js` uses `apiFetch`, stores control_token per run_id, fetches labels-only catalog. | Cannot work without Step 4 (no control_token to store) and Step 5 (no labels endpoint to fetch). |

**Implementation order within this batch:**
1. Step 4 backend (models + service + ui_app auth)
2. Step 8 backend (MCP register override)
3. Step 9 frontend (JS uses new auth + labels)
4. Run full test suite + manual smoke test

**Key safety check:** `create_and_claim()` (used by `/client/runs/start` and all tests) must **not** change its return signature. It should continue returning `(assignment, raw_token)`. Only `create_run()` (used by `POST /ui/runs`) returns the 3-tuple. Verify:

```python
# service.py — create_and_claim must stay as-is:
def create_and_claim(self, task, client_info, ...) -> tuple[RunAssignment, str]:
    assignment, raw_token = self.create_run(task, ...)  # ← WRONG, would break
    # Instead, create_and_claim should generate its own token internally
    # or create_run should return a result object instead of a tuple
```

**Recommended approach:** Change `create_run()` to return a `CreateRunResult` dataclass instead of a bare tuple, so `create_and_claim()` can selectively ignore `control_token`:

```python
@dataclass
class CreateRunResult:
    assignment: RunAssignment
    run_token: str
    control_token: str  # empty string for create_and_claim (no browser owner)
```

This avoids fragile tuple destructuring and makes it explicit that `create_and_claim` runs have no control_token (they are client-initiated, not browser-initiated).

**Verification after Tier 3:**
```bash
# Full test suite (must pass — create_and_claim unchanged)
pytest bench/tests/ -x -v

# Manual end-to-end via attach flow
# 1. POST /ui/runs → get run_token + control_token
# 2. python -m client attach --server http://localhost:8000 --run-token qtb_...
# 3. GET /ui/runs/{id} with control_token → 200
# 4. GET /ui/runs/{id} without token → 401

# Manual: open run-agent page in browser, create run, verify connection card
```

### 8.4 Tier 4 — Do before public launch (no impact on local workflows)

These steps add restrictions and features for multi-user hosted scenarios. They are irrelevant to single-machine development and testing.

| Step | Why it can wait | Trigger to implement |
|------|----------------|---------------------|
| **Step 6: Trace upload auth** | Current trace upload endpoint is unused by any code path. Adding auth to a dead endpoint has no value. | When `--upload-trace` flag has a real use case (external users running benchmarks). |
| **Step 7: Gate `/client/runs/start`** | Default `QTB_ALLOW_DIRECT_RUN=1` means behavior is unchanged. The env flag only matters when deploying with `=0`. | When VPS is deployed and you want to prevent unauthenticated run creation. |
| **Phase 5: Session endpoint ownership** | `/session/{sid}/*` endpoints are keyed by `session_id` which is unguessable. Acceptable for single-user VPS. | When multiple untrusted users share the same VPS. |
| **Phase 5: Rate limiting** | No external users = no abuse. | Before opening the VPS to public traffic. |
| **Phase 5: Crash recovery** | Single-user can manually re-run failed tasks. | When unattended operation is required (overnight batch runs on VPS). |
| **Phase 5: Atomic RunStore writes** | Single ASGI worker + threading.Lock = safe for single-process. | When moving to multi-worker deployment. |
| **Phase 5: Concurrent run limits** | Single user manages their own concurrency. | When external users can create runs. |
| **Phase 5: Trace privacy** | No external users uploading traces. | When trace upload is enabled for external users. |

---

## 9. Implementation Checklist

### Tier 1 — Now (zero risk)
- [x] ~~Step 1: Backend CORS (Starlette middleware + MCP endpoint CORS headers)~~ — architecturally replaced by Vercel edge rewrites in `vercel-frontend/vercel.json`; same-origin from the browser.
- [x] ~~Step 2: Frontend API base URL helper (`QTB.apiFetch`, `config.js`)~~ — architecturally replaced; frontend uses relative URLs.
- [x] Step 5: Label-only Run catalog endpoint (`3b0b48f`, merged via PR #20)
- [x] Verify: `pytest bench/tests/ -x -v` all pass — 94/94 on `master@7ed0f99`.

### Tier 2 — Soon (safe, needs verification)
- [x] Step 3: Run state transition hardening (terminal guards, bind rollback, sweeper fix) (`3b0b48f`, merged via PR #20)
- [x] Verify: `pytest bench/tests/ -x -v` all pass — 94/94 on `master@7ed0f99`.
- [x] Verify: external availability smoke against the live VPS — 22/22 PASS via `bench/scripts/prod_smoke.py` (PR #22).

### Tier 3 — Before Vercel deployment (one atomic batch)
- [ ] Step 4: control_token on RunAssignment and RunService + endpoint auth
- [ ] Step 8: MCP register_session ignores body task_id when run_token present
- [ ] Step 9: Frontend Run UI updates (apiFetch, control_token, labels-only)
- [ ] Verify: `pytest bench/tests/ -x -v` all pass (create_and_claim unchanged)
- [ ] Verify: manual attach flow with control_token works
- [ ] Verify: browser Run UI creates run and monitors via control_token

### Tier 4 — Before public launch
- [ ] Step 6: Client trace upload auth + client-side `--upload-trace`
- [ ] Step 7: Gate `/client/runs/start` with env flag
- [ ] Phase 5: Pre-launch hardening (session auth, rate limits, crash recovery, trace privacy)
