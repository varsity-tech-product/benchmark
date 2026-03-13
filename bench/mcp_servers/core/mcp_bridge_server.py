#!/usr/bin/env python3
"""Minimal MCP stdio server that bridges tool calls to a parent process.

Launched as a subprocess by Claude Code via --mcp-config. Communicates
with the parent ClaudeCodeAdapter over a Unix domain socket to execute
benchmark tool calls through the MCPProxy.

Supports both Content-Length framed transport (MCP 2024-11-05) and
newline-delimited JSON transport (Claude Code 2.1+).

Environment variables:
    QTB_BRIDGE_SOCKET  - Path to Unix domain socket for parent communication
    QTB_TOOL_SCHEMAS   - Path to JSON file with tool schema definitions
"""

import json
import os
import socket
import sys


# ── Transport auto-detection ──────────────────────────────────

# Claude Code 2.1+ sends raw JSON lines (no Content-Length headers).
# Older MCP clients use Content-Length framing (LSP-style).
# We auto-detect based on the first byte of input.

_use_ndjson = None  # True=newline-delimited, False=Content-Length, None=unknown


def _read_message():
    """Read a JSON-RPC message from stdin, auto-detecting transport."""
    global _use_ndjson

    # Peek at first byte to detect transport on first call
    if _use_ndjson is None:
        first_byte = sys.stdin.buffer.peek(1)[:1]
        if not first_byte:
            return None
        _use_ndjson = first_byte == b"{"

    if _use_ndjson:
        return _read_ndjson()
    else:
        return _read_content_length()


def _read_ndjson():
    """Read a JSON-RPC message as a newline-delimited JSON line."""
    line = sys.stdin.buffer.readline()
    if not line:
        return None
    stripped = line.decode("utf-8", errors="replace").strip()
    if not stripped:
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _read_content_length():
    """Read a JSON-RPC message using Content-Length framing."""
    headers = {}
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None  # EOF
        decoded = line.decode("utf-8", errors="replace").strip()
        if decoded == "":
            break  # End of headers
        if ":" in decoded:
            key, value = decoded.split(":", 1)
            headers[key.strip()] = value.strip()

    content_length = int(headers.get("Content-Length", 0))
    if content_length == 0:
        return None

    data = b""
    while len(data) < content_length:
        chunk = sys.stdin.buffer.read(content_length - len(data))
        if not chunk:
            return None
        data += chunk

    return json.loads(data.decode("utf-8"))


def _write_message(msg):
    """Write a JSON-RPC message to stdout.

    Uses the same transport as detected for reading: newline-delimited
    JSON or Content-Length framing.
    """
    data = json.dumps(msg).encode("utf-8")
    if _use_ndjson:
        sys.stdout.buffer.write(data + b"\n")
    else:
        header = f"Content-Length: {len(data)}\r\n\r\n"
        sys.stdout.buffer.write(header.encode("utf-8"))
        sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


# ── Tool call bridge to parent ─────────────────────────────────


def _call_parent(sock_path: str, tool_name: str, tool_args: dict) -> str:
    """Send a tool call request to the parent adapter via Unix socket."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(300)  # 5 min timeout for long-running tools
    try:
        sock.connect(sock_path)
        request = json.dumps({"name": tool_name, "args": tool_args}) + "\n"
        sock.sendall(request.encode("utf-8"))
        # Signal that we're done sending
        sock.shutdown(socket.SHUT_WR)

        # Read full response
        chunks = []
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            chunks.append(chunk)

        data = b"".join(chunks).decode("utf-8")
        response = json.loads(data)
        return response.get("result", "")
    finally:
        sock.close()


# ── MCP schema conversion ─────────────────────────────────────


def _convert_schemas(tool_schemas: list[dict]) -> list[dict]:
    """Convert benchmark tool schemas to MCP tool format."""
    mcp_tools = []
    for schema in tool_schemas:
        properties = {}
        required = []
        for pname, pinfo in schema.get("parameters", {}).items():
            if isinstance(pinfo, dict):
                properties[pname] = {
                    "type": pinfo.get("type", "string"),
                    "description": pinfo.get("description", pname),
                }
                if pinfo.get("required", False):
                    required.append(pname)

        mcp_tools.append({
            "name": schema["name"],
            "description": schema.get("description", ""),
            "inputSchema": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        })
    return mcp_tools


# ── Main server loop ──────────────────────────────────────────


def main():
    sock_path = os.environ.get("QTB_BRIDGE_SOCKET", "")
    schemas_path = os.environ.get("QTB_TOOL_SCHEMAS", "")

    if not sock_path or not schemas_path:
        sys.stderr.write(
            "mcp_bridge_server: QTB_BRIDGE_SOCKET and QTB_TOOL_SCHEMAS required\n"
        )
        sys.exit(1)

    with open(schemas_path) as f:
        tool_schemas = json.load(f)

    mcp_tools = _convert_schemas(tool_schemas)

    while True:
        msg = _read_message()
        if msg is None:
            break

        method = msg.get("method", "")
        msg_id = msg.get("id")

        if method == "initialize":
            _write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "qtb-bridge", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            })

        elif method.startswith("notifications/"):
            # Notifications don't need a response
            pass

        elif method == "tools/list":
            _write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {"tools": mcp_tools},
            })

        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})

            try:
                result_text = _call_parent(sock_path, tool_name, tool_args)
                _write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": str(result_text)}],
                        "isError": False,
                    },
                })
            except Exception as e:
                _write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "result": {
                        "content": [{"type": "text", "text": f"Bridge error: {e}"}],
                        "isError": True,
                    },
                })

        elif method == "ping":
            _write_message({
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {},
            })

        else:
            # Unknown method
            if msg_id is not None:
                _write_message({
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {
                        "code": -32601,
                        "message": f"Method not found: {method}",
                    },
                })


if __name__ == "__main__":
    main()
