"""Real-time conversation monitor for QuantTutorBench.

Launches a lightweight HTTP + SSE server in a background thread.
Other modules call ``emit()`` to push events; the browser dashboard
receives them via Server-Sent Events and renders a live chat UI.

Usage:
    from orchestrator.live_monitor import LiveMonitor

    monitor = LiveMonitor(port=8765)
    monitor.start()          # non-blocking — runs in daemon thread
    # ... run benchmark ...
    monitor.stop()

Or via the global helper (used by simulation.py / mcp_proxy.py):
    from orchestrator.live_monitor import emit
    emit("student_message", {"content": "..."})
"""

import json
import logging
import os
import queue
import tempfile
import textwrap
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Optional

logger = logging.getLogger(__name__)

# ── Global state ──────────────────────────────────────────────────────
_event_queue: queue.Queue = queue.Queue()
_monitor_active: bool = False

# Async mode (web dashboard) — set by init_async()
_broadcast_fn = None  # events.broadcast callable
_event_loop = None  # asyncio event loop
_mode: str = "off"  # "off" | "sync" | "async"

# Thread-local job key — set by group runs so parallel tasks' events are distinguishable.
# Single runs never set this, so their events have no job_key (backward-compatible).
_thread_local = threading.local()

# Active workspace path — set by orchestrator so web API can serve live files.
# Thread-safe: only one single-run at a time; group runs use per-job workspaces
# but only the "currently viewed" job's workspace is needed.
_active_workspace: Optional[str] = None
_workspace_lock = threading.Lock()

# Event log persistence (survives server restart)
_event_log_lock = threading.Lock()
_EVENT_LOG_PATH = os.path.join(tempfile.gettempdir(), "qtb_event_log.jsonl")


def init_async(loop, broadcast_fn):
    """Switch emit() to async mode for the web dashboard."""
    global _broadcast_fn, _event_loop, _mode, _monitor_active
    _broadcast_fn = broadcast_fn
    _event_loop = loop
    _mode = "async"
    _monitor_active = True


def init_sync():
    """Switch emit() to sync mode for CLI --live."""
    global _mode, _monitor_active
    _mode = "sync"
    _monitor_active = True


def set_job_key(key: Optional[str]):
    """Set the job key for the current thread (used by group runs)."""
    _thread_local.job_key = key


def get_job_key() -> Optional[str]:
    """Get the job key for the current thread, or None."""
    return getattr(_thread_local, "job_key", None)


def set_active_workspace(path: Optional[str]):
    """Set the active workspace path for live file serving."""
    global _active_workspace
    with _workspace_lock:
        _active_workspace = path


def get_active_workspace() -> Optional[str]:
    """Get the active workspace path, or None if no run is active."""
    with _workspace_lock:
        return _active_workspace


def emit(event_type: str, data: dict):
    """Push an event to connected clients.

    Routes to broadcast (web) or threading.Queue (CLI) based on mode.
    Also persists events to disk for crash recovery (async mode only).
    Injects thread-local ``job_key`` when set (group runs).
    No-op when mode is "off".
    """
    if _mode == "off":
        return
    payload = {"type": event_type, "ts": time.time(), **data}
    job_key = getattr(_thread_local, "job_key", None)
    if job_key is not None:
        payload["job_key"] = job_key
    if _mode == "async" and _broadcast_fn is not None and _event_loop is not None:
        _persist_event(event_type, payload)
        _event_loop.call_soon_threadsafe(_broadcast_fn, payload)
    elif _mode == "sync":
        _event_queue.put(payload)


def _persist_event(event_type: str, payload: dict):
    """Append event to JSONL log file. Truncate on new run session."""
    try:
        with _event_log_lock:
            # New run session (not eval) → start fresh log
            if event_type == "session_start" and payload.get("mode") != "eval":
                mode = "w"
            else:
                mode = "a"
            with open(_EVENT_LOG_PATH, mode) as f:
                f.write(json.dumps(payload, default=str) + "\n")
    except Exception:
        pass


