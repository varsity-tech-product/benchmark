# QuantTutorBench Web Dashboard — Technical Documentation

## 1. System Overview

The QuantTutorBench Web Dashboard is a full-stack web application (FastAPI + vanilla JS SPA) that provides a visual interface for the QuantTutorBench quantitative tutoring evaluation framework. Users can run tasks, monitor progress in real time, evaluate results, and browse saved runs — all from the browser, replacing the traditional CLI workflow.

**Launch:**
```bash
cd bench && python -m web.server [--port 8765] [--host 0.0.0.0]
```

**Tech stack:**
- Backend: FastAPI + Uvicorn + SSE-Starlette
- Frontend: Vanilla JavaScript SPA (no framework), hash-based routing, marked.js + KaTeX rendering
- Real-time: Server-Sent Events (SSE)
- Styling: Custom CSS (warm Claude-inspired theme with automatic dark mode)

**Codebase size (5264 lines total):**

| File | Lines | Responsibility |
|---|---|---|
| `static/js/app.js` | 1910 | SPA router, page logic, SSE event handling |
| `static/css/styles.css` | 1543 | All styles (responsive + dark mode) |
| `api/runs.py` | 505 | Run / evaluate / stop API endpoints |
| `static/js/chat.js` | 440 | Chat UI components (avatars, bubbles, content blocks) |
| `static/index.html` | 252 | HTML entry + 6 `<template>` tags |
| `static/js/tools.js` | 236 | Tool call panel components |
| `api/results.py` | 125 | Result directory scanning + file serving |
| `static/js/render.js` | 99 | Markdown + LaTeX render pipeline |
| `server.py` | 68 | FastAPI application entry point |
| `api/tasks.py` | 59 | Task / persona listing endpoints |
| `api/events.py` | 27 | SSE event stream endpoint |

---

## 2. Backend Architecture

### 2.1 Application Entry (`server.py`)

The FastAPI app initializes via a `lifespan` context manager:
- Acquires the current asyncio event loop
- Calls `orchestrator.live_monitor.init_async(loop, event_queue)` to switch `emit()` into async mode
- This allows background threads to safely push events into the asyncio Queue via `loop.call_soon_threadsafe()`

Mounts 4 API routers (under `/api` prefix) + static file serving + SPA entry point (`index.html`).

### 2.2 API Endpoints

#### `GET /api/tasks` — Task listing
Scans `bench/tasks/layer2/` directory tree, returns all task JSONs (task_id, category, difficulty, description, persona_ids, max_turns, requires_code).

#### `GET /api/tasks/{task_id}` — Task detail
Returns the complete task JSON definition.

#### `GET /api/personas` — Persona listing
Scans `bench/personas/` directory, returns persona_id, knowledge_level, and description.

#### `GET /api/results` — Result listing
Scans both `results/run-single/` and `results/run-group/` directory trees, traversing the 6-level folder hierarchy (source → agent → model → category → task_id → persona_id), reading each `run_state.json` for summary info (duration, turn count, tool call count, scored status, etc.).

#### `GET /api/results/{source}/{agent}/{model}/{category}/{task_id}/{persona_id}` — Result detail
Returns the full `run_state.json` content, with additional `_timestamp`, `_scores_md` (score report), and `_cost_md` (cost report) fields attached.

#### `GET /api/results/.../files/{filename}` — Result file serving
Serves images, CSVs, etc. from the `agent_files/` directory. Includes path traversal security check.

#### `POST /api/run` — Start single task run
Accepts `RunRequest` (task_id, persona_id, agent, docker, max_turns, model, skip_eval). Executes `run_single_job()` in a background thread. Mutex lock ensures only one run at a time.

#### `POST /api/run-group` — Start group run
Accepts `RunGroupRequest` (group, agent, persona, docker, max_turns, workers). Uses `run_jobs_parallel()` with a ThreadPoolExecutor to execute all tasks in a category concurrently.

#### `POST /api/eval` — Start evaluation
Accepts `EvalRequest` (source, agent, model, category, task_id, persona_id). Executes `eval_single_job()` in a background thread. Supports concurrency (multiple evals can run simultaneously).

#### `POST /api/eval/stop` — Stop evaluation
Accepts `EvalStopRequest` (task_id, persona_id). Sets a `threading.Event` cancellation flag. The eval thread checks this flag at key checkpoints (after task loading, after persona loading, before/after eval execution) and raises `InterruptedError` to abort.

