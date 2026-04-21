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
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from server.config.bootstrap import load_server_env
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
        eval_model: str = "anthropic/claude-haiku-4-5",
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
                            try:
                                if state.session is not None:
                                    state.session.force_complete("timeout")
                                state.phase = SessionPhase.COMPLETED
                                state._save_results()
                            except Exception:
                                logger.warning(
                                    "Session %s save failed in sweep",
                                    sid,
                                    exc_info=True,
                                )
                            state._destroy_container()
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
                await transport.handle_request(scope, receive, send)
                await self._cleanup_session(session_id, persist_partial=True)
                return
            resp = Response(status_code=404, content="Session not found")
            await resp(scope, receive, send)
            return

        # Existing session
        if session_id and session_id in self._transports:
            await self._transports[session_id].handle_request(scope, receive, send)
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
                            await self._cleanup_session(session_id)

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

            await transport.handle_request(scope, receive, send)
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
                        await self._cleanup_session(new_id)

        try:
            await self._task_group.start(run_server)
        except Exception as e:
            logger.error("Failed to start session %s: %s", new_id, e)
            await self._cleanup_session(new_id)
            resp = JSONResponse({"error": "Internal server error"}, status_code=500)
            await resp(scope, receive, send)
            return

        await transport.handle_request(scope, receive, send)

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
                return await state.handle_tool_call(name, arguments)

        return server

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
        state = self._sessions.get(session_id)
        if not state:
            self._transports.pop(session_id, None)
            logger.info("Session %s removed", session_id)
            return

        # Mark associated run as failed if session didn't complete normally
        if state.run_id and state.phase != SessionPhase.COMPLETED:
            try:
                run = self._run_service.get_run(state.run_id)
                if run and run.status.value not in ("completed", "failed", "cancelled"):
                    self._run_service.mark_failed(state.run_id, "Client disconnected")
                    logger.info(
                        "Run %s marked failed (client disconnected)",
                        state.run_id,
                    )
            except Exception as exc:
                logger.warning("Failed to mark run %s as failed: %s", state.run_id, exc)

        async with state._request_lock:
            self._sessions.pop(session_id, None)
            self._transports.pop(session_id, None)
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

        Session ID format:
        - ``{uuid}_{task_prefix}`` (new) — task prefix (e.g. D01) enables
          O(1) directory lookup instead of scanning all tasks.
        - ``{uuid}`` (legacy) — falls back to full scan.

        Supports both old layout  ``{task_id}/{session_id}/``
        and new layout ``{task_id}/{persona_id}/{ts}_{session_id[:8]}/``.
        """
        import re

        results_root = self.bench_root / "results" / "server"
        if not results_root.is_dir():
            return None

        # Parse task hint from session_id suffix (e.g. "…_D01" → "D01")
        task_hint = None
        m = re.search(r"_([A-Z]\d{2})$", session_id)
        if m:
            task_hint = m.group(1)

        short_id = session_id[:8]

        # Select which task directories to search
        if task_hint:
            # O(1): only scan task dirs matching the hint
            task_dirs = [
                d
                for d in results_root.iterdir()
                if d.is_dir() and d.name.startswith(f"{task_hint}_")
            ]
        else:
            # Legacy: scan all task dirs
            task_dirs = [d for d in results_root.iterdir() if d.is_dir()]

        for task_dir in task_dirs:
            # Old layout: {task_id}/{session_id}/
            candidate = task_dir / session_id
            if candidate.is_dir():
                return candidate
            # New layout: {task_id}/{persona_id}/{ts}_{session_id[:8]}/
            for persona_dir in task_dir.iterdir():
                if not persona_dir.is_dir():
                    continue
                for run_dir in persona_dir.iterdir():
                    if run_dir.is_dir() and run_dir.name.endswith(f"_{short_id}"):
                        rs = run_dir / "run_state.json"
                        if rs.exists():
                            return run_dir
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
        self, session_id: str, *, history: bool = False
    ) -> dict | None:
        result_dir = self.find_archived_result_dir(session_id)
        if not result_dir:
            return None

        state = self.get_archived_results(session_id) or {}
        task_id = state.get("task_id", "")
        persona_id = state.get("persona_id", "")
        bundle_session_id = state.get("session_id", session_id)

        from server.evaluator.paths import (
            find_latest_eval_dir,
            list_eval_history,
        )

        if history:
            entries = []
            for sub in list_eval_history(
                bench_root=self.bench_root,
                task_id=task_id,
                persona_id=persona_id,
                session_id=bundle_session_id,
            ):
                meta_path = sub / "eval_meta.json"
                if not meta_path.exists():
                    continue
                try:
                    meta = json.loads(meta_path.read_text(encoding="utf-8"))
                    meta["eval_dir"] = sub.name
                    entries.append(meta)
                except Exception:
                    continue
            return {"session_id": session_id, "evaluations": entries}

        latest_dir = find_latest_eval_dir(
            bench_root=self.bench_root,
            task_id=task_id,
            persona_id=persona_id,
            session_id=bundle_session_id,
        )
        if latest_dir is not None:
            latest_meta = latest_dir / "eval_meta.json"
            if latest_meta.exists():
                try:
                    meta = json.loads(latest_meta.read_text(encoding="utf-8"))
                    scores: dict = {
                        "quant_result": meta.get("quant_result", 0.0),
                        "quant_process": meta.get("quant_process", 0.0),
                        "tutor_scores": meta.get("tutor_scores", {}),
                        "overall": meta.get("overall_score", 0.0),
                    }
                    meta_errors = meta.get("errors")
                    if meta_errors:
                        scores["errors"] = meta_errors
                    return {"status": "completed", "scores": scores}
                except Exception:
                    pass

        if not state:
            return None
        return {"status": state.get("evaluation_status", "pending")}

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
        proc = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=3
        )
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
        usage = shutil.disk_usage("/home")
        free_gb = usage.free / (1024**3)
        return {
            "ok": free_gb >= _HEALTH_DISK_MIN_GB,
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
        return JSONResponse(result)
    else:
        state.cleanup()
        logger.warning("[REST] register failed: %s", result.get("error"))
        error = result.get("error", "")
        code = 404 if "not found" in error.lower() else 400
        return JSONResponse(result, status_code=code)


async def rest_start(request: Request) -> JSONResponse:
    """``POST /session/{sid}/start``"""
    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    state = manager.get_session(sid)
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

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
    return JSONResponse(result)


async def rest_tools(request: Request) -> JSONResponse:
    """``GET /session/{sid}/tools``"""
    manager: BenchSessionManager = request.app.state.manager
    state = manager.get_session(request.path_params["sid"])
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

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

    name = request.path_params["name"]

    # Block session API tools — use dedicated endpoints
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
                _execute_tool_job(manager, state, job["job_id"], name, body)
            )
        except BaseException:
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
    return JSONResponse(parsed)


async def _execute_tool_job(
    manager: "BenchSessionManager",
    state: SessionState,
    job_id: str,
    name: str,
    body: dict,
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
            store.update(
                job_id, status=JOB_STATUS_RUNNING, started_at=time.time()
            )
            state._last_activity = time.time()
            result = await asyncio.to_thread(
                state.call_domain_tool, name, **body
            )
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
        logger.info(
            "[REST:%s] job %s (%s) completed", sid[:8], job_id[:8], name
        )
    except Exception as exc:
        logger.exception("[REST:%s] job %s failed", sid[:8], job_id[:8])
        store.update(
            job_id,
            status=JOB_STATUS_FAILED,
            completed_at=time.time(),
            error=f"{type(exc).__name__}: {exc}",
        )
    finally:
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
            {"error": "Empty message. Provide text to send to the student."}, 400
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
        "[REST:%s] student reply (status=%s): %s...",
        sid[:8],
        data.get("status", "?"),
        data.get("student_message", "")[:100],
    )
    return JSONResponse(data)


async def ops_evaluate(request: Request) -> JSONResponse:
    """``POST /ops/session/{sid}/evaluate[?force=true&eval_mode=tutor_only&tutor_dims=D3,D4]``

    Operator-only. Issue #46 slice 3 cut this off the agent surface — agents
    finish at COMPLETED and never trigger their own scoring.
    """
    from server.web.ui_app import _authorize_admin_token

    auth_err = _authorize_admin_token(request)
    if auth_err is not None:
        return auth_err

    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    state = manager.get_or_restore_session(sid)
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    if state.phase != SessionPhase.COMPLETED:
        return JSONResponse(
            {
                "error": (
                    "Session not yet completed — operator evaluation is only "
                    "permitted on COMPLETED bundles."
                ),
                "current_phase": state.phase.value,
            },
            409,
        )

    eval_mode = request.query_params.get("eval_mode", "full")
    tutor_dims_raw = request.query_params.get("tutor_dims", "")
    tutor_dims = (
        [d.strip() for d in tutor_dims_raw.split(",") if d.strip()]
        if tutor_dims_raw
        else None
    )

    # ``?force=true`` is a no-op now that scoring is synchronous — every
    # POST scores a fresh ``eval_run_id`` under the sibling tree. Accept
    # the param for compat with existing operator scripts (#46 slice 4).

    if not state._result_dir:
        return JSONResponse(
            {"error": "No bundle on disk for this session — cannot score."},
            409,
        )

    from server.evaluator import score_bundle
    from server.storage.eval_writer import _collect_eval_errors

    async with state._request_lock:
        state._last_activity = time.time()
        try:
            eval_results = await asyncio.to_thread(
                score_bundle,
                bundle_dir=state._result_dir,
                task=state.task,
                persona=state.persona,
                bench_root=str(state.bench_root),
                eval_model=state.eval_model,
                eval_mode=eval_mode,
                tutor_dims=tutor_dims,
            )
        except Exception as exc:
            logger.error("[OPS:%s] evaluate failed: %s", sid[:8], exc, exc_info=True)
            return JSONResponse(
                {"status": "failed", "error": str(exc)}, 500
            )

    scores: dict = {
        "quant_result": eval_results.get("quant_result", 0.0),
        "quant_process": eval_results.get("quant_process", 0.0),
        "tutor_scores": eval_results.get("tutor_scores", {}),
        "overall": eval_results.get("_overall_score", 0.0),
    }
    errors = _collect_eval_errors(eval_results)
    if errors:
        scores["errors"] = errors
    # ``_eval_run_dir`` is a Path — stringify so JSON encoding succeeds.
    eval_run_dir = eval_results.get("_eval_run_dir")
    logger.info(
        "[OPS:%s] evaluate complete: %s", sid[:8], eval_results.get("_eval_run_id")
    )
    return JSONResponse(
        {
            "status": "completed",
            "scores": scores,
            "eval_run_id": eval_results.get("_eval_run_id"),
            "eval_run_dir": str(eval_run_dir) if eval_run_dir else None,
        }
    )


async def ops_results(request: Request) -> JSONResponse:
    """``GET /ops/session/{sid}/results`` — operator-only."""
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


async def ops_scores(request: Request) -> JSONResponse:
    """``GET /ops/session/{sid}/scores[?history=true]`` — operator-only."""
    from server.web.ui_app import _authorize_admin_token

    auth_err = _authorize_admin_token(request)
    if auth_err is not None:
        return auth_err

    manager: BenchSessionManager = request.app.state.manager
    history = request.query_params.get("history", "false").lower() == "true"
    state = manager.get_or_restore_session(request.path_params["sid"])
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)

    return JSONResponse(state.get_eval_scores(history=history))


async def rest_session_status(request: Request) -> JSONResponse | Response:
    """``GET /session/{sid}`` or ``DELETE /session/{sid}``"""
    manager: BenchSessionManager = request.app.state.manager
    sid = request.path_params["sid"]
    state = manager.get_session(sid)

    if request.method == "DELETE":
        if not state:
            return JSONResponse({"error": "Session not found"}, 404)
        logger.info("[REST:%s] DELETE — cancelling session", sid[:8])
        await manager._cleanup_session(sid)
        return JSONResponse({"status": "cancelled"})

    # GET
    if not state:
        state = manager.get_or_restore_session(sid)
    if not state:
        return JSONResponse({"error": "Session not found"}, 404)
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
    return JSONResponse({"sessions": manager.list_sessions(task_id)})


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
    eval_model: str = "anthropic/claude-haiku-4-5",
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
        # Operator-only evaluation surface (issue #46 slice 3 — no longer
        # reachable from the agent's MCP/REST catalogue).
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
