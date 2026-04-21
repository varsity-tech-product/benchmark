"""Task runner for QuantTutorBench baseline client.

Two entry points:
- ``run_via_attach``: Run flow (create run → claim → connect → session)
- ``run_single_task``: Legacy direct flow (kept for internal testing only)

Both use the SessionTransport abstraction for protocol-agnostic execution.
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Callable, Optional

import httpx

from .cost_tracker import aggregate_cost
from .trace_writer import save_client_trace
from .transports.base import SessionTransport
from .transports.mcp_transport import MCPTransport
from .transports.rest_transport import RESTTransport

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run flow: create run → claim/start → connect → session
# ---------------------------------------------------------------------------


async def run_via_attach(
    server_base_url: str,
    token: str,
    adapter_factory: Callable,
    result_dir: Optional[Path] = None,
    protocol: str = "mcp",
) -> dict:
    """Execute a benchmark session by attaching to an existing run.

    The run must already be in CLAIMED state (token obtained from
    /ui/runs or /client/runs/start).
    """
    adapter = adapter_factory()
    session_id = None
    task_label = ""
    start_time = time.time()
    terminal_error: Optional[str] = None

    transport = _create_transport(protocol)

    try:
        # 1. Claim (if not already claimed — /client/runs/claim)
        base = server_base_url.rstrip("/")
        async with httpx.AsyncClient(base_url=base, timeout=30.0) as http:
            resp = await http.post(
                "/client/runs/claim",
                json={
                    "run_token": token,
                    "client": {"name": "baseline_client", "version": "0.1.0"},
                },
            )
            if resp.status_code == 401:
                # Already claimed (by /client/runs/start) — resolve run info
                # Try the token to find the run
                pass
            elif resp.status_code != 200:
                raise RuntimeError(f"Claim failed: {resp.text}")

            claim_data = resp.json() if resp.status_code == 200 else {}
            mcp_url = claim_data.get("mcp_url", f"{base}/mcp")
            task_label = claim_data.get("public_task_label", "")

        # 2. Connect transport
        auth_headers = {"Authorization": f"Bearer {token}"}
        if protocol == "mcp":
            await transport.connect(mcp_url, headers=auth_headers)
        else:
            await transport.connect(base, headers=auth_headers)

        # 3. Register session (task_id resolved from run on server side)
        reg = await transport.register_session()
        if reg.get("error"):
            raise RuntimeError(f"Registration failed: {reg['error']}")
        session_id = reg.get("session_id")
        logger.info("[%s] Registered: session=%s", task_label, session_id)

        # 4. Start session
        start = await transport.start_session()
        background = start.get("background", "")
        opening = start.get("student_message", "")
        logger.info("[%s] Session started: %s...", task_label, opening[:80])

        # 5. List tools
        domain_tools = await transport.list_tools()
        logger.info("[%s] %d domain tools", task_label, len(domain_tools))

        # 6. Build initial conversation
        conversation = []
        if background:
            conversation.append(
                {"role": "user", "content": f"[Session Background]\n{background}"}
            )
            conversation.append({"role": "assistant", "content": "Understood."})
        conversation.append({"role": "user", "content": opening})

        # 7. Run agent
        if protocol == "mcp" and isinstance(transport, MCPTransport):
            bridge = transport.create_tool_bridge()
            response = await asyncio.to_thread(
                adapter.generate_response,
                messages=conversation,
                available_tools=domain_tools,
                tool_callback=bridge.call,
            )
        else:
            # REST transport: create a sync bridge over the async transport
            bridge = _RESTToolBridge(transport, asyncio.get_running_loop())
            response = await asyncio.to_thread(
                adapter.generate_response,
                messages=conversation,
                available_tools=domain_tools,
                tool_callback=bridge.call,
            )

        logger.info(
            "[%s] Session complete (%d chars final text)",
            task_label,
            len(response or ""),
        )

    except Exception as exc:
        terminal_error = str(exc)
        logger.error("[%s] Task failed: %s", task_label, exc, exc_info=True)
    finally:
        try:
            await transport.close()
        except Exception:
            pass

    duration = time.time() - start_time

    # Save client trace
    agent_cost = {}
    try:
        records = adapter.get_token_records()
        agent_cost = aggregate_cost(records) if records else {}
        if result_dir and session_id:
            save_client_trace(
                result_dir=result_dir,
                session_id=session_id,
                task_id=task_label,
                duration_seconds=duration,
                agent_cost=agent_cost,
                thinking_trace=adapter.get_thinking_trace(),
                content_blocks=adapter.get_content_blocks(),
            )
    except Exception as exc:
        logger.warning("[%s] Failed to save client trace: %s", task_label, exc)

    try:
        adapter.close()
    except Exception:
        pass

    result = {
        "task_id": task_label,
        "session_id": session_id,
        "agent_cost": agent_cost,
        "duration_seconds": round(duration, 2),
    }
    if terminal_error:
        result["error"] = terminal_error
    return result


# ---------------------------------------------------------------------------
# Convenience: create run + attach in one call
# ---------------------------------------------------------------------------


async def run_task(
    server_base_url: str,
    task: str,
    adapter_factory: Callable,
    result_dir: Optional[Path] = None,
    protocol: str = "mcp",
) -> dict:
    """Create a run + claim + execute. One-step convenience entry point.

    Calls POST /client/runs/start to get a token, then run_via_attach.
    """
    base = server_base_url.rstrip("/")
    async with httpx.AsyncClient(base_url=base, timeout=30.0) as http:
        resp = await http.post(
            "/client/runs/start",
            json={
                "task": task,
                "client": {"name": "baseline_client", "version": "0.1.0"},
            },
        )
        if resp.status_code != 200:
            return {"task_id": task, "error": f"Create run failed: {resp.text}"}
        data = resp.json()
        token = data["token"]
        logger.info("[%s] Run created: %s", task, data.get("run_id", "?"))

    return await run_via_attach(
        server_base_url=server_base_url,
        token=token,
        adapter_factory=adapter_factory,
        result_dir=result_dir,
        protocol=protocol,
    )


async def run_multiple_tasks(
    server_base_url: str,
    tasks: list[str],
    adapter_factory: Callable,
    workers: int = 1,
    result_dir: Optional[Path] = None,
    protocol: str = "mcp",
) -> list[dict]:
    """Run multiple tasks with bounded concurrency."""
    semaphore = asyncio.Semaphore(workers)

    async def _run_with_sem(task: str) -> dict:
        async with semaphore:
            return await run_task(
                server_base_url,
                task,
                adapter_factory,
                result_dir,
                protocol,
            )

    coros = [_run_with_sem(t) for t in tasks]
    results = await asyncio.gather(*coros, return_exceptions=True)

    out = []
    for task, r in zip(tasks, results):
        if isinstance(r, Exception):
            logger.error("[%s] Exception: %s", task, r)
            out.append({"task_id": task, "error": str(r)})
        else:
            out.append(r)
    return out


# ---------------------------------------------------------------------------
# Legacy: direct MCP connection (used only by old tests internally)
# ---------------------------------------------------------------------------


async def run_single_task(
    server_url: str,
    task_id: str,
    adapter_factory: Callable,
    result_dir: Optional[Path] = None,
    persona_id: Optional[str] = None,
) -> dict:
    """Legacy: run a task via direct MCP connection (no Run layer).

    Kept for backward compatibility with existing test infrastructure.
    New code should use run_task() or run_via_attach().
    """
    from mcp.client.session import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    from .tool_bridge import ToolBridge, convert_mcp_tools

    _PROTOCOL_TOOLS = frozenset(
        {
            "register_session",
            "start_session",
        }
    )

    adapter = adapter_factory()
    session_id = None
    start_time = time.time()
    terminal_error: Optional[str] = None

    try:
        async with streamablehttp_client(server_url) as (read, write, get_sid):
            async with ClientSession(read, write) as mcp:
                await mcp.initialize()

                register_args = {"task_id": task_id}
                if persona_id:
                    register_args["persona_id"] = persona_id
                reg_result = await mcp.call_tool("register_session", register_args)
                reg = _parse_tool_result(reg_result)
                if not reg.get("accepted") and not reg.get("session_id"):
                    raise RuntimeError(
                        f"Registration failed: {reg.get('error', 'unknown')}"
                    )
                session_id = reg["session_id"]

                start_result = await mcp.call_tool("start_session", {})
                start = _parse_tool_result(start_result)
                background = start.get("background", "")
                opening = start.get("student_message", "")

                tools_result = await mcp.list_tools()
                all_tools = convert_mcp_tools(tools_result)
                domain_tools = [
                    t for t in all_tools if t["name"] not in _PROTOCOL_TOOLS
                ]

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

                bridge = ToolBridge(mcp, asyncio.get_running_loop())

                await asyncio.to_thread(
                    adapter.generate_response,
                    messages=conversation,
                    available_tools=domain_tools,
                    tool_callback=bridge.call,
                )

    except Exception as exc:
        terminal_error = str(exc)
        logger.error("[%s] Task failed: %s", task_id, exc, exc_info=True)

    duration = time.time() - start_time

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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_transport(protocol: str) -> SessionTransport:
    if protocol == "rest":
        return RESTTransport()
    return MCPTransport()


class _RESTToolBridge:
    """Sync bridge for REST transport, matching ToolBridge interface."""

    def __init__(self, transport: RESTTransport, loop: asyncio.AbstractEventLoop):
        self._transport = transport
        self._loop = loop

    def call(self, tool_name: str, **kwargs) -> str:
        future = asyncio.run_coroutine_threadsafe(
            self._transport.call_tool(tool_name, kwargs),
            self._loop,
        )
        # Heavy tools now queue behind a process-wide semaphore and then
        # run up to the server's 600s backtest timeout, so the bridge
        # must allow strictly more than RESTTransport._JOB_POLL_MAX_S
        # (900s) plus a small network slop.
        return future.result(timeout=960)


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