#### `GET /api/events` — SSE event stream
Uses `sse_starlette.EventSourceResponse`, continuously reading from an `asyncio.Queue` and pushing events. Sends heartbeat every 15 seconds to keep the connection alive.

#### `GET /api/status` — Run status
Returns current run task info and list of all active evaluations.

### 2.3 SSE Event Protocol

All events are emitted via `orchestrator.live_monitor.emit()`. In web mode, they are delivered through `loop.call_soon_threadsafe()` into the asyncio Queue:

| Event Type | Context | Key Fields | Trigger |
|---|---|---|---|
| `session_start` | Run/Eval | task_id, persona_id, agent, mode? | Task/eval begins |
| `student_message` | Run | content, turn_index | Student message sent |
| `tutor_response` | Run | content, content_blocks? | Tutor replies |
| `tool_start` | Run | name, args, call_id | Tool call begins |
| `tool_result` | Run | call_id, result, success, duration_ms | Tool call completes |
| `session_end` | Run/Eval | task_id, error?, scores?, source, agent, model, category, mode? | Task/eval ends |
| `eval_step` | Eval | task_id, persona_id, step, status, score? | Eval step progress |
| `group_start` | Group | group, agent, model, total_jobs, jobs[] | Group run begins |
| `group_task_start` | Group | task_id, persona_id | A group task begins |
| `group_task_end` | Group | task_id, persona_id, error?, duration, scores? | A group task ends |
| `group_end` | Group | group, total, ok_count, err_count | Group run completes |

### 2.4 Reuse of CLI Infrastructure

The web backend fully reuses the CLI's core execution path:
- `orchestrator.runners.job_runner.run_single_job()` — single task execution
- `orchestrator.runners.job_runner.eval_single_job()` — single task evaluation
- `orchestrator.runners.parallel_runner.run_jobs_parallel()` — parallel execution
- `orchestrator.live_monitor.emit()` — event emission (CLI uses sync mode + embedded SSE server; web uses async mode + FastAPI SSE)

The web layer does not modify any CLI logic. It constructs `JobSpec` before execution and reads `JobResult` after completion.

---

## 3. Frontend Architecture

### 3.1 SPA Routing System

Uses hash-based routing (`#/route`) driven by the `hashchange` event. Core data structures:

```
_modules      = {}   // module name → container div (persistent, never destroyed)
_subPages     = {}   // route string → sub-page div (cached, hide/show)
_staleRoutes  = {}   // route string → true (marked for re-render on next visit)
_lastModRoute = {}   // module name → last visited route (resume position on module switch)
```

**Module Container Architecture:** Each top-level module (dashboard, run, results, evaluate, tasks) owns a persistent container div. Module switching = hide/show operations, so DOM state (conversations, eval progress, etc.) is fully preserved across switches.

**Sub-page Caching:** Within Results, Evaluate, Tasks, and Run modules, each sub-route generates an independent sub-page div cached in `_subPages`. Drilling in/out = hide/show of cached divs. Pages can be marked stale via `invalidateSubPage(route)` and will re-render on next visit.

**Cross-module Position Memory:** `_lastModRoute` records the last visited route per module. When the user switches back to a module via the nav bar, the router automatically redirects to the last sub-page (instead of the module root), preserving visibility of eval progress and other stateful views. Navigating to the root within the same module (via breadcrumbs) does not trigger this redirect.

**Scoped DOM Queries:** All in-page element lookups use `scope.querySelector('#id')` instead of `document.getElementById()`, where `scope` is the current sub-page container div. This prevents multiple cached sub-pages with identical IDs from returning wrong elements. SSE event handlers obtain the correct scope via `_getRunScope()` / `_getGroupScope()`.

### 3.2 Route Table

