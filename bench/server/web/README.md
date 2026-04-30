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
- `#/flow-demo` SPA route for run flow monitoring
- `#/run` SPA route for agent connection and run creation
- `#/review` SPA route for archived session review
- `#/tasks` SPA route for task browsing
- `GET /ui/tasks`
- `GET /ui/results`
- `GET /ui/results/{session_id}`
- `GET /ui/results/{session_id}/workspace`
- `GET /ui/results/{session_id}/workspace/preview/{path:path}`
- `GET /ui/results/{session_id}/files/{path:path}`

Run route scope:
- `#/run` opens the agent connection surface directly.
- The agent connection surface creates a task-specific run through `/ui/runs`, exposes run token connection details, opens the REST API key modal, and links to the QuantTutorBench REST agent skill.
- `Agent Test` mirrors the real automated flow in `bench/client/runner.py` (`register_session` → `start_session` → `list_tools` → `adapter.generate_response` → `save_client_trace`) when the dedicated `run-agent.js` module is loaded.
- The Run surface only displays public task labels such as `D01`/`X09`; hidden task metadata stays in the server/client execution context.
- Session Flow owns run lifecycle browsing. Human Review owns archived session inspection.

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
