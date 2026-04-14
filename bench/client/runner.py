"""Task runner for QuantTutorBench baseline client.

Manages the conversation loop between agent and server:
1. Connect → register → start_session → list_tools
2. Loop: adapter.generate_response (one turn) → runner sends to server → receive student reply
3. Save client trace

The agent sees only domain tools (shell_exec, file_read, etc.).
send_message is a runner-to-server handshake, NOT an agent tool.
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

# Tools that are server protocol, not agent domain tools.
_PROTOCOL_TOOLS = frozenset(
    {
        "register_session",
        "start_session",
        "send_message",
        "request_evaluation",
        "get_results",
        "get_scores",
    }
)

_NON_FAILURE_TERMINAL_ERRORS = (
    "Session completed.",
    "Session is closed",
)


async def run_single_task(
    server_url: str,
    task_id: str,
    adapter_factory: Callable,
    result_dir: Optional[Path] = None,
    agent_max_steps: Optional[int] = 15,
    persona_id: Optional[str] = None,
) -> dict:
    """Run a single benchmark task end-to-end.

    Flow:
        1. Connect to server via MCP StreamableHTTP
        2. register_session(task_id) → session_id
        3. start_session() → student opening message
        4. list_tools() → domain tools (protocol tools filtered out)
        5. Per-turn loop:
           a. adapter.generate_response(conversation, domain_tools)
           b. Runner sends agent text to server via send_message
           c. Server returns student reply + status
           d. If completed → break, else append to conversation
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

                # 2. Start session
                start_result = await mcp.call_tool("start_session", {})
                start = _parse_tool_result(start_result)
                opening = start.get("student_message", "")
                logger.info(
                    "[%s] Session started: %s...",
                    task_id,
                    opening[:80],
                )

                # 3. List tools — after start_session the domain tools become visible
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

                # 4. Conversation loop
                bridge = ToolBridge(mcp, asyncio.get_running_loop())
                conversation = [{"role": "user", "content": opening}]

                # Baseline cost control: local per-turn cap, configurable by CLI.
                if agent_max_steps is not None:
                    adapter.set_agent_max_steps(agent_max_steps)

                while True:
                    # _input_history accumulates across turns (tool_use/tool_result context).
                    # SDK compaction handles token overflow automatically.
                    # Do NOT call set_task_context("") — it would erase tool memory.

                    # Agent turn: generate response using domain tools only
                    response = await asyncio.to_thread(
                        adapter.generate_response,
                        messages=list(conversation),
                        available_tools=domain_tools,
                        tool_callback=bridge.call,
                    )

                    if not response or not response.strip():
                        response = "(no response)"

                    logger.info(
                        "[%s] Agent response (%d chars): %s...",
                        task_id,
                        len(response),
                        response[:100],
                    )

                    # Runner handshake: send agent's text to server
                    send_result = await mcp.call_tool(
                        "send_message",
                        {"text": response},
                    )
                    data = _parse_tool_result(send_result)

                    status = data.get("status", "")
                    error = data.get("error", "")
                    student_msg = data.get("student_message", "")

                    logger.info(
                        "[%s] Student reply (status=%s): %s...",
                        task_id,
                        status or error[:40],
                        student_msg[:80],
                    )

                    # Exit on completion or error (permission denied, max_turns, etc.)
                    if status == "completed" or error:
                        conversation.append({"role": "assistant", "content": response})
                        if student_msg:
                            conversation.append(
                                {"role": "user", "content": student_msg}
                            )
                        if error and not _is_non_failure_terminal_error(error):
                            terminal_error = error
                            needs_session_cleanup = True
                            logger.warning(
                                "[%s] Session ended with error: %s", task_id, error
                            )
                        break

                    # Append exchange and continue
                    conversation.append({"role": "assistant", "content": response})
                    conversation.append({"role": "user", "content": student_msg})

                logger.info(
                    "[%s] Conversation complete: %d messages",
                    task_id,
                    len(conversation),
                )

    except Exception as exc:
        terminal_error = str(exc)
        needs_session_cleanup = session_id is not None
        logger.error("[%s] Task failed: %s", task_id, exc, exc_info=True)

    if needs_session_cleanup and session_id:
        await _delete_mcp_session(server_url, session_id)

    duration = time.time() - start_time

    # 5. Save client-side trace
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
    agent_max_steps: Optional[int] = 15,
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


def _is_non_failure_terminal_error(error: str) -> bool:
    """Return True for server terminal signals that should not count as failure."""
    return any(marker in error for marker in _NON_FAILURE_TERMINAL_ERRORS)


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