| Route | Module | Page |
|---|---|---|
| `#/` | dashboard | Overview dashboard |
| `#/run` | run | Run mode selection (Single / Group) |
| `#/run/single` | run | Single task run interface |
| `#/run/group` | run | Group run interface |
| `#/results` | results | Result source selection (Single / Group) |
| `#/results/s/{source}` | results | Agent listing |
| `#/results/s/{source}/a/{agent}` | results | Model listing |
| `#/results/s/{source}/a/{agent}/m/{model}` | results | Category listing |
| `#/results/s/{source}/a/{agent}/m/{model}/c/{category}` | results | Task result listing |
| `#/results/{source}/{agent}/{model}/{category}/{task_id}/{persona_id}` | results | Result detail |
| `#/evaluate` | evaluate | Evaluate source selection |
| `#/evaluate/s/{source}` | evaluate | Agent listing |
| `#/evaluate/s/{source}/a/{agent}` | evaluate | Model listing |
| `#/evaluate/s/{source}/a/{agent}/m/{model}` | evaluate | Category listing |
| `#/evaluate/s/{source}/a/{agent}/m/{model}/c/{category}` | evaluate | Category eval operations |
| `#/tasks` | tasks | Task category folders |
| `#/tasks/{category}` | tasks | Tasks within category |

### 3.3 Five Functional Modules

#### 3.3.1 Dashboard

Lightweight overview page that refreshes on every visit. Displays three summary cards:
- **Status** — current run state (Idle / Running)
- **Results** — total saved result count
- **Tasks** — total available task count

Below the cards, a list of the 5 most recent runs.

#### 3.3.2 Run

**Mode Selection (`#/run`):** Two cards for choosing Single Task or Group Run mode.

**Single Task Run (`#/run/single`):** Three-column layout:
- **Left — Config panel:** Category → Task cascading selects, Persona select, Agent select (Anthropic/OpenAI/Google/Generic), Docker sandbox toggle, max turns, run mode (Run Only / Run + Eval), Run/Stop buttons, status bar
- **Center — Conversation panel:** Real-time Student/Tutor chat display. Shows thinking animation while tutor is processing (bouncing dots + "Thinking..." / "Using tool_name..."). Supports Content Blocks mode (collapsible thinking blocks + inline tool calls + text bubbles)
- **Right — Tool panel:** Real-time tool call cards showing name, status (running → ok/fail), duration, argument preview. Click tool name to open modal with full arguments and results

Both Config and Tool panels are collapsible via vertical side tabs. The tool panel auto-expands on the first tool call.

**Group Run (`#/run/group`):** Configure category group, Agent, Persona (optional — default "all"), Docker, max turns, parallel worker count. After launch, displays:
- Progress bar (percentage)
- Timer
- Job list (each row: status icon ○→●→✓/✗, task_id, persona_id, duration/scores)

**SSE Real-time Updates:** Navigating to other modules during a run and returning preserves the full conversation state (thanks to module container architecture). The connection status indicator (connected/completed/disconnected) is only visible while a run or group run is active.

#### 3.3.3 Results

5-level folder drill-down navigation matching the filesystem hierarchy:
```
Source (run-single / run-group)
  → Agent (anthropic / openai / ...)
    → Model (claude-haiku-4-5-20251001 / ...)
      → Category (backtest / implementation / ...)
        → Task Items (each task_id + persona_id result)
```

Each level displays folder cards with result counts, scored/unscored breakdowns.

**Result Detail Page:** Reuses the Run page's three-column layout:
- **Info panel:** Agent, Model, Category, Persona, timestamp, duration, turn count, tool call count
- **Conversation panel:** Full conversation replay (supports Content Blocks inline tool display)
- **Tool panel:** Full tool call replay

Additional features:
- **Evaluate button:** Triggers evaluation for this result directly
- **Cost Report button:** Opens modal with score report and cost report (Markdown rendered)
- **Image rewriting:** Image paths in conversations are rewritten to the `/api/results/.../files/` endpoint

Breadcrumb navigation with back buttons at every level.

#### 3.3.4 Evaluate

Same 5-level folder structure as Results, but the leaf level is an evaluation operations page:
- Lists all results under the category, each showing task_id (clickable link to result detail), persona_id, score status
- **Evaluate button:** Triggers evaluation for a single result
- **Evaluate All Unscored button:** Batch evaluates all unscored results

**Eval Progress Card:** When evaluation is triggered, an inline progress card expands:
- 7 evaluation steps with real-time status (○ pending → ● running → ✓ done + score)
- Steps: Programmatic QR → Code Evaluation → Tool Usage → Result Judge → Process Metrics → Tutor 7D → QR Blending
- Live timer
- **Stop button:** Red stop button that sends `POST /api/eval/stop`; backend sets cancellation flag to interrupt the evaluation thread

