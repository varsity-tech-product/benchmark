"""HTTP application for QuantTutorBench Server.

Combined ASGI app:
- ``/mcp``       — MCP StreamableHTTP endpoint (per-session MCP servers)
- ``/session/*`` — REST endpoints for all benchmark interactions + queries

Each MCP HTTP session gets its own ``SessionState`` and ``mcp.server.Server``
instance.  REST sessions share ``SessionState`` but have no MCP server/transport.
Both protocols use ``_request_lock`` for write-operation serialization.

Reference: dual_protocol_design.md
"""

import asyncio
import contextlib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import AsyncIterator
from uuid import uuid4

import anyio
from mcp.server import Server
from mcp.server.streamable_http import StreamableHTTPServerTransport
from mcp.types import TextContent
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from server.audit import record_event
from server.config.bootstrap import load_server_env
from server.config.llm_config import EVAL_DEFAULT_MODEL
from server.quota import QuotaExceeded, QuotaManager
from server.run import JobStore, RunService, RunStore, TaskCatalog
from server.run.jobs import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_RUNNING,
)
from server.run.models import RunStatus
from server.web.ui_app import extract_bearer_token, resolve_run_from_token, ui_routes

from .limits import HEAVY_TOOLS, backtest_sem
from .protocol import (
    SERVER_ONLY_EVAL_TOOLS,
    TOOL_ENDPOINT_BLOCKED,
    SessionPhase,
    check_permission,
)
from .session_api import SessionState

logger = logging.getLogger(__name__)

# Sweeper configuration
_SWEEPER_INTERVAL: int = 30
_UNREGISTERED_IDLE_TIMEOUT: int = 300  # 5 min
_REGISTERED_IDLE_TIMEOUT: int = 300  # 5 min
_COMPLETED_IDLE_TIMEOUT: int = 3600  # 1 hour


def _audit_user_from_run(run) -> dict:
    return {
        "user_id": getattr(run, "owner_user_id", "") or "",
        "github_login": getattr(run, "owner_github_login", "") or "",
        "email": getattr(run, "owner_email", "") or "",
        "role": "user",
    }


def _audit_user_from_state(state: SessionState) -> dict:
    return {
        "user_id": state.owner_user_id,
        "github_login": state.owner_github_login,
        "email": state.owner_email,
        "role": "user",
    }


def _mcp_tool_success(contents: list[TextContent]) -> bool:
    if not contents:
        return True
    text = getattr(contents[0], "text", "")
    try:
        payload = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return True
    if not isinstance(payload, dict):
        return True
    if payload.get("success") is False:
        return False
    if payload.get("error"):
        return False
    return True


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _rest_session_token_required() -> bool:
    if os.environ.get("QTB_REQUIRE_SESSION_TOKEN", "").strip():
        return _bool_env("QTB_REQUIRE_SESSION_TOKEN", False)
    return (
        os.environ.get("QTB_AUTH_MODE", "disabled").strip().lower() == "github"
        or _bool_env("QTB_REQUIRE_CLIENT_AUTH", False)
        or bool(os.environ.get("QTB_CLIENT_API_KEYS", "").strip())
    )


def _authorize_rest_session_request(
    request: Request, manager: "BenchSessionManager", state: SessionState
) -> JSONResponse | None:
    """Require the run token that owns this REST session when auth is enabled."""
    if not _rest_session_token_required() or not state.run_id:
        return None

    raw = extract_bearer_token(request)
    if not raw:
        return JSONResponse(
            {"error": "Authorization: Bearer <token> required"},
            status_code=401,
        )
    run = manager._run_service.resolve_token(raw, allow_expired_bound=True)
    if run is None:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)
    if run.run_id != state.run_id:
        return JSONResponse({"error": "Run token does not own this session"}, 403)
    return None


def _authorize_rest_job_request(
    request: Request, manager: "BenchSessionManager", sid: str
) -> JSONResponse | None:
    """Authorize polling a job by matching bearer run token to session id."""
    if not _rest_session_token_required():
        return None

    raw = extract_bearer_token(request)
    if not raw:
        return JSONResponse(
            {"error": "Authorization: Bearer <token> required"},
            status_code=401,
        )
    run = manager._run_service.resolve_token(raw, allow_expired_bound=True)
    if run is None:
        return JSONResponse({"error": "Invalid or expired token"}, status_code=401)
    if run.session_id != sid:
        return JSONResponse({"error": "Run token does not own this session"}, 403)
    return None


