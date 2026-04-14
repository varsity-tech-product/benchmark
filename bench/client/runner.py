"""Task runner for QuantTutorBench baseline client.

Minimal orchestration layer:
1. Connect → register → start_session → list_tools
2. Inject background + student opening into conversation
3. Single adapter.generate_response() call — BetaToolRunner manages
   the full session (tool calls, send_message, multi-turn dialogue)
4. Save client trace

The agent sees ALL domain tools including send_message and get_background.
It autonomously decides when to use tools and when to message the student.
"""

import asyncio
import json
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Optional

from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamablehttp_client

from .cost_tracker import aggregate_cost
from .tool_bridge import ToolBridge, convert_mcp_tools
from .trace_writer import save_client_trace

logger = logging.getLogger(__name__)

# Session setup/teardown tools — not part of the teaching interaction.
# These are called by the runner before/after the agent loop.
_PROTOCOL_TOOLS = frozenset(
    {
        "register_session",
        "start_session",
        "request_evaluation",
        "get_results",
        "get_scores",
    }
)


async def run_single_task(
    server_url: str,
    task_id: str,
    adapter_factory: Callable,
    result_dir: Optional[Path] = None,
    agent_max_steps: Optional[int] = 200,
    persona_id: Optional[str] = None,
) -> dict:
    """Run a single benchmark task end-to-end.

    Flow:
        1. Connect to server via MCP StreamableHTTP
        2. register_session(task_id) → session_id
        3. start_session() → background + student opening message
        4. list_tools() → all tools (send_message visible to agent)
        5. Single adapter.generate_response() — agent autonomously
           calls tools, sends messages to student, handles replies
        6. Save client trace (client_trace.json + client_trace.md)
    """
    adapter = adapter_factory()
    session_id = None
    start_time = time.time()
    terminal_error: Optional[str] = None
    needs_session_cleanup = False

    try:
        async with streamablehttp_client(server_url) as (read, write, get_sid):
            async with ClientSession(read, write) as mcp:
                await mcp.initialize()

                # 1. Register
                register_args = {"task_id": task_id}
                if persona_id:
                    register_args["persona_id"] = persona_id
                reg_result = await mcp.call_tool(
                    "register_session",
                    register_args,
                )
                reg = _parse_tool_result(reg_result)
                if not reg.get("accepted"):
                    raise RuntimeError(
                        f"Registration failed: {reg.get('error', 'unknown')}"
                    )
                session_id = reg["session_id"]
                logger.info("[%s] Registered: session=%s", task_id, session_id)

                # 2. Start session — returns background + student opening
                start_result = await mcp.call_tool("start_session", {})
                start = _parse_tool_result(start_result)
                background = start.get("background", "")
                opening = start.get("student_message", "")
                logger.info(
                    "[%s] Session started: %s...",
                    task_id,
                    opening[:80],
                )

                # 3. List tools — agent sees everything including
                #    send_message and get_background
                tools_result = await mcp.list_tools()
                all_tools = convert_mcp_tools(tools_result)
                domain_tools = [
                    t for t in all_tools if t["name"] not in _PROTOCOL_TOOLS
                ]
                logger.info(
                    "[%s] %d domain tools (filtered from %d total)",
                    task_id,
                    len(domain_tools),
                    len(all_tools),
                )

                # 4. Build initial conversation
                conversation = []
                if background:
                    conversation.append(
                        {
                            "role": "user",
                            "content": f"[Session Background]\n{background}",
                        }
                    )
                    conversation.append({"role": "assistant", "content": "Understood."})
                conversation.append({"role": "user", "content": opening})

                # 5. Single agent run — BetaToolRunner handles the full
                #    session including send_message calls to the student
                bridge = ToolBridge(mcp, asyncio.get_running_loop())
                if agent_max_steps is not None and agent_max_steps > 0:
                    adapter.set_agent_max_steps(agent_max_steps)

                response = await asyncio.to_thread(
                    adapter.generate_response,
                    messages=conversation,
                    available_tools=domain_tools,
                    tool_callback=bridge.call,
                )

                logger.info(
                    "[%s] Session complete (%d chars final text)",
                    task_id,
                    len(response or ""),
                )

    except Exception as exc:
        terminal_error = str(exc)
        needs_session_cleanup = session_id is not None
        logger.error("[%s] Task failed: %s", task_id, exc, exc_info=True)

    if needs_session_cleanup and session_id:
        await _delete_mcp_session(server_url, session_id)

    duration = time.time() - start_time

    # 6. Save client-side trace
    agent_cost = {}
    try:
        records = adapter.get_token_records()
        agent_cost = aggregate_cost(records) if records else {}

        if result_dir and session_id:
            save_client_trace(
                result_dir=result_dir,
                session_id=session_id,
                task_id=task_id,
                duration_seconds=duration,
                agent_cost=agent_cost,
                thinking_trace=adapter.get_thinking_trace(),
                content_blocks=adapter.get_content_blocks(),
            )
    except Exception as exc:
        logger.warning("[%s] Failed to save client trace: %s", task_id, exc)

    try:
        adapter.close()
    except Exception:
        pass

    result = {
        "task_id": task_id,
        "session_id": session_id,
        "agent_cost": agent_cost,
        "duration_seconds": round(duration, 2),
    }
    if terminal_error:
        result["error"] = terminal_error
    return result


async def run_multiple_tasks(
    server_url: str,
    task_ids: list[str],
    adapter_factory: Callable,
    workers: int = 1,
    result_dir: Optional[Path] = None,
    agent_max_steps: Optional[int] = 200,
    persona_id: Optional[str] = None,
) -> list[dict]:
    """Run multiple tasks with bounded concurrency."""
    semaphore = asyncio.Semaphore(workers)

    async def _run_with_sem(tid: str) -> dict:
        async with semaphore:
            return await run_single_task(
                server_url,
                tid,
                adapter_factory,
                result_dir,
                agent_max_steps,
                persona_id,
            )

    tasks = [_run_with_sem(tid) for tid in task_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    out = []
    for tid, r in zip(task_ids, results):
        if isinstance(r, Exception):
            logger.error("[%s] Exception: %s", tid, r)
            out.append({"task_id": tid, "error": str(r)})
        else:
            out.append(r)
    return out


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


async def _delete_mcp_session(server_url: str, session_id: str) -> None:
    """Best-effort MCP session cleanup for failed runs."""
    await asyncio.to_thread(_delete_mcp_session_sync, server_url, session_id)


def _delete_mcp_session_sync(server_url: str, session_id: str) -> None:
    req = urllib.request.Request(
        server_url,
        method="DELETE",
        headers={"mcp-session-id": session_id},
    )
    try:
        with urllib.request.urlopen(req, timeout=5):
            return
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            logger.debug(
                "Failed to delete MCP session %s: HTTP %s",
                session_id[:8],
                exc.code,
            )
    except Exception as exc:
        logger.debug("Failed to delete MCP session %s: %s", session_id[:8], exc)