**Eval Completion Modal:** Displays OAS, QR, QP scores with a "View Result" navigation button.

**State Caching:** Eval progress card DOM state is preserved across module switches (thanks to module container architecture and position memory). On eval completion, related sub-pages are auto-invalidated so data refreshes on next visit.

#### 3.3.5 Tasks

Two-level structure:
- Level 1: Folder cards grouped by category, showing task counts
- Level 2: Task detail list within a category, displaying task_id, difficulty, description, persona_ids, max_turns, etc.

### 3.4 Frontend Module Breakdown

#### `render.js` (99 lines) — Render Pipeline
- **renderMarkdown(text):** Unified Markdown + LaTeX rendering
- Pipeline: protect code blocks → KaTeX render (`$$...$$` and `$...$`) → restore code blocks → marked.js parse
- **escapeHtml(str):** Safe HTML escaping

#### `chat.js` (440 lines) — Chat UI
- **addChatMessage(container, role, content, contentBlocks, toolLogs):** Adds a chat message
- Two rendering modes: simple text bubble / Content Blocks mode (collapsible thinking blocks + inline tool cards + text bubbles)
- SVG avatars (Tutor: amber triangle / Student: brown person silhouette)
- **showThinking / updateThinking / hideThinking:** Thinking state animation
- **showResponding / hideResponding:** Student typing indicator
- **buildConversationReplay(container, conversation, toolLogs):** Replays saved conversation data

#### `tools.js` (236 lines) — Tool Panel
- **addToolStart(container, data):** Adds a "running" status tool card
- **updateToolResult(container, data):** Updates to completed state (ok/fail + duration + result preview)
- **buildToolReplay(container, toolLogs):** Replays saved tool call data
- Click tool name → modal with full arguments/results

#### `app.js` (1910 lines) — Core Logic
- SPA routing and module management
- SSE connection management (auto-reconnect, max 3 retries)
- 11 SSE event handler functions
- All page rendering logic for 5 modules
- Global modal system (showModal/closeModal)
- Breadcrumb generator (buildBreadcrumb)
- Panel collapse/expand (bindSideTab)

### 3.5 UI/UX Design

**Theme:** Warm Claude-inspired palette. Light mode uses beige/amber tones; dark mode auto-switches to deep brown/amber via CSS `prefers-color-scheme` media query.

**Fonts:**
- Body: Inter (sans-serif)
- Headings: Playfair Display / DM Serif Display (serif — academic feel)
- Code: JetBrains Mono (monospace)

**Interaction patterns:**
- Folder-style hierarchical browsing (card click to drill in, breadcrumbs to go back)
- Three-column layout (collapsible side panels)
- Global modal popups (ESC / click backdrop to close)
- Real-time progress animations (thinking dots, tool status, eval steps)

---

## 4. Data Flow

### 4.1 Run Flow

```
[Frontend] User clicks Run
  → POST /api/run {task_id, persona_id, agent, ...}
  → [Backend] Construct JobSpec → background thread run_single_job()
  → [Orchestrator] emit("session_start") → emit("student_message") →
      emit("tool_start") → emit("tool_result") → emit("tutor_response") →
      ... → emit("session_end")
  → [live_monitor] loop.call_soon_threadsafe(queue.put_nowait, payload)
  → [events.py] asyncio.Queue → SSE EventSourceResponse → browser EventSource
  → [Frontend] handleSSEEvent → onSessionStart/onStudentMessage/... → DOM update
```

### 4.2 Evaluate Flow

```
[Frontend] User clicks Evaluate
  → POST /api/eval {source, agent, model, category, task_id, persona_id}
  → [Backend] background thread eval_single_job()
  → [Orchestrator] emit("session_start", mode="eval") →
      emit("eval_step", step="quant_result") → ... →
      emit("session_end", mode="eval", scores={...})
  → [Frontend] onEvalStart → onEvalStep (update progress card) →
      onEvalEnd (completion modal)
```

### 4.3 Result Browsing Flow

```
[Frontend] User clicks through folder hierarchy
  → GET /api/results (fetches all results; frontend filters/groups per level)
  → Final: GET /api/results/{6-segment path} for full run_state.json
  → buildConversationReplay() + buildToolReplay() render chat and tools
```

---

## 5. Key Design Decisions