class NoCacheStaticFiles(StaticFiles):
    """StaticFiles variant that forces browser revalidation for UI assets."""

    async def get_response(self, path: str, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


class BenchSessionManager:
    """Manages per-session MCP servers and transports.

    Architecture:
        One ``mcp.server.Server`` per MCP session — handlers close over
        ``SessionState``.  REST sessions have no MCP server/transport.
        Both protocols share the same ``_sessions`` dict and same
        ``SessionState`` methods.

    Lifecycle:
        ``run()`` creates the anyio task group + sweeper.  Each new MCP
        connection spawns a task.  On shutdown all sessions are cleaned up.
    """

    def __init__(
        self,
        use_docker: bool = True,
        bench_root: str | Path | None = None,
        eval_model: str = EVAL_DEFAULT_MODEL,
    ):
        self.use_docker = use_docker
        self.bench_root = (
            Path(bench_root) if bench_root else Path(__file__).parent.parent.parent
        )
        self.eval_model = eval_model

        self._sessions: dict[str, SessionState] = {}
        self._transports: dict[str, StreamableHTTPServerTransport] = {}
        self._task_group: anyio.abc.TaskGroup | None = None

        # Run layer
        self._catalog = TaskCatalog(self.bench_root)
        self._run_store = RunStore(self.bench_root / "results" / "runs")
        self._run_service = RunService(self._catalog, self._run_store)

        # Job layer — async tool dispatch (slice 2)
        self._job_store = JobStore(self.bench_root / "results" / "jobs")
        self._quota_manager = QuotaManager(self.bench_root)
        # Strong refs for background tasks so the event loop does not GC them
        # mid-flight (asyncio.create_task caveat).
        self._job_tasks: dict[str, asyncio.Task] = {}

    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """Lifespan context — manages task group for all sessions."""
        # Any pending/running jobs on disk at startup lost their worker when
        # the previous process died; fail them cleanly so clients polling a
        # stale job_id get a terminal state instead of timing out.
        orphans = await asyncio.to_thread(self._job_store.mark_orphans_failed)
        if orphans:
            logger.warning(
                "Startup: failed %d orphan tool job(s) from previous run", orphans
            )
        async with anyio.create_task_group() as tg:
            self._task_group = tg
            tg.start_soon(self._session_sweeper)
            logger.info("BenchSessionManager started")
            try:
                yield
            finally:
                logger.info(
                    "BenchSessionManager shutting down — %d active sessions",
                    len(self._sessions),
                )
                for sid in list(self._sessions):
                    try:
                        await self._cleanup_session(sid)
                    except Exception as e:
                        logger.warning("Cleanup failed for session %s: %s", sid, e)
                tg.cancel_scope.cancel()
                self._sessions.clear()
                self._transports.clear()

    # ------------------------------------------------------------------
    # Session sweeper
    # ------------------------------------------------------------------

    async def _session_sweeper(self) -> None:
        """Periodically check for timed-out and idle sessions.

        Four cleanup policies (dual_protocol_design.md S8.3):
        1. UNREGISTERED + idle > 5 min  → remove (MCP initialize without register)
        2. REGISTERED   + idle > 5 min  → destroy container + remove
        3. IN_SESSION   + past deadline → save results + complete + destroy
        4. COMPLETED    + idle > 1 hour → remove (results persisted on disk)
        """
        while True:
            await anyio.sleep(_SWEEPER_INTERVAL)
            now = time.time()
            for sid, state in list(self._sessions.items()):
                try:
                    idle = now - state._last_activity

                    if (
                        state.phase == SessionPhase.UNREGISTERED
                        and idle > _UNREGISTERED_IDLE_TIMEOUT
                    ):
                        logger.warning("Session %s UNREGISTERED idle — removing", sid)
                        await self._cleanup_session(sid)

                    elif (
                        state.phase == SessionPhase.REGISTERED
                        and idle > _REGISTERED_IDLE_TIMEOUT
                    ):
                        logger.warning("Session %s REGISTERED idle — removing", sid)
                        await self._cleanup_session(sid)

                    elif (
                        state.phase == SessionPhase.IN_SESSION
                        and state.proxy
                        and state.proxy._deadline
                        and now > state.proxy._deadline
                    ):
                        logger.warning(
                            "Session %s past deadline — force-completing", sid
                        )
                        async with state._request_lock:
                            if state.phase != SessionPhase.IN_SESSION:
                                continue
                            save_ok = False
                            try:
                                if state.session is not None:
                                    state.session.force_complete("timeout")
                                state.phase = SessionPhase.COMPLETED
                                state._save_results()
                                save_ok = True
                            except Exception:
                                logger.warning(
                                    "Session %s save failed in sweep",
                                    sid,
                                    exc_info=True,
                                )
                            state._destroy_container()
                            if save_ok:
                                state._trigger_auto_eval()
                            # Notify Run layer so run status tracks the
                            # force-completion. Swallow ValueError from
                            # mark_completed guards (e.g. run was cancelled
                            # between the deadline check and here).
                            if state.run_id:
                                result_dir = (
                                    str(state._result_dir)
                                    if getattr(state, "_result_dir", None)
                                    else None
                                )
                                try:
                                    self._run_service.mark_completed(
                                        state.run_id, result_dir
                                    )
                                except ValueError as exc:
                                    logger.info(
                                        "Run %s mark_completed skipped in sweep: %s",
                                        state.run_id,
                                        exc,
                                    )
                                except Exception:
                                    logger.warning(
                                        "Run %s mark_completed failed in sweep",
                                        state.run_id,
                                        exc_info=True,
                                    )

                    elif (
                        state.phase == SessionPhase.COMPLETED
                        and idle > _COMPLETED_IDLE_TIMEOUT
                    ):
                        logger.info(
                            "Session %s COMPLETED idle — removing from memory", sid
                        )
                        await self._cleanup_session(sid)

                except Exception as exc:
                    logger.warning("Sweeper error for session %s: %s", sid, exc)

            # Run-level timeout checks
            try:
                self._sweep_runs(now)
            except Exception as exc:
                logger.warning("Run sweeper error: %s", exc)

    def _sweep_runs(self, now: float) -> None:
        """Check for timed-out runs (waiting with expired token, claimed too long)."""
        from datetime import datetime, timezone

        _CLAIMED_IDLE_TIMEOUT = 300  # 5 minutes

        for run in self._run_service.list_runs():
            try:
                if run.status == RunStatus.WAITING and run.token_expires_at:
                    try:
                        exp = datetime.fromisoformat(run.token_expires_at)
                        if datetime.now(timezone.utc) > exp:
                            self._run_service.mark_failed(run.run_id, "Token expired")
                            logger.info(
                                "Run %s: token expired — marked failed", run.run_id
                            )
                    except ValueError:
                        pass

                elif run.status == RunStatus.CLAIMED and run.claimed_at:
                    try:
                        claimed = datetime.fromisoformat(run.claimed_at)
                        age = (datetime.now(timezone.utc) - claimed).total_seconds()
                        if age > _CLAIMED_IDLE_TIMEOUT:
                            self._run_service.mark_failed(
                                run.run_id, "Client did not connect within timeout"
                            )
                            logger.info(
                                "Run %s: claimed timeout — marked failed",
                                run.run_id,
                            )
                    except ValueError:
                        pass
            except Exception as exc:
                logger.warning("Run sweeper error for %s: %s", run.run_id, exc)

    # ------------------------------------------------------------------
    # Run-level cancel (串联 RunService + Session cleanup)
    # ------------------------------------------------------------------

    async def cancel_run(self, run_id: str) -> None:
        """Cancel a run. For active runs, also cancels the session."""
        run = self._run_service.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")

        # Update run status FIRST (before cleanup, which would mark_failed)
        self._run_service.cancel_run(run_id)

        # Active run: then cancel the session
        if run.status == RunStatus.ACTIVE and run.session_id:
            state = self.get_session(run.session_id)
            if state and state.proxy and state.proxy._cancel_event:
                state.proxy._cancel_event.set()
            await self._cleanup_session(run.session_id, persist_partial=True)

    # ------------------------------------------------------------------
    # MCP request routing
    # ------------------------------------------------------------------

    async def handle_mcp_request(self, scope, receive, send):
        """ASGI handler for ``/mcp``."""
        request = Request(scope, receive)
        session_id = request.headers.get("mcp-session-id")
        logger.debug(
            "/mcp %s session_id=%s",
            request.method,
            session_id[:8] if session_id else "NEW",
        )

        # DELETE
        if request.method == "DELETE":
            if session_id and session_id in self._transports:
                transport = self._transports[session_id]
                await self._handle_mcp_transport_request(
                    transport, session_id, scope, receive, send
                )
                await self._cleanup_session(session_id, persist_partial=True)
                return
            resp = Response(status_code=404, content="Session not found")
            await resp(scope, receive, send)
            return

        # Existing session
        if session_id and session_id in self._transports:
            await self._handle_mcp_transport_request(
                self._transports[session_id], session_id, scope, receive, send
            )
            return

        # Unknown session ID — try to restore from server storage
        if session_id and session_id not in self._transports:
            state = self.get_or_restore_session(session_id)
            if state is None:
                resp = Response(status_code=404, content="Unknown session ID")
                await resp(scope, receive, send)
                return

            # Restored from storage — set up MCP server + transport
            server = self._create_mcp_server(state)
            transport = StreamableHTTPServerTransport(
                mcp_session_id=session_id,
                is_json_response_enabled=False,
                event_store=None,
                security_settings=None,
            )
            self._transports[session_id] = transport

            assert self._task_group is not None

            async def run_restored(
                *,
                task_status: anyio.abc.TaskStatus[None] = anyio.TASK_STATUS_IGNORED,
            ):
                async with transport.connect() as streams:
                    read_stream, write_stream = streams
                    task_status.started()
                    try:
                        await server.run(
                            read_stream,
                            write_stream,
                            server.create_initialization_options(),
                        )
                    except Exception as e:
                        logger.error(
                            "Restored session %s error: %s",
                            session_id[:8],
                            e,
                            exc_info=True,
                        )
                    finally:
                        if session_id in self._sessions:
                            await self._cleanup_session(
                                session_id, persist_partial=True
                            )

            try:
                await self._task_group.start(run_restored)
            except Exception as e:
                logger.error(
                    "Failed to start restored session %s: %s", session_id[:8], e
                )
                await self._cleanup_session(session_id)
                resp = JSONResponse({"error": "Internal server error"}, status_code=500)
                await resp(scope, receive, send)
                return

            await self._handle_mcp_transport_request(
                transport, session_id, scope, receive, send
            )
            return

        # New session — POST only
        if request.method != "POST":
            resp = Response(
                status_code=400, content="Session ID required for non-POST requests"
            )
            await resp(scope, receive, send)
            return

        # Token verification — all MCP connections require a run token
        raw_token = extract_bearer_token(request)
        if not raw_token:
            resp = Response(
                status_code=401, content="Authorization: Bearer <token> required"
            )
            await resp(scope, receive, send)
            return

        run = resolve_run_from_token(self._run_service, raw_token)
        if run is None:
            resp = Response(status_code=401, content="Invalid or expired token")
            await resp(scope, receive, send)
            return

        if run.status != RunStatus.CLAIMED:
            resp = Response(
                status_code=409,
                content=f"Run {run.run_id} is '{run.status.value}', expected 'claimed'",
            )
            await resp(scope, receive, send)
            return

        new_id = uuid4().hex
        state = SessionState(
            session_id=new_id,
            use_docker=self.use_docker,
            bench_root=self.bench_root,
            eval_model=self.eval_model,
        )
        # Bind to Run
        state.run_id = run.run_id
        state._run_task_id = run.task_id
        state.owner_user_id = run.owner_user_id
        state.owner_github_login = run.owner_github_login
        state.owner_email = run.owner_email
        state.visibility = run.visibility
        state._on_registered = lambda sid: self._run_service.bind_session(
            run.run_id, sid
        )
        state._on_completed = lambda result_dir: self._run_service.mark_completed(
            run.run_id, result_dir
        )
        self._sessions[new_id] = state

        server = self._create_mcp_server(state)
        transport = StreamableHTTPServerTransport(
            mcp_session_id=new_id,
            is_json_response_enabled=False,
            event_store=None,
            security_settings=None,
        )
        self._transports[new_id] = transport

        assert self._task_group is not None

        async def run_server(
            *,
            task_status: anyio.abc.TaskStatus[None] = anyio.TASK_STATUS_IGNORED,
        ):
            async with transport.connect() as streams:
                read_stream, write_stream = streams
                task_status.started()
                try:
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options(),
                    )
                except Exception as e:
                    logger.error(
                        "Session %s MCP server error: %s", new_id, e, exc_info=True
                    )
                finally:
                    if new_id in self._sessions:
                        await self._cleanup_session(new_id, persist_partial=True)

        try:
            await self._task_group.start(run_server)
        except Exception as e:
            logger.error("Failed to start session %s: %s", new_id, e)
            await self._cleanup_session(new_id)
            resp = JSONResponse({"error": "Internal server error"}, status_code=500)
            await resp(scope, receive, send)
            return

        await self._handle_mcp_transport_request(
            transport, new_id, scope, receive, send
        )

    async def _handle_mcp_transport_request(
        self,
        transport: StreamableHTTPServerTransport,
        session_id: str,
        scope,
        receive,
        send,
    ) -> None:
        """Handle one MCP HTTP request and persist partial state on disconnect."""
        disconnected = False

        async def watched_receive():
            nonlocal disconnected
            message = await receive()
            if message.get("type") == "http.disconnect":
                disconnected = True
            return message

        try:
            await transport.handle_request(scope, watched_receive, send)
        finally:
            method = str(scope.get("method") or "").upper()
            if (
                disconnected
                and method in {"POST", "DELETE"}
                and session_id in self._sessions
            ):
                logger.info(
                    "Session %s MCP client disconnected — cleaning up partial state",
                    session_id[:8],
                )
                await self._cleanup_session(session_id, persist_partial=True)

    # ------------------------------------------------------------------
    # Per-session MCP server factory
    # ------------------------------------------------------------------

    def _create_mcp_server(self, state: SessionState) -> Server:
        """Create an MCP Server with handlers closing over *state*.

        call_tool acquires ``_request_lock`` so MCP and REST writes
        are mutually exclusive on the same session.
        """
        server = Server("QuantTutorBench")
        state._mcp_server = server

        @server.list_tools()
        async def handle_list_tools():
            return state.get_visible_tools()

        @server.call_tool()
        async def handle_call_tool(name: str, arguments: dict):
            async with state._request_lock:
                return await self._handle_mcp_tool_call(state, name, arguments)

        return server

    async def _handle_mcp_tool_call(
        self, state: SessionState, name: str, arguments: dict
    ) -> list[TextContent]:
        """Run one MCP tool call with per-user quota checks for heavy tools."""
        if name not in HEAVY_TOOLS:
            return await state.handle_tool_call(name, arguments)

        quota_reservation: str | None = None
        try:
            quota_reservation = self._quota_manager.reserve_heavy_job(
                owner_user_id=state.owner_user_id,
                run_id=state.run_id or "",
                session_id=state.session_id,
                tool_name=name,
            )
        except QuotaExceeded as exc:
            record_event(
                self.bench_root,
                _audit_user_from_state(state),
                "tool.call",
                run_id=state.run_id or "",
                session_id=state.session_id,
                task_id=state.task_id,
                success=False,
                payload={"tool": name, "transport": "mcp", "error": str(exc)},
            )
            return [
                TextContent(
                    type="text",
                    text=json.dumps(
                        {
                            "success": False,
                            "error": str(exc),
                            "current_phase": state.phase.value,
                        }
                    ),
                )
            ]

        try:
            contents = await state.handle_tool_call(name, arguments)
            record_event(
                self.bench_root,
                _audit_user_from_state(state),
                "tool.call",
                run_id=state.run_id or "",
                session_id=state.session_id,
                task_id=state.task_id,
                success=_mcp_tool_success(contents),
                payload={"tool": name, "transport": "mcp"},
            )
            return contents
        except Exception as exc:
            record_event(
                self.bench_root,
                _audit_user_from_state(state),
                "tool.call",
                run_id=state.run_id or "",
                session_id=state.session_id,
                task_id=state.task_id,
                success=False,
                payload={"tool": name, "transport": "mcp", "error": str(exc)},
            )
            raise
        finally:
            self._quota_manager.release_heavy_job(
                owner_user_id=state.owner_user_id,
                reservation_id=quota_reservation,
            )

    # ------------------------------------------------------------------
    # Session cleanup
    # ------------------------------------------------------------------

    async def _cleanup_session(
        self,
        session_id: str,
        *,
        persist_partial: bool = False,
    ):
        """Remove session state and free resources.

        If the session is bound to a Run that is still active (not in a
        terminal state), mark the run as failed — the client disconnected
        without completing the session.
        """
        state = self._sessions.pop(session_id, None)
        transport = self._transports.pop(session_id, None)
        if not state:
            if transport:
                with contextlib.suppress(Exception):
                    await transport.terminate()
            logger.info("Session %s removed", session_id)
            return

        async with state._request_lock:
            if transport:
                with contextlib.suppress(Exception):
                    await transport.terminate()

            # Mark associated run as failed if session didn't complete normally.
            if state.run_id and state.phase != SessionPhase.COMPLETED:
                try:
                    run = self._run_service.get_run(state.run_id)
                    if (
                        run
                        and run.status.value
                        not in ("completed", "failed", "cancelled")
                    ):
                        self._run_service.mark_failed(
                            state.run_id, "Client disconnected"
                        )
                        logger.info(
                            "Run %s marked failed (client disconnected)",
                            state.run_id,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to mark run %s as failed: %s", state.run_id, exc
                    )

            try:
                state.cleanup(persist_partial=persist_partial)
            except Exception as e:
                logger.warning("Session %s cleanup error: %s", session_id, e)
        logger.info("Session %s removed", session_id)

    # ------------------------------------------------------------------
    # Helpers for REST handlers
    # ------------------------------------------------------------------

    def get_session(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def find_archived_result_dir(self, session_id: str) -> Path | None:
        """Find archived result dir for a session_id.

        New layout:
        ``{task_id}/{persona_id}/{ts}_{session_id[:12]}/`` with a mandatory
        ``.session_id`` file for exact matching.
        """
        results_root = self.bench_root / "results" / "server"
        try:
            from eval.contracts.request import resolve_result_dir

            return resolve_result_dir(session_id, results_root)
        except Exception:
            return None

    def get_archived_session_status(self, session_id: str) -> dict | None:
        state = self.get_archived_results(session_id)
        if not state:
            return None
        return {
            "session_id": session_id,
            "task_id": state.get("task_id", ""),
            "phase": "completed",
            "persona_id": state.get("persona_id", ""),
            "archived": True,
        }

    def get_archived_results(self, session_id: str) -> dict | None:
        result_dir = self.find_archived_result_dir(session_id)
        if not result_dir:
            return None
        state_path = result_dir / "run_state.json"
        if not state_path.exists():
            return None
        try:
            return json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def get_archived_scores(
        self,
        session_id: str,
        *,
        history: bool = False,
        score_id: str | None = None,
        score_ids: list[str] | None = None,
        status_filter: list[str] | None = None,
    ) -> dict | None:
        result_dir = self.find_archived_result_dir(session_id)
        if not result_dir:
            return None
        from eval.storage.score_store import get_scores_payload

        payload = get_scores_payload(
            result_dir,
            history=history,
            score_id=score_id,
            score_ids=score_ids,
            status_filter=status_filter,
        )
        payload["session_id"] = session_id
        return payload

    def list_sessions(self, task_id: str = "") -> list[dict]:
        results = []
        for sid, state in self._sessions.items():
            if task_id and state.task_id != task_id:
                continue
            results.append(
                {
                    "session_id": sid,
                    "task_id": state.task_id,
                    "phase": state.phase.value,
                    "persona_id": state.persona_id,
                }
            )
        return results

    def create_rest_session(self) -> SessionState:
        """Create a SessionState for a REST session (no MCP server/transport)."""
        new_id = uuid4().hex
        state = SessionState(
            session_id=new_id,
            use_docker=self.use_docker,
            bench_root=self.bench_root,
            eval_model=self.eval_model,
        )
        return state

    def register_rest_session(self, state: SessionState):
        """Store a successfully registered REST session."""
        self._sessions[state.session_id] = state

    def get_or_restore_session(self, session_id: str) -> SessionState | None:
        """Get a session from memory, or restore from server storage.

        Server persists all session data to disk.  If the session was
        cleaned from memory (sweeper, server restart), it can be restored
        from the persisted run_state.json.  The restored session supports
        all read operations and evaluation.
        """
        # 1. In memory — fast path
        state = self.get_session(session_id)
        if state is not None:
            return state

        # 2. On disk — restore from server storage
        result_dir = self.find_archived_result_dir(session_id)
        if result_dir is None:
            return None

        state = SessionState.restore_from_storage(
            session_id=session_id,
            result_dir=result_dir,
            bench_root=self.bench_root,
            eval_model=self.eval_model,
        )
        # Cache in memory so subsequent calls don't re-read from disk
        self._sessions[session_id] = state
        return state


# ---------------------------------------------------------------------------
# REST handlers — /session/*
# ---------------------------------------------------------------------------


_HEALTH_DISK_MIN_GB = 5.0
_HEALTH_LEAN_IMAGE_DEFAULT = "quant-tutor-env:v2.2-lean"


def _health_check_docker() -> dict:
    try:
        proc = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
        return {"ok": proc.returncode == 0}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def _health_check_lean_image() -> dict:
    image = os.environ.get("QTB_LEAN_IMAGE", _HEALTH_LEAN_IMAGE_DEFAULT)
    try:
        proc = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=3,
        )
        return {"ok": proc.returncode == 0, "image": image}
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as exc:
        return {
            "ok": False,
            "image": image,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _health_check_disk() -> dict:
    try:
        path = "/home"
        usage = shutil.disk_usage(path)
        if usage.total <= 0:
            path = "/"
            usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024**3)
        return {
            "ok": free_gb >= _HEALTH_DISK_MIN_GB,
            "path": path,
            "free_gb": round(free_gb, 2),
            "percent_free": round(usage.free / usage.total * 100, 1),
        }
    except OSError as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


