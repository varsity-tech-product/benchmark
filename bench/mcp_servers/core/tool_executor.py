#!/usr/bin/env python3
"""Container-side tool executor daemon for QuantTutorBench.

Runs inside the Docker container as a long-lived process.  Reads JSON-line
requests from stdin, dispatches to CORE_TOOLS functions, and writes
JSON-line responses to stdout.

Protocol
--------
Request:  {"id": <int>, "tool": <str>, "args": <dict>}
Response: {"id": <int>, "result": <str>, "error": <str|null>}

Special commands:
  {"id": ..., "tool": "__ping__"}      -> {"id": ..., "result": "pong", "error": null}
  {"id": ..., "tool": "__shutdown__"}  -> daemon exits gracefully
"""

import json
import signal
import sys
import traceback

from tools import CORE_TOOLS

# Default per-call timeout (seconds).  The host may also enforce its own
# timeout; this is a safety net for runaway exec()/subprocess calls.
_DEFAULT_TIMEOUT = 120


class _ToolTimeout(Exception):
    pass


def _on_alarm(signum, frame):
    raise _ToolTimeout("Tool execution timed out")


def main():
    # Line-buffered stdout for reliable JSON-lines communication.
    sys.stdout.reconfigure(line_buffering=True)

    # Signal readiness to the host.
    sys.stdout.write(json.dumps({"status": "ready"}) + "\n")
    sys.stdout.flush()

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        # --- Parse request ---
        try:
            request = json.loads(line)
        except json.JSONDecodeError as exc:
            _respond(-1, f"Invalid JSON: {exc}", str(exc))
            continue

        req_id = request.get("id", 0)
        tool_name = request.get("tool", "")
        tool_args = request.get("args", {})

        # --- Special commands ---
        if tool_name == "__ping__":
            _respond(req_id, "pong", None)
            continue

        if tool_name == "__shutdown__":
            _respond(req_id, "shutdown", None)
            break

        # --- Dispatch to tool ---
        if tool_name not in CORE_TOOLS:
            _respond(
                req_id,
                f"Error: Unknown tool '{tool_name}'",
                f"Unknown tool: {tool_name}",
            )
            continue

        func = CORE_TOOLS[tool_name]["func"]

        # Per-call timeout via SIGALRM (Linux only — fine, we are in a container).
        signal.signal(signal.SIGALRM, _on_alarm)
        signal.alarm(_DEFAULT_TIMEOUT)
        try:
            result = func(**tool_args)
            signal.alarm(0)
            _respond(req_id, str(result), None)
        except _ToolTimeout:
            _respond(
                req_id,
                f"Error: Tool '{tool_name}' timed out after {_DEFAULT_TIMEOUT}s",
                "timeout",
            )
        except KeyboardInterrupt:
            signal.alarm(0)
            raise  # Let Ctrl+C propagate
        except BaseException:
            signal.alarm(0)
            tb = traceback.format_exc()
            _respond(req_id, f"Error: {tb}", tb)


def _respond(req_id: int, result: str, error):
    """Write a single JSON-line response to stdout."""
    sys.stdout.write(
        json.dumps({"id": req_id, "result": result, "error": error}) + "\n"
    )
    sys.stdout.flush()


if __name__ == "__main__":
    main()