### 5.1 Why SSE Instead of WebSocket?
SSE is unidirectional push, sufficient for "backend pushes progress to frontend". Frontend-to-backend requests use REST API. SSE is simpler, has better HTTP compatibility, and browsers natively handle reconnection.

### 5.2 Why No Frontend Framework?
The project is moderate in size (~2700 lines JS) and primarily read/display UI. Vanilla JS avoids build toolchain complexity, supports direct editing and hot-reload, and CDN dependencies are minimal (only marked.js and KaTeX).

### 5.3 Why Module Container Architecture?
Eval progress cards contain live-updating DOM elements and timers. If the DOM were destroyed on module switch, these states would be lost. Module Containers preserve DOM via hide/show, combined with sub-page caching for multi-level page state retention.

### 5.4 Scoped querySelector Principle
Since sub-page caching creates multiple divs cloned from the same template (with identical IDs) in the same DOM, `document.getElementById()` returns the first match (which may be hidden). All in-page element lookups must use `scope.querySelector('#id')` where scope is the current visible sub-page container div.

---

## 6. File Structure

```
bench/web/
├── server.py              # FastAPI app entry + Uvicorn launch
├── __init__.py
├── api/
│   ├── __init__.py
│   ├── events.py          # SSE event stream endpoint
│   ├── tasks.py           # Task / persona listing API
│   ├── results.py         # Result scanning / detail / file serving API
│   └── runs.py            # Run / group run / eval / stop API
└── static/
    ├── index.html          # SPA entry + 6 <template> tags
    ├── css/
    │   └── styles.css      # All styles
    └── js/
        ├── render.js       # Markdown + LaTeX rendering
        ├── chat.js         # Chat UI components
        ├── tools.js        # Tool call panel components
        └── app.js          # SPA router + page logic + SSE
```

---

## 7. Unimplemented Features & Known Limitations

The following features are planned or partially implemented but not yet complete:

### 7.1 Streaming Token Output

**Status:** Not implemented.

Currently, tutor responses arrive as complete messages via a single `tutor_response` SSE event. The UI renders the full response at once.

**Planned behavior:** Stream individual tokens as they are generated by the LLM, rendering them progressively in the chat bubble (typewriter effect). This requires:
- Backend: A new SSE event type (e.g., `tutor_token`) emitted per token/chunk from the agent adapter's streaming response
- Frontend: A streaming text accumulator that appends tokens to a growing bubble element, with Markdown re-rendering on sentence boundaries or after a debounce period
- Adapter changes: Each agent adapter (anthropic, openai, google, generic) must expose a streaming mode that yields partial responses instead of waiting for the complete message

### 7.2 Run Group — Unverified & Detail Panel TBD

**Status:** Backend endpoint and basic progress UI implemented but not fully tested end-to-end.

**Known gaps:**
- The group run has not been verified with actual multi-task parallel execution in the web context (only the CLI `run_jobs_parallel` path has been tested)
- SSE event routing during concurrent group tasks may produce interleaved `student_message` / `tutor_response` events from different tasks — the current frontend does not disambiguate these by task_id
- **Detail panel design TBD:** Currently, clicking a completed group job row does nothing. The intended behavior is to either (a) expand an inline detail view with conversation summary and key metrics, or (b) navigate to the full result detail page. The detail panel design and interaction pattern have not been finalized.
- No "Stop Group Run" button exists yet (individual task cancellation within a group is not supported)
- Worker count vs. API rate limit interaction is untested

### 7.3 Evaluate Page — Design Not Finalized

**Status:** Core eval trigger and progress tracking work, but the overall evaluate page UX is provisional.

**Open design questions:**
- **Batch evaluation strategy:** The current "Evaluate All Unscored" button triggers sequential eval calls. A more sophisticated approach might use a server-side batch queue with concurrency control and a unified progress view.
- **Cross-model comparison view:** No UI exists yet for comparing evaluation scores across different models/agents for the same task. This would be valuable for benchmarking.
- **Score visualization:** Scores are displayed as raw numbers. Charts (radar charts for multi-dimensional scores, bar charts for cross-task comparison) are planned but not implemented.
- **Re-evaluation workflow:** When re-evaluating a previously scored result, the old scores are silently overwritten. There is no version history or diff view for scores.
- **Filtering and sorting:** The eval page lists all results in a category but provides no filtering (by score range, by scored/unscored status) or sorting (by score, by timestamp) controls.
