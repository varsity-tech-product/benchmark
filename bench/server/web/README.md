# Isolated Web UI

This directory contains the new session-centric frontend for the client-server
architecture.

Key rules:
- Keep this tree independent from legacy `bench/web`.
- Serve all frontend routes and assets from `server.api.http_app:create_app()`.
- Read archived results only through `ui_indexer.py`.
- Serve archived workspace raw files only through `/ui/results/{session_id}/files/{path:path}`.
- Serve workspace browsing and inline preview through the dedicated read-only workspace endpoints.
- Keep protocol traffic (`send_message`) separate from domain tool replay in the UI read model.

Main pieces:
- `ui_app.py`: Starlette `/ui/*` route factory
- `ui_indexer.py`: merged read model for `results/server` + `results/client`
- `templates/index.html`: isolated SPA shell
- `static/js/`: isolated replay/render frontend
- `static/css/styles.css`: isolated visual layer for the new UI

Current routes:
- `GET /`
- `#/run` SPA route for the REST session run harness
- `#/results` SPA route for archived result browsing
- `#/tasks` SPA route for task browsing
- `GET /ui/tasks`
- `GET /ui/results`
- `GET /ui/results/{session_id}`
- `GET /ui/results/{session_id}/workspace`
- `GET /ui/results/{session_id}/workspace/preview/{path:path}`
- `GET /ui/results/{session_id}/files/{path:path}`

Run route scope:
- `#/run` first asks for `Agent Test` or `Human Test`.
- `Human Test` is a browser-side REST harness: register/start a server session, display the client-visible background and student opening, send tutor messages through `/session/{sid}/send`, refresh status/tools, cancel active sessions, and jump to Results after completion.
- `Agent Test` mirrors the real automated flow in `bench/client/runner.py` (`register_session` → `start_session` → `list_tools` → `adapter.generate_response` → `save_client_trace`) but its start button is intentionally disabled until a backend job launcher exists.
- The Run surface only displays public task labels such as `D01`/`X09`; hidden task metadata belongs in the server/client execution context, not the tester-facing Run panels.
- The frontend does not start local agent subprocesses or manage automated client jobs yet.

Result detail read model:
- `tool_logs`: domain tool calls only; excludes `send_message`
- `all_tool_logs`: raw server `run_state.tool_logs`
- `send_message_events`: protocol communication events rendered in Conversation
- `send_message_count`: count of protocol communication events
- `workspace_files`: archived file paths from server run_state

Client trace lookup:
- Server archives may expose `session_id` as `{uuid}_{task_prefix}` for fast archived lookup.
- Client traces are commonly stored under the original `{uuid}`.
- `ui_indexer.py` must support both ids and gracefully fall back to server-only replay when no client trace exists.