async def rest_health(request: Request) -> JSONResponse:
    """``GET /health`` — deep liveness probe.

    In Docker mode validates Docker daemon, LEAN backtest image, and disk
    headroom. In no-docker mode only disk is checked so a supported local
    deployment is not reported unhealthy for a missing Docker daemon.
    Returns 200 when all applicable checks pass, 503 otherwise so uptime
    monitors and the deploy smoke test can detect real outages.
    """
    manager: BenchSessionManager = request.app.state.manager
    checks: dict[str, dict] = {}
    if manager.use_docker:
        docker, image, disk = await asyncio.gather(
            asyncio.to_thread(_health_check_docker),
            asyncio.to_thread(_health_check_lean_image),
            asyncio.to_thread(_health_check_disk),
        )
        checks["docker"] = docker
        checks["lean_image"] = image
        checks["disk"] = disk
    else:
        checks["mode"] = {"ok": True, "docker": False}
        checks["disk"] = await asyncio.to_thread(_health_check_disk)
    ok = all(c.get("ok") for c in checks.values())
    return JSONResponse(
        {"status": "ok" if ok else "down", "checks": checks},
        status_code=200 if ok else 503,
    )


async def rest_register(request: Request) -> JSONResponse:
    """``POST /session/register``

    Requires ``Authorization: Bearer <token>`` header. The task_id is
    resolved from the RunAssignment — body only needs optional persona_id.
    """
    manager: BenchSessionManager = request.app.state.manager

    # Token verification
    raw_token = extract_bearer_token(request)
    if not raw_token:
        return JSONResponse({"error": "Authorization: Bearer <token> required"}, 401)

    run = resolve_run_from_token(manager._run_service, raw_token)
    if run is None:
        return JSONResponse({"error": "Invalid or expired token"}, 401)

    if run.status != RunStatus.CLAIMED:
        return JSONResponse(
            {"error": f"Run {run.run_id} is '{run.status.value}', expected 'claimed'"},
            409,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    persona_id = body.get("persona_id")
    task_id = run.task_id  # Always from RunAssignment

    logger.info(
        "[REST] register run=%s task=%s persona=%s",
        run.run_id,
        task_id,
        persona_id or "auto",
    )
    state = manager.create_rest_session()
    state.run_id = run.run_id
    state._run_task_id = run.task_id
    state.owner_user_id = run.owner_user_id
    state.owner_github_login = run.owner_github_login
    state.owner_email = run.owner_email
    state.visibility = run.visibility
    state._on_completed = lambda result_dir: manager._run_service.mark_completed(
        run.run_id, result_dir
    )
    result = await asyncio.to_thread(state.register, task_id, persona_id)

    if "session_id" in result:
        manager.register_rest_session(state)
        # Bind session to run
        manager._run_service.bind_session(run.run_id, state.session_id)
        logger.info(
            "[REST] registered session=%s task=%s persona=%s run=%s",
            state.session_id[:8],
            task_id,
            state.persona_id,
            run.run_id,
        )
        record_event(
            manager.bench_root,
            _audit_user_from_run(run),
            "session.register",
            request=request,
            run_id=run.run_id,
            session_id=state.session_id,
            task_id=task_id,
            success=True,
        )
        return JSONResponse(result)
    else:
        state.cleanup()
        logger.warning("[REST] register failed: %s", result.get("error"))
        error = result.get("error", "")
        code = 404 if "not found" in error.lower() else 400
        record_event(
            manager.bench_root,
            _audit_user_from_run(run),
            "session.register",
            request=request,
            run_id=run.run_id,
            task_id=task_id,
            success=False,
            payload={"error": error},
        )
        return JSONResponse(result, status_code=code)


_RETRYABLE_FAILURE = "infrastructure_failure"


def _classify_session_failure(termination_reason: str | None) -> str:
    """Map a session termination_reason to a retry-eligibility category.

    Categories (per #126 P1):
    - ``infrastructure_failure`` — retryable (NPC/user-sim crash, sandbox
      crash; surfaces today as ``user_sim_error:*``).
    - ``agent_gave_up`` — not retryable (``agent_stuck``, ``agent_abandoned``).
    - ``max_turns_reached`` — not retryable (``max_turns``, ``timeout``;
      session-level timeout is treated as exhaustion, not infra failure).
    - ``terminal_success`` — not retryable (``user_satisfied``).
    - ``unknown`` — not retryable; defensive default.
    """
    if not termination_reason:
        return "unknown"
    if termination_reason.startswith("user_sim_error:"):
        return _RETRYABLE_FAILURE
    if termination_reason in ("agent_stuck", "agent_abandoned"):
        return "agent_gave_up"
    if termination_reason == "user_satisfied":
        return "terminal_success"
    if termination_reason in ("max_turns", "timeout"):
        return "max_turns_reached"
    return "unknown"


async def rest_retry_session(request: Request) -> JSONResponse:
    """``POST /session/{sid}/retry`` — owner-scoped retry of a failed session.

    Only sessions whose ``termination_reason`` classifies as
    ``infrastructure_failure`` are retryable. The retry resets the
    underlying RunAssignment back to CLAIMED, allocates a fresh session
    under the same run, and returns the new session_id. The original
    bundle remains on disk under its session_id as the failure receipt.
    """
    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    try:
        state = manager.get_or_restore_session(sid)
    except Exception:
        return JSONResponse({"error": "Session not found"}, 404)
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    auth_err = _authorize_rest_session_request(request, manager, state)
    if auth_err is not None:
        return auth_err

    if state.phase != SessionPhase.COMPLETED:
        return JSONResponse(
            {
                "error": "Session is not in a terminal state",
                "current_phase": state.phase.value,
            },
            409,
        )

    if not state.run_id:
        return JSONResponse(
            {"error": "Session is not bound to a run"},
            409,
        )

    run_state = state.get_run_results()
    termination_reason = run_state.get("termination_reason")
    category = _classify_session_failure(termination_reason)
    if category != _RETRYABLE_FAILURE:
        return JSONResponse(
            {
                "error": "Session is not retryable",
                "category": category,
                "termination_reason": termination_reason,
            },
            409,
        )

    # Snapshot the COMPLETED run so the retry can be undone if the new
    # session fails to register — otherwise a transient sandbox failure
    # would leave the run in FAILED with no session_id pointer back to
    # the failure bundle, blocking any further retry attempt.
    pre_reset_run = manager._run_service.get_run(state.run_id)
    pre_reset_snapshot = (
        {
            "session_id": pre_reset_run.session_id,
            "result_dir": pre_reset_run.result_dir,
            "completed_at": pre_reset_run.completed_at,
            "eval_status": pre_reset_run.eval_status,
            "error": pre_reset_run.error,
        }
        if pre_reset_run is not None
        else None
    )

    try:
        run = manager._run_service.reset_for_retry(state.run_id)
    except (ValueError, KeyError) as exc:
        return JSONResponse({"error": str(exc)}, 409)

    new_state = manager.create_rest_session()
    new_state.run_id = run.run_id
    new_state._run_task_id = run.task_id
    new_state.owner_user_id = run.owner_user_id
    new_state.owner_github_login = run.owner_github_login
    new_state.owner_email = run.owner_email
    new_state.visibility = run.visibility
    new_state._on_completed = lambda result_dir: manager._run_service.mark_completed(
        run.run_id, result_dir
    )
    # Preserve the original persona so the retry exercises the same
    # user assignment; falling through to a random pick would change
    # the scenario and make scores incomparable across attempts.
    register_result = await asyncio.to_thread(
        new_state.register, run.task_id, state.persona_id or None
    )
    if "session_id" not in register_result:
        new_state.cleanup()
        # Re-attach the original COMPLETED failure receipt so the run
        # stays eligible for another retry.
        if pre_reset_snapshot is not None:
            try:
                manager._run_service.restore_after_failed_retry(
                    run.run_id, **pre_reset_snapshot
                )
            except Exception:
                logger.warning(
                    "Run %s restore_after_failed_retry failed",
                    run.run_id,
                    exc_info=True,
                )
        return JSONResponse(register_result, 500)

    manager.register_rest_session(new_state)
    manager._run_service.bind_session(run.run_id, new_state.session_id)

    logger.info(
        "[REST:%s] retry → new session=%s run=%s",
        sid[:8],
        new_state.session_id[:8],
        run.run_id,
    )
    record_event(
        manager.bench_root,
        _audit_user_from_state(state),
        "session.retry",
        request=request,
        run_id=run.run_id,
        session_id=new_state.session_id,
        task_id=run.task_id,
        success=True,
        payload={
            "previous_session_id": sid,
            "termination_reason": termination_reason,
            "category": category,
        },
    )
    return JSONResponse(
        {
            "status": "retry_started",
            "session_id": new_state.session_id,
            "previous_session_id": sid,
            "run_id": run.run_id,
            "category": category,
            "termination_reason": termination_reason,
            "next_action": f"POST /session/{new_state.session_id}/start",
        }
    )


async def rest_start(request: Request) -> JSONResponse:
    """``POST /session/{sid}/start``"""
    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    state = manager.get_session(sid)
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    auth_err = _authorize_rest_session_request(request, manager, state)
    if auth_err is not None:
        return auth_err

    allowed, error, ops = check_permission(state.phase, "start_session")
    if not allowed:
        logger.debug("[REST:%s] DENIED start in phase %s", sid[:8], state.phase.value)
        return JSONResponse(
            {"error": error, "allowed": ops, "current_phase": state.phase.value},
            403,
        )

    async with state._request_lock:
        state._last_activity = time.time()
        result = await asyncio.to_thread(state.start)
    logger.info("[REST:%s] session started", sid[:8])
    record_event(
        manager.bench_root,
        _audit_user_from_state(state),
        "session.start",
        request=request,
        run_id=state.run_id or "",
        session_id=sid,
        task_id=state.task_id,
    )
    return JSONResponse(result)


async def rest_tools(request: Request) -> JSONResponse:
    """``GET /session/{sid}/tools``"""
    manager: BenchSessionManager = request.app.state.manager
    state = manager.get_session(request.path_params["sid"])
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    auth_err = _authorize_rest_session_request(request, manager, state)
    if auth_err is not None:
        return auth_err

    tools = state.get_visible_tools()
    return JSONResponse(
        {
            "tools": [
                {
                    "name": t.name,
                    "description": t.description or "",
                    "inputSchema": t.inputSchema,
                }
                for t in tools
            ],
        }
    )


async def rest_tool_call(request: Request) -> JSONResponse:
    """``POST /session/{sid}/tool/{name}``"""
    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    state = manager.get_session(sid)
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    auth_err = _authorize_rest_session_request(request, manager, state)
    if auth_err is not None:
        return auth_err

    name = request.path_params["name"]

    if name in SERVER_ONLY_EVAL_TOOLS:
        logger.debug("[REST:%s] BLOCKED server-only tool/%s", sid[:8], name)
        return JSONResponse(
            {"error": "Evaluation is server-side and is not available via /tool."},
            403,
        )

    # Block session API tools — use dedicated endpoints.
    if name in TOOL_ENDPOINT_BLOCKED:
        logger.debug(
            "[REST:%s] BLOCKED tool/%s (use dedicated endpoint)", sid[:8], name
        )
        return JSONResponse(
            {"error": f"Use the dedicated /session/{{sid}}/{name} endpoint instead."},
            400,
        )

    allowed, error, ops = check_permission(state.phase, name)
    if not allowed:
        logger.debug(
            "[REST:%s] DENIED %s in phase %s", sid[:8], name, state.phase.value
        )
        return JSONResponse(
            {"error": error, "allowed": ops, "current_phase": state.phase.value},
            403,
        )

    try:
        body = await request.json()
    except Exception:
        body = {}

    logger.debug("[REST:%s] tool_call: %s", sid[:8], name)
    state._last_activity = time.time()

    if name in HEAVY_TOOLS:
        try:
            quota_reservation = manager._quota_manager.reserve_heavy_job(
                owner_user_id=state.owner_user_id,
                run_id=state.run_id or "",
                session_id=sid,
                tool_name=name,
            )
        except QuotaExceeded as exc:
            record_event(
                manager.bench_root,
                _audit_user_from_state(state),
                "tool.job_enqueued",
                request=request,
                run_id=state.run_id or "",
                session_id=sid,
                task_id=state.task_id,
                success=False,
                payload={"tool": name, "error": str(exc)},
            )
            return JSONResponse({"error": str(exc)}, status_code=429)

        # Async dispatch — return 202 immediately so the client is not
        # holding an HTTP connection open for the full backtest window.
        #
        # Reserve the session lock synchronously before returning so any
        # request (DELETE, send, another tool) arriving on a parallel
        # connection after the 202 waits behind the accepted backtest
        # instead of racing the background task's first timeslice.
        # Ownership transfers to ``_execute_tool_job``, which releases
        # it in a ``finally`` once the tool has run.
        await state._request_lock.acquire()
        try:
            job = manager._job_store.create(sid, name, body)
            task = asyncio.create_task(
                _execute_tool_job(
                    manager,
                    state,
                    job["job_id"],
                    name,
                    body,
                    quota_reservation=quota_reservation,
                )
            )
        except BaseException:
            manager._quota_manager.release_heavy_job(
                owner_user_id=state.owner_user_id,
                reservation_id=quota_reservation,
            )
            state._request_lock.release()
            raise
        manager._job_tasks[job["job_id"]] = task
        task.add_done_callback(
            lambda _t, jid=job["job_id"]: manager._job_tasks.pop(jid, None)
        )
        logger.info(
            "[REST:%s] enqueued job %s for %s",
            sid[:8],
            job["job_id"][:8],
            name,
        )
        record_event(
            manager.bench_root,
            _audit_user_from_state(state),
            "tool.job_enqueued",
            request=request,
            run_id=state.run_id or "",
            session_id=sid,
            task_id=state.task_id,
            payload={"tool": name, "job_id": job["job_id"]},
        )
        return JSONResponse(
            {
                "job_id": job["job_id"],
                "status": job["status"],
                "poll_url": f"/session/{sid}/tool/jobs/{job['job_id']}",
            },
            status_code=202,
        )

    async with state._request_lock:
        result = await asyncio.to_thread(state.call_domain_tool, name, **body)
    result_preview = str(result)[:150]
    logger.debug("[REST:%s] %s -> %s...", sid[:8], name, result_preview)
    try:
        parsed = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        parsed = {"success": True, "output": result}
    record_event(
        manager.bench_root,
        _audit_user_from_state(state),
        "tool.call",
        request=request,
        run_id=state.run_id or "",
        session_id=sid,
        task_id=state.task_id,
        success=bool(parsed.get("success", True)),
        payload={"tool": name},
    )
    return JSONResponse(parsed)


async def _execute_tool_job(
    manager: "BenchSessionManager",
    state: SessionState,
    job_id: str,
    name: str,
    body: dict,
    *,
    quota_reservation: str | None = None,
) -> None:
    """Background worker for heavy tool invocations.

    Inherits ownership of ``state._request_lock`` from the request
    handler that accepted the job (see rest_tool_call). The lock is
    released in the ``finally`` below, after the tool has run, so
    ordering with later same-session requests matches the synchronous
    pre-slice-2 path.
    """
    store = manager._job_store
    sid = state.session_id
    try:
        async with backtest_sem():
            store.update(job_id, status=JOB_STATUS_RUNNING, started_at=time.time())
            state._last_activity = time.time()
            result = await asyncio.to_thread(state.call_domain_tool, name, **body)
        try:
            parsed = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            parsed = {"success": True, "output": result}
        store.update(
            job_id,
            status=JOB_STATUS_COMPLETED,
            completed_at=time.time(),
            result=parsed,
        )
        logger.info("[REST:%s] job %s (%s) completed", sid[:8], job_id[:8], name)
        record_event(
            manager.bench_root,
            _audit_user_from_state(state),
            "tool.job_completed",
            run_id=state.run_id or "",
            session_id=sid,
            task_id=state.task_id,
            success=bool(parsed.get("success", True)),
            payload={"tool": name, "job_id": job_id},
        )
    except Exception as exc:
        logger.exception("[REST:%s] job %s failed", sid[:8], job_id[:8])
        store.update(
            job_id,
            status=JOB_STATUS_FAILED,
            completed_at=time.time(),
            error=f"{type(exc).__name__}: {exc}",
        )
        record_event(
            manager.bench_root,
            _audit_user_from_state(state),
            "tool.job_completed",
            run_id=state.run_id or "",
            session_id=sid,
            task_id=state.task_id,
            success=False,
            payload={"tool": name, "job_id": job_id, "error": str(exc)},
        )
    finally:
        manager._quota_manager.release_heavy_job(
            owner_user_id=state.owner_user_id,
            reservation_id=quota_reservation,
        )
        state._request_lock.release()


async def rest_tool_job_status(request: Request) -> JSONResponse:
    """``GET /session/{sid}/tool/jobs/{job_id}``

    Returns the current status of an async tool job. The job_id must
    belong to ``sid`` — a client cannot probe other sessions' jobs.

    Deliberately does not require the session to still be in memory:
    after a restart, sessions are gone but the JobStore still holds the
    job record (possibly marked ``failed`` by ``mark_orphans_failed``),
    and clients polling a stale job_id need to be able to see that
    terminal state instead of a generic 404.
    """
    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    job_id = request.path_params["job_id"]
    job = manager._job_store.get(job_id)
    if job is None or job.get("session_id") != sid:
        return JSONResponse({"error": "Job not found"}, 404)
    auth_err = _authorize_rest_job_request(request, manager, sid)
    if auth_err is not None:
        return auth_err
    # Trim the echoed arguments — they can be large (full source files).
    return JSONResponse(
        {
            "job_id": job["job_id"],
            "tool_name": job["tool_name"],
            "status": job["status"],
            "created_at": job["created_at"],
            "started_at": job["started_at"],
            "completed_at": job["completed_at"],
            "result": job["result"],
            "error": job["error"],
        }
    )


async def rest_send(request: Request) -> JSONResponse:
    """``POST /session/{sid}/send``"""
    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    state = manager.get_session(sid)
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    auth_err = _authorize_rest_session_request(request, manager, state)
    if auth_err is not None:
        return auth_err

    allowed, error, ops = check_permission(state.phase, "send_message")
    if not allowed:
        logger.debug("[REST:%s] DENIED send in phase %s", sid[:8], state.phase.value)
        return JSONResponse(
            {"error": error, "allowed": ops, "current_phase": state.phase.value},
            403,
        )

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body"}, 400)

    text = body.get("text", "")
    if not text or not text.strip():
        return JSONResponse(
            {"error": "Empty message. Provide text to send to the user."}, 400
        )

    attachments = body.get("attachments") or []
    if not isinstance(attachments, list):
        return JSONResponse(
            {"error": "attachments must be an array of file paths"}, 400
        )
    if len(attachments) > 3:
        return JSONResponse({"error": "Maximum 3 attachments allowed"}, 400)

    reasoning = body.get("reasoning")
    if reasoning is not None and not isinstance(reasoning, str):
        return JSONResponse({"error": "reasoning must be a string"}, 400)

    logger.info(
        "[REST:%s] send_message (turn %d, %d attachments, reasoning=%s): %s...",
        sid[:8],
        state.session.turn if state.session else 0,
        len(attachments),
        "yes" if reasoning else "no",
        text[:100],
    )
    async with state._request_lock:
        state._last_activity = time.time()
        result = await asyncio.to_thread(
            state.handle_send_message,
            text,
            attachments=attachments,
            reasoning=reasoning,
        )
    data = json.loads(result)
    logger.info(
        "[REST:%s] user reply (status=%s): %s...",
        sid[:8],
        data.get("status", "?"),
        data.get("user_message", "")[:100],
    )
    return JSONResponse(data)


async def ops_evaluate(request: Request) -> JSONResponse:
    """``POST /ops/session/{sid}/evaluate[?eval_mode=qr|qp|full]``

    Operator-only. Agents can finish runs and read scoped results, but scoring
    is triggered by the server side only.
    """
    from server.web.ui_app import _authorize_admin_token

    auth_err = _authorize_admin_token(request)
    if auth_err is not None:
        return auth_err

    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    try:
        state = manager.get_or_restore_session(sid)
    except (
        Exception
    ) as exc:  # noqa: BLE001 - persist hard preflight failure if possible
        return _save_archived_eval_restore_failure(request, manager, sid, exc)
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    if state.phase != SessionPhase.COMPLETED:
        logger.debug(
            "[REST:%s] DENIED evaluate in phase %s", sid[:8], state.phase.value
        )
        return JSONResponse(
            {
                "error": (
                    "Session not yet completed - operator evaluation is only "
                    "permitted on COMPLETED bundles."
                ),
                "current_phase": state.phase.value,
            },
            409,
        )

    from eval.contracts.request import EvalError, parse_eval_request

    try:
        eval_request = parse_eval_request(
            {
                "session_id": sid,
                "eval_mode": request.query_params.get("eval_mode", "full"),
                "eval_model": request.query_params.get("eval_model")
                or state.eval_model,
                "idempotency_key": (
                    request.query_params.get("idempotency_key")
                    or request.headers.get("Idempotency-Key")
                ),
            }
        )
    except EvalError as exc:
        return JSONResponse({"error": str(exc)}, 400)

    state._eval_mode = eval_request.eval_mode
    state.eval_model = eval_request.eval_model or state.eval_model
    state._eval_idempotency_key = eval_request.idempotency_key

    async with state._request_lock:
        state._last_activity = time.time()
        result = await asyncio.to_thread(state.request_evaluation)
    logger.info("[OPS:%s] evaluate: %s", sid[:8], result.get("status"))
    return JSONResponse(result)


def _save_archived_eval_restore_failure(
    request: Request,
    manager: BenchSessionManager,
    sid: str,
    exc: Exception,
) -> JSONResponse:
    result_dir = manager.find_archived_result_dir(sid)
    if result_dir is None:
        return JSONResponse({"error": "Session not found"}, 404)

    from eval.contracts.request import EvalError, parse_eval_request
    from eval.storage.score_store import allocate_score_run

    try:
        eval_request = parse_eval_request(
            {
                "session_id": sid,
                "eval_mode": request.query_params.get("eval_mode", "full"),
                "eval_model": request.query_params.get("eval_model")
                or manager.eval_model,
                "idempotency_key": (
                    request.query_params.get("idempotency_key")
                    or request.headers.get("Idempotency-Key")
                ),
            }
        )
    except EvalError as parse_exc:
        return JSONResponse({"error": str(parse_exc)}, 400)

    run, created = allocate_score_run(
        result_dir,
        eval_mode=eval_request.eval_mode,
        eval_model=eval_request.eval_model,
        idempotency_key=eval_request.idempotency_key,
    )
    if not created:
        return JSONResponse(
            {
                "status": "running",
                "score_id": run.score_id,
                "message": "Evaluation in progress.",
            }
        )

    from server.storage.eval_writer import save_terminal_eval_result

    message = f"run_state.json could not be loaded: {exc}"
    payload = save_terminal_eval_result(
        result_dir=result_dir,
        score_id=run.score_id,
        eval_mode=eval_request.eval_mode,
        eval_model=eval_request.eval_model,
        created_at=run.created_at,
        status="failed",
        error=message,
        preflight={
            "hard_errors": [
                {
                    "code": "run_state_invalid",
                    "message": message,
                }
            ],
            "track_blockers": {"qr": [], "qp": []},
            "skipped_dependencies": [],
        },
    )
    payload["session_id"] = sid
    return JSONResponse(payload)


_PUBLIC_RUN_RESULT_KEYS = {
    "session_id",
    "run_id",
    "task_id",
    "public_task_label",
    "persona_id",
    "session_status",
    "termination_reason",
    "timestamp",
    "duration_seconds",
    "conversation",
    "key_results",
    "trace_summary",
    "workspace_files",
}


def _public_run_results(data: dict) -> dict:
    """Return the client export scope for run results."""
    return {key: data.get(key) for key in _PUBLIC_RUN_RESULT_KEYS if key in data}


def _public_score_summary(summary: dict | None) -> dict | None:
    if not isinstance(summary, dict):
        return summary
    out = dict(summary)
    out.pop("eval_cost_usd", None)
    return out


def _public_score_entry(entry: dict) -> dict:
    allowed = {
        "score_id",
        "status",
        "score_status",
        "eval_mode",
        "created_at",
        "completed_at",
        "overall_score",
        "error",
    }
    out = {key: entry.get(key) for key in allowed if key in entry}
    if "scores" in entry:
        out["scores"] = _public_score_summary(entry.get("scores"))
    return out


def _v1_single_score_response(payload: dict, *, public: bool) -> dict:
    """Convert a single-score store payload into the #131 v1 response shape.

    ``public`` strips operator-only blobs (full ``score`` + ``cost``);
    ``public=False`` keeps them so ops dashboards still surface raw eval
    detail.

    Pending / running / failed envelopes still return the v1 fields with
    nulls so external clients only ever see the new shape — the contract
    is uniform regardless of where the eval is in its lifecycle.
    """
    from eval.storage.score_store import (
        SCORE_RESPONSE_SCHEMA_VERSION,
        build_v1_response,
    )

    score = payload.get("score") if isinstance(payload.get("score"), dict) else None
    cost = payload.get("cost") if isinstance(payload.get("cost"), dict) else None

    if score is None:
        body: dict = {
            "schema_version": SCORE_RESPONSE_SCHEMA_VERSION,
            "status": payload.get("status"),
            "score_id": payload.get("score_id"),
            "score_status": payload.get("score_status") or payload.get("status"),
            "task_score": None,
            "task_pass": None,
            "detail": {},
        }
        if "error" in payload:
            body["error"] = payload["error"]
        return body

    # Public path mirrors the pre-#131 behaviour of dropping cost entirely
    # from the response; ops keeps it under detail.cost + raw cost blob.
    body = build_v1_response(score, cost if not public else None)
    body["status"] = payload.get("status")

    if not public:
        body["score"] = score
        if cost is not None:
            body["cost"] = cost

    return body


def _is_single_score_envelope(payload: dict) -> bool:
    """Single-score envelopes carry a score_id (or score blob) but no scores list."""
    if isinstance(payload.get("score"), dict):
        return True
    if isinstance(payload.get("scores"), list):
        return False
    if payload.get("score_id"):
        return True
    return payload.get("status") == "pending" and "scores" not in payload


def _public_scores_payload(payload: dict) -> dict:
    """Strip private score internals from a score-store payload."""
    if _is_single_score_envelope(payload):
        return _v1_single_score_response(payload, public=True)

    out = {
        key: value
        for key, value in payload.items()
        if key not in {"score", "cost", "evaluations"}
    }
    if "scores" in payload:
        scores = payload.get("scores")
        if isinstance(scores, list):
            out["scores"] = [
                _public_score_entry(item) if isinstance(item, dict) else item
                for item in scores
            ]
            out["evaluations"] = out["scores"]
        elif isinstance(scores, dict):
            out["scores"] = _public_score_summary(scores)
        else:
            out["scores"] = scores
    return out


def _ops_scores_payload(payload: dict) -> dict:
    """Operator-side equivalent — keeps raw score/cost but applies the v1 shape."""
    if _is_single_score_envelope(payload):
        return _v1_single_score_response(payload, public=False)
    return payload


async def rest_results(request: Request) -> JSONResponse:
    """``GET /session/{sid}/results``"""
    manager: BenchSessionManager = request.app.state.manager
    state = manager.get_or_restore_session(request.path_params["sid"])
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)
    auth_err = _authorize_rest_session_request(request, manager, state)
    if auth_err is not None:
        return auth_err

    data = state.get_run_results()
    if "error" in data:
        return JSONResponse(data, 404)
    return JSONResponse(_public_run_results(data))


