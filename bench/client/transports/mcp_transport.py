"""MCP transport — StreamableHTTP + tool calls.

Extracted from the original runner.py. Uses mcp.client.session.ClientSession
for all server interactions.
"""

import asyncio
import json
import logging
from contextlib import AsyncExitStack
from typing import Optional

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from ..tool_bridge import ToolBridge, convert_mcp_tools
from .base import SessionTransport

logger = logging.getLogger(__name__)

# Tools managed by the runner, not exposed to the agent.
_PROTOCOL_TOOLS = frozenset(
    {
        "register_session",
        "start_session",
        "request_evaluation",
        "get_results",
        "get_scores",
    }
)


class MCPTransport(SessionTransport):
    """MCP StreamableHTTP transport."""

    def __init__(self):
        self._exit_stack: Optional[AsyncExitStack] = None
        self._mcp: Optional[ClientSession] = None
        self._session_id: Optional[str] = None
        self._bridge: Optional[ToolBridge] = None

    async def connect(self, url: str, headers: Optional[dict] = None) -> None:
        self._exit_stack = AsyncExitStack()
        await self._exit_stack.__aenter__()

        # streamablehttp_client is an async context manager yielding (read, write, get_sid)
        cm = streamablehttp_client(url, headers=headers)
        read, write, _get_sid = await self._exit_stack.enter_async_context(cm)

        self._mcp = await self._exit_stack.enter_async_context(
            ClientSession(read, write)
        )
        await self._mcp.initialize()
        logger.info("MCP connected to %s", url)

    async def register_session(self, task_id: str = "", persona_id: str = "") -> dict:
        args = {}
        if task_id:
            args["task_id"] = task_id
        if persona_id:
            args["persona_id"] = persona_id

        result = await self._mcp.call_tool("register_session", args)
        parsed = _parse_tool_result(result)

        if parsed.get("session_id"):
            self._session_id = parsed["session_id"]
        return parsed

    async def start_session(self) -> dict:
        result = await self._mcp.call_tool("start_session", {})
        return _parse_tool_result(result)

    async def list_tools(self) -> list[dict]:
        tools_result = await self._mcp.list_tools()
        all_tools = convert_mcp_tools(tools_result)
        return [t for t in all_tools if t["name"] not in _PROTOCOL_TOOLS]

    async def call_tool(self, name: str, arguments: dict) -> str:
        result = await self._mcp.call_tool(name, arguments)
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
        return "\n".join(parts) if parts else ""

    async def send_message(self, text: str, attachments: Optional[list] = None) -> dict:
        args = {"text": text}
        if attachments:
            args["attachments"] = attachments
        result = await self._mcp.call_tool("send_message", args)
        return _parse_tool_result(result)

    async def close(self) -> None:
        if self._exit_stack:
            await self._exit_stack.aclose()
            self._exit_stack = None
        self._mcp = None

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    def create_tool_bridge(self) -> ToolBridge:
        """Create a ToolBridge for adapter.generate_response() tool_callback."""
        if self._mcp is None:
            raise RuntimeError("Not connected")
        return ToolBridge(self._mcp, asyncio.get_running_loop())


def _parse_tool_result(result) -> dict:
    """Parse a CallToolResult into a dict."""
    if not result.content:
        return {}
    text = result.content[0].text if hasattr(result.content[0], "text") else ""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        if isinstance(text, str) and text.strip().startswith("Error:"):
            return {"error": text}
        return {"raw": text}