def load_event_log() -> list[dict]:
    """Load persisted events from disk (for crash recovery on startup).

    Tolerates corrupt lines (e.g. from mid-write server kill) by skipping
    them instead of discarding the entire log.
    """
    events = []
    try:
        with open(_EVENT_LOG_PATH) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    logger.warning("Skipped corrupt line in event log: %s", line[:80])
    except FileNotFoundError:
        pass
    return events


# ── Embedded HTML dashboard ───────────────────────────────────────────
_DASHBOARD_HTML = textwrap.dedent(
    """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>QuantTutorBench Live Monitor</title>
<style>
  :root {
    --bg: #0f1117; --surface: #1a1d27; --border: #2a2d3a;
    --text: #e4e4e7; --muted: #71717a; --accent: #6366f1;
    --student: #3b82f6; --tutor: #10b981; --tool: #f59e0b;
    --error: #ef4444; --success: #22c55e;
  }
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: 'SF Mono', 'Fira Code', monospace; background: var(--bg);
         color: var(--text); height: 100vh; display: flex; flex-direction: column; }

  header { padding: 12px 20px; background: var(--surface); border-bottom: 1px solid var(--border);
           display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 14px; font-weight: 600; }
  #status { font-size: 11px; padding: 3px 8px; border-radius: 10px;
            background: var(--error); color: white; }
  #status.connected { background: var(--success); }
  #turn-counter { margin-left: auto; font-size: 12px; color: var(--muted); }

  .container { flex: 1; display: flex; overflow: hidden; }

  /* Chat panel (left) */
  #chat-panel { flex: 2; display: flex; flex-direction: column; border-right: 1px solid var(--border); }
  #chat-panel h2 { padding: 10px 16px; font-size: 12px; color: var(--muted);
                   border-bottom: 1px solid var(--border); }
  #chat { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }

  .msg { max-width: 85%; padding: 10px 14px; border-radius: 12px; font-size: 13px;
         line-height: 1.5; white-space: pre-wrap; word-break: break-word; }
  .msg.student { align-self: flex-end; background: var(--student); color: white;
                 border-bottom-right-radius: 4px; }
  .msg.tutor { align-self: flex-start; background: var(--surface); border: 1px solid var(--border);
               border-bottom-left-radius: 4px; }
  .msg .label { font-size: 10px; font-weight: 700; text-transform: uppercase;
                margin-bottom: 4px; opacity: 0.7; }

  /* Tool panel (right) */
  #tool-panel { flex: 1; display: flex; flex-direction: column; min-width: 320px; }
  #tool-panel h2 { padding: 10px 16px; font-size: 12px; color: var(--muted);
                   border-bottom: 1px solid var(--border); }
  #tools { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 8px; }

  .tool-call { background: var(--surface); border: 1px solid var(--border);
               border-radius: 8px; padding: 10px 12px; font-size: 12px; }
  .tool-call .tool-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
  .tool-call .tool-name { font-weight: 700; color: var(--tool); }
  .tool-call .tool-status { font-size: 10px; padding: 1px 6px; border-radius: 8px; }
  .tool-call .tool-status.running { background: var(--tool); color: black; }
  .tool-call .tool-status.success { background: var(--success); color: black; }
  .tool-call .tool-status.failed { background: var(--error); color: white; }
  .tool-call .tool-duration { font-size: 10px; color: var(--muted); margin-left: auto; }
  .tool-call .tool-args, .tool-call .tool-result {
    font-size: 11px; color: var(--muted); margin-top: 4px;
    max-height: 120px; overflow-y: auto; white-space: pre-wrap; word-break: break-all;
  }
  .tool-call .tool-result { color: var(--text); border-top: 1px solid var(--border);
                            padding-top: 6px; margin-top: 6px; }

  /* Status bar */
  #statusbar { padding: 6px 16px; background: var(--surface); border-top: 1px solid var(--border);
               font-size: 11px; color: var(--muted); display: flex; gap: 20px; }

  /* Scrollbar */
  ::-webkit-scrollbar { width: 6px; }
  ::-webkit-scrollbar-track { background: transparent; }
  ::-webkit-scrollbar-thumb { background: var(--border); border-radius: 3px; }
</style>
</head>
<body>
  <header>
    <h1>QuantTutorBench Live Monitor</h1>
    <span id="status">disconnected</span>
    <span id="turn-counter">Turn 0</span>
  </header>
  <div class="container">
    <div id="chat-panel">
      <h2>Conversation</h2>
      <div id="chat"></div>
    </div>
    <div id="tool-panel">
      <h2>Tool Calls</h2>
      <div id="tools"></div>
    </div>
  </div>
  <div id="statusbar">
    <span id="msg-count">Messages: 0</span>
    <span id="tool-count">Tool calls: 0</span>
    <span id="elapsed">Elapsed: 0s</span>
  </div>

<script>
const chat = document.getElementById('chat');
const tools = document.getElementById('tools');
const statusEl = document.getElementById('status');
const turnEl = document.getElementById('turn-counter');
const msgCountEl = document.getElementById('msg-count');
const toolCountEl = document.getElementById('tool-count');
const elapsedEl = document.getElementById('elapsed');

let msgCount = 0, toolCount = 0, startTime = null;
// Track in-flight tool calls by ID
const pendingTools = {};

function scrollBottom(el) { el.scrollTop = el.scrollHeight; }

function truncate(s, n) {
  if (typeof s !== 'string') s = JSON.stringify(s, null, 2);
  return s.length > n ? s.slice(0, n) + '...' : s;
}

function formatArgs(args) {
  if (!args || Object.keys(args).length === 0) return '';
  return Object.entries(args).map(([k, v]) => {
    const val = typeof v === 'string' ? truncate(v, 200) : JSON.stringify(v);
    return k + ': ' + val;
  }).join('\\n');
}

function addMessage(role, content) {
  msgCount++;
  const div = document.createElement('div');
  div.className = 'msg ' + role;
  const label = document.createElement('div');
  label.className = 'label';
  label.textContent = role === 'student' ? 'Student' : 'Tutor';
  div.appendChild(label);
  div.appendChild(document.createTextNode(truncate(content, 3000)));
  chat.appendChild(div);
  scrollBottom(chat);
  msgCountEl.textContent = 'Messages: ' + msgCount;
}

function addToolStart(data) {
  toolCount++;
  const id = data.call_id || ('t' + toolCount);
  const div = document.createElement('div');
  div.className = 'tool-call';
  div.id = 'tool-' + id;
  div.innerHTML =
    '<div class="tool-header">' +
      '<span class="tool-name">' + data.name + '</span>' +
      '<span class="tool-status running">running</span>' +
      '<span class="tool-duration"></span>' +
    '</div>' +
    '<div class="tool-args">' + formatArgs(data.args) + '</div>';
  tools.appendChild(div);
  pendingTools[id] = { el: div, start: data.ts };
  scrollBottom(tools);
  toolCountEl.textContent = 'Tool calls: ' + toolCount;
}

function addToolResult(data) {
  const id = data.call_id || '';
  const pending = pendingTools[id];
  if (pending) {
    const el = pending.el;
    const statusSpan = el.querySelector('.tool-status');
    const durSpan = el.querySelector('.tool-duration');
    const ok = data.success !== false;
    statusSpan.className = 'tool-status ' + (ok ? 'success' : 'failed');
    statusSpan.textContent = ok ? 'ok' : 'fail';
    if (data.duration_ms != null) {
      durSpan.textContent = Math.round(data.duration_ms) + 'ms';
    }
    const resultDiv = document.createElement('div');
    resultDiv.className = 'tool-result';
    resultDiv.textContent = truncate(data.result || '', 1000);
    el.appendChild(resultDiv);
    delete pendingTools[id];
    scrollBottom(tools);
  }
}

function updateElapsed() {
  if (!startTime) return;
  const s = Math.round((Date.now() - startTime) / 1000);
  const m = Math.floor(s / 60);
  elapsedEl.textContent = 'Elapsed: ' + (m > 0 ? m + 'm ' : '') + (s % 60) + 's';
}

// SSE connection
function connect() {
  const es = new EventSource('/events');
  es.onopen = () => {
    statusEl.textContent = 'connected';
    statusEl.className = 'connected';
  };
  es.onmessage = (e) => {
    const data = JSON.parse(e.data);
    if (!startTime) startTime = Date.now();

    switch (data.type) {
      case 'student_message':
        addMessage('student', data.content);
        if (data.turn_index != null) turnEl.textContent = 'Turn ' + (data.turn_index + 1);
        break;
      case 'tutor_response':
        addMessage('tutor', data.content);
        break;
      case 'tool_start':
        addToolStart(data);
        break;
      case 'tool_result':
        addToolResult(data);
        break;
      case 'session_start':
        chat.innerHTML = '';
        tools.innerHTML = '';
        msgCount = 0; toolCount = 0; startTime = Date.now();
        turnEl.textContent = 'Turn 0';
        msgCountEl.textContent = 'Messages: 0';
        toolCountEl.textContent = 'Tool calls: 0';
        break;
      case 'session_end':
        statusEl.textContent = 'completed';
        break;
    }
  };
  es.onerror = () => {
    statusEl.textContent = 'disconnected';
    statusEl.className = '';
    setTimeout(connect, 2000);
  };
}

connect();
setInterval(updateElapsed, 1000);
</script>
</body>
</html>
"""
)