async def ops_results(request: Request) -> JSONResponse:
    """``GET /ops/session/{sid}/results`` — operator-only full run state."""
    from server.web.ui_app import _authorize_admin_token

    auth_err = _authorize_admin_token(request)
    if auth_err is not None:
        return auth_err

    manager: BenchSessionManager = request.app.state.manager
    state = manager.get_or_restore_session(request.path_params["sid"])
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    data = state.get_run_results()
    if "error" in data:
        return JSONResponse(data, 404)
    return JSONResponse(data)


async def rest_scores(request: Request) -> JSONResponse:
    """``GET /session/{sid}/scores[?history=true&score=score_2]``"""
    manager: BenchSessionManager = request.app.state.manager
    from eval.contracts.request import EvalError, parse_score_query

    try:
        query = parse_score_query(
            {
                "session_id": request.path_params["sid"],
                "history": request.query_params.get("history", "false").lower()
                == "true",
                "score": request.query_params.get("score"),
                "score_id": request.query_params.get("score_id"),
                "scores": request.query_params.get("scores"),
                "score_ids": request.query_params.get("score_ids"),
                "status": request.query_params.get("status", ""),
            }
        )
    except EvalError as exc:
        return JSONResponse({"error": str(exc)}, 400)

    try:
        state = manager.get_or_restore_session(request.path_params["sid"])
    except Exception:
        payload = manager.get_archived_scores(
            query.session_id,
            history=query.history,
            score_id=query.score_id,
            score_ids=query.score_ids,
            status_filter=query.status_filter,
        )
        if payload is not None:
            return JSONResponse(_public_scores_payload(payload))
        raise
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)
    auth_err = _authorize_rest_session_request(request, manager, state)
    if auth_err is not None:
        return auth_err

    payload = state.get_eval_scores(
        history=query.history,
        score_id=query.score_id,
        score_ids=query.score_ids,
        status_filter=query.status_filter,
    )
    return JSONResponse(_public_scores_payload(payload))


