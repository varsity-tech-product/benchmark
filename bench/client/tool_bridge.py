"""Sync adapter <-> async MCP HTTP bridge.

The adapter's ``generate_response`` runs synchronously in a worker thread
(via ``asyncio.to_thread`` in the runner).  When the adapter calls a tool
through its ``tool_callback``, this bridge forwards the call to the async
MCP ``ClientSession.call_tool`` running on the event loop.

"""

import asyncio
import json
import logging
from typing import Any

from mcp.client.session import ClientSession

logger = logging.getLogger(__name__)


class ToolBridge:
    """Bridge between a synchronous adapter tool_callback and async MCP client.

    Usage (inside the async runner)::

        bridge = ToolBridge(mcp_session, asyncio.get_running_loop())

        # Adapter runs in a thread, calls bridge.call synchronously
        await asyncio.to_thread(
            adapter.generate_response,
            messages=[...],
            available_tools=tools,
            tool_callback=bridge.call,
        )
    """

    def __init__(self, mcp: ClientSession, loop: asyncio.AbstractEventLoop):
        self._mcp = mcp
        self._loop = loop

    def call(self, tool_name: str, **kwargs: Any) -> str:
        """Synchronous tool_callback — blocks until the MCP server responds.

        Matches the adapter's ``tool_callback(tool_name, **kwargs) -> str``
        signature expected by ``BaseAgentAdapter.generate_response``.
        """
        # Truncate args for logging
        args_summary = {
            k: (str(v)[:80] + "..." if len(str(v)) > 80 else v)
            for k, v in kwargs.items()
        }
        logger.debug("[ToolBridge] -> %s(%s)", tool_name, args_summary)

        future = asyncio.run_coroutine_threadsafe(
            self._call_async(tool_name, kwargs),
            self._loop,
        )
        # Block the worker thread until the async call completes.
        # Timeout is generous — some tools (run_backtest) can take minutes.
        result = future.result(timeout=600)

        result_preview = result[:200] + "..." if len(result) > 200 else result
        logger.debug("[ToolBridge] <- %s: %s", tool_name, result_preview)
        return result

    async def _call_async(self, tool_name: str, arguments: dict) -> str:
        """Async MCP call_tool wrapper."""
        try:
            result = await self._mcp.call_tool(tool_name, arguments)
            # CallToolResult.content is a list of content blocks.
            # Concatenate text blocks into a single string.
            parts = []
            for block in result.content:
                if hasattr(block, "text"):
                    parts.append(block.text)
                elif hasattr(block, "data"):
                    parts.append(f"[binary: {len(block.data)} bytes]")
            return "\n".join(parts) if parts else ""
        except Exception as exc:
            logger.error("[ToolBridge] %s EXCEPTION: %s", tool_name, exc)
            return json.dumps({"error": str(exc)})


def convert_mcp_tools(list_tools_result) -> list[dict]:
    """Convert MCP ``ListToolsResult`` to the adapter's tool schema format.

    The adapter expects::

        [{"name": str, "description": str, "parameters": {param: {type, description}}}, ...]

    MCP ``Tool`` has ``inputSchema`` (full JSON Schema with properties/required).
    We preserve the full schema for clients that can consume it directly, and
    also flatten it to the simpler format the adapter's
    ``normalize_tool_params`` expects.
    """
    tools = []
    for tool in list_tools_result.tools:
        schema = tool.inputSchema or {}
        properties = schema.get("properties", {})
        required = set(schema.get("required", []))

        params = {}
        for pname, pschema in properties.items():
            entry = {
                "type": pschema.get("type", "string"),
                "description": pschema.get("description", pname),
            }
            if pname in required:
                entry["required"] = True
            if "items" in pschema:
                entry["items"] = pschema["items"]
            params[pname] = entry

        tools.append(
            {
                "name": tool.name,
                "description": tool.description or "",
                "parameters": params,
                "input_schema": schema,
            }
        )
    return tools