# ── HTTP + SSE handler ────────────────────────────────────────────────


class _SSEHandler(BaseHTTPRequestHandler):
    """Serves the dashboard HTML and SSE event stream."""

    def do_GET(self):
        if self.path == "/events":
            self._handle_sse()
        else:
            self._handle_dashboard()

    def _handle_dashboard(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(_DASHBOARD_HTML.encode("utf-8"))

    def _handle_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        while _monitor_active:
            try:
                event = _event_queue.get(timeout=1.0)
                payload = f"data: {json.dumps(event, default=str)}\n\n"
                self.wfile.write(payload.encode("utf-8"))
                self.wfile.flush()
            except queue.Empty:
                # Send heartbeat to keep connection alive
                try:
                    self.wfile.write(b": heartbeat\n\n")
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
            except (BrokenPipeError, ConnectionResetError):
                break

    def log_message(self, format, *args):
        """Suppress default HTTP access logs."""
        pass


# ── LiveMonitor class ─────────────────────────────────────────────────


class LiveMonitor:
    """Background HTTP+SSE server for real-time benchmark monitoring.

    Args:
        port: TCP port to listen on (default 8765).
        open_browser: Whether to auto-open the dashboard in a browser.
    """

    def __init__(self, port: int = 8765, open_browser: bool = True):
        self.port = port
        self.open_browser = open_browser
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self):
        """Start the SSE server in a daemon thread."""
        init_sync()

        # Drain any stale events from a previous session
        while not _event_queue.empty():
            try:
                _event_queue.get_nowait()
            except queue.Empty:
                break

        self._server = HTTPServer(("0.0.0.0", self.port), _SSEHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="live-monitor",
        )
        self._thread.start()

        url = f"http://localhost:{self.port}"
        logger.info("Live monitor started at %s", url)
        print(f"\n  Live Monitor: {url}\n")

        if self.open_browser:
            webbrowser.open(url)

    def stop(self):
        """Shut down the SSE server."""
        global _monitor_active
        _monitor_active = False
        if self._server:
            self._server.shutdown()
            self._server = None
        if self._thread:
            self._thread.join(timeout=3)
            self._thread = None
        logger.info("Live monitor stopped.")