async def ops_scores(request: Request) -> JSONResponse:
    """``GET /ops/session/{sid}/scores[?history=true&score=score_2]``."""
    from server.web.ui_app import _authorize_admin_token

    auth_err = _authorize_admin_token(request)
    if auth_err is not None:
        return auth_err

    manager: BenchSessionManager = request.app.state.manager
    from eval.contracts.request import EvalError, parse_score_query

    try:
        query = parse_score_query(
            {
                "session_id": request.path_params["sid"],
                "history": request.query_params.get("history", "false").lower()
                == "true",
                "score": request.query_params.get("score"),
                "score_id": request.query_params.get("score_id"),
                "scores": request.query_params.get("scores"),
                "score_ids": request.query_params.get("score_ids"),
                "status": request.query_params.get("status", ""),
            }
        )
    except EvalError as exc:
        return JSONResponse({"error": str(exc)}, 400)

    try:
        state = manager.get_or_restore_session(request.path_params["sid"])
    except Exception:
        payload = manager.get_archived_scores(
            query.session_id,
            history=query.history,
            score_id=query.score_id,
            score_ids=query.score_ids,
            status_filter=query.status_filter,
        )
        if payload is not None:
            return JSONResponse(_ops_scores_payload(payload))
        raise
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    return JSONResponse(
        _ops_scores_payload(
            state.get_eval_scores(
                history=query.history,
                score_id=query.score_id,
                score_ids=query.score_ids,
                status_filter=query.status_filter,
            )
        )
    )


async def rest_session_status(request: Request) -> JSONResponse | Response:
    """``GET /session/{sid}`` or ``DELETE /session/{sid}``"""
    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    state = manager.get_session(sid)

    if request.method == "DELETE":
        if not state:
            return JSONResponse({"error": "Session not found"}, 404)
        auth_err = _authorize_rest_session_request(request, manager, state)
        if auth_err is not None:
            return auth_err
        logger.info("[REST:%s] DELETE — cancelling session", sid[:8])
        # persist_partial=True so an active session's conversation + tool
        # logs land as a bundle under results/server/... before the
        # container is destroyed. Without this the offline evaluator
        # (issue #47 batch driver) cannot see DELETEd sessions at all.
        await manager._cleanup_session(sid, persist_partial=True)
        return JSONResponse({"status": "cancelled"})

    # GET
    if not state:
        state = manager.get_or_restore_session(sid)
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)
    auth_err = _authorize_rest_session_request(request, manager, state)
    if auth_err is not None:
        return auth_err
    return JSONResponse(
        {
            "session_id": sid,
            "task_id": state.task_id,
            "phase": state.phase.value,
            "persona_id": state.persona_id,
        }
    )


async def rest_list(request: Request) -> JSONResponse:
    """``GET /session/list[?task_id=X01]``"""
    manager: BenchSessionManager = request.app.state.manager
    task_id = request.query_params.get("task_id", "")
    sessions = manager.list_sessions(task_id)
    if _rest_session_token_required():
        raw = extract_bearer_token(request)
        if not raw:
            return JSONResponse(
                {"error": "Authorization: Bearer <token> required"},
                status_code=401,
            )
        run = manager._run_service.resolve_token(raw, allow_expired_bound=True)
        if run is None:
            return JSONResponse({"error": "Invalid or expired token"}, status_code=401)
        sessions = [
            session
            for session in sessions
            if session.get("session_id") == run.session_id
        ]
    return JSONResponse({"sessions": sessions})


# ---------------------------------------------------------------------------
# ASGI app factory
# ---------------------------------------------------------------------------


class _ServerApp:
    """Combined ASGI app: MCP at ``/mcp``, REST at ``/session/*``."""

    def __init__(self, manager: BenchSessionManager, rest_app: Starlette):
        self._manager = manager
        self._rest_app = rest_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._rest_app(scope, receive, send)
        elif scope["type"] == "http" and scope.get("path", "") in ("/mcp", "/mcp/"):
            await self._manager.handle_mcp_request(scope, receive, send)
        else:
            await self._rest_app(scope, receive, send)


def create_app(
    use_docker: bool = True,
    bench_root: str | Path | None = None,
    eval_model: str = EVAL_DEFAULT_MODEL,
) -> _ServerApp:
    """Create the QuantTutorBench ASGI application."""
    load_server_env(bench_root)
    manager = BenchSessionManager(
        use_docker=use_docker,
        bench_root=bench_root,
        eval_model=eval_model,
    )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        async with manager.run():
            yield

    # Route registration order: exact paths before parameterized paths
    rest_routes = [
        Route("/session/register", rest_register, methods=["POST"]),
        Route("/session/list", rest_list, methods=["GET"]),
        Route("/session/{sid}", rest_session_status, methods=["GET", "DELETE"]),
        Route("/session/{sid}/start", rest_start, methods=["POST"]),
        Route("/session/{sid}/tools", rest_tools, methods=["GET"]),
        # More-specific /tool/jobs/{job_id} must precede /tool/{name}
        # or Starlette would bind "jobs" as the tool name.
        Route(
            "/session/{sid}/tool/jobs/{job_id}",
            rest_tool_job_status,
            methods=["GET"],
        ),
        Route("/session/{sid}/tool/{name}", rest_tool_call, methods=["POST"]),
        Route("/session/{sid}/send", rest_send, methods=["POST"]),
        Route("/session/{sid}/retry", rest_retry_session, methods=["POST"]),
        Route("/session/{sid}/results", rest_results, methods=["GET"]),
        Route("/session/{sid}/scores", rest_scores, methods=["GET"]),
        # Operator-only evaluation surface: not reachable from MCP or
        # client-facing /session tool dispatch.
        Route("/ops/session/{sid}/evaluate", ops_evaluate, methods=["POST"]),
        Route("/ops/session/{sid}/results", ops_results, methods=["GET"]),
        Route("/ops/session/{sid}/scores", ops_scores, methods=["GET"]),
    ]

    web_dir = Path(manager.bench_root) / "server" / "web"

    async def serve_index(request: Request) -> FileResponse:
        return FileResponse(
            str(web_dir / "templates" / "index.html"),
            headers={"Cache-Control": "no-cache"},
        )

    all_routes = [
        Route("/", serve_index, methods=["GET"]),
        Route("/review", serve_index, methods=["GET"]),
        Route("/review/{path:path}", serve_index, methods=["GET"]),
        Route("/health", rest_health, methods=["GET"]),
        *ui_routes(manager),
        *rest_routes,
        Mount(
            "/static",
            app=NoCacheStaticFiles(directory=str(web_dir / "static")),
            name="static",
        ),
    ]

    rest_app = Starlette(routes=all_routes, lifespan=lifespan)
    rest_app.state.manager = manager

    return _ServerApp(manager, rest_app)
