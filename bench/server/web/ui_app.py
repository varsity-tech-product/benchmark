"""Starlette route factory for the web UI.

Provides:
- ``/ui/results/*``  — read-only result browsing (existing)
- ``/ui/tasks/*``    — task listing + public catalog (existing + new)
- ``/ui/runs/*``     — Run management (new)
- ``/client/runs/*`` — Client-facing Run endpoints (new)
"""

from __future__ import annotations

import hmac
import html
import json
import logging
import math
import os
from dataclasses import asdict
from pathlib import Path

from server.audit import record_event
from server.auth import AuthService
from server.quota import QuotaExceeded
from starlette.requests import Request
from starlette.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.routing import Route

from .review_store import ReviewStore
from .ui_indexer import ResultIndexer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token utilities (shared by /mcp and /session/register in http_app.py)
# ---------------------------------------------------------------------------


def extract_bearer_token(request: Request) -> str | None:
    """Extract token from ``Authorization: Bearer <token>`` header."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token if token else None
    return None


def resolve_run_from_token(run_service, raw_token: str):
    """Find RunAssignment by raw token. Returns None if invalid/expired."""
    return run_service.resolve_token(raw_token)


def _authorize_control_token(
    request: Request, run_service, run_id: str
):
    """Return the RunAssignment if the request carries a valid control token.

    Returns a ``(JSONResponse, None)`` tuple for the error path and
    ``(None, assignment)`` for the success path so callers can short-circuit
    with a single check.
    """
    raw = extract_bearer_token(request)
    if not raw:
        return JSONResponse(
            {"error": "Authorization: Bearer <control_token> required"}, 401
        ), None
    try:
        assignment = run_service.verify_control_token(run_id, raw)
    except PermissionError:
        return JSONResponse({"error": "Invalid control token"}, 401), None
    except ValueError:
        return JSONResponse({"error": "Run not found"}, 404), None
    return None, assignment


def _authorize_admin_token(request: Request):
    """Compare the bearer token against ``QTB_ADMIN_TOKEN``.

    If the env var is unset we allow the request (local-dev convenience);
    this lets existing test/CI flows keep working without setting the var.
    """
    expected = os.environ.get("QTB_ADMIN_TOKEN", "")
    if not expected:
        return None  # no enforcement
    raw = extract_bearer_token(request) or ""
    if not raw or not hmac.compare_digest(expected, raw):
        return JSONResponse(
            {"error": "Authorization: Bearer <admin_token> required"}, 401
        )
    return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_for_json(obj):
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    if isinstance(obj, dict):
        return {key: _sanitize_for_json(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_for_json(item) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# Route factory
# ---------------------------------------------------------------------------


def ui_routes(manager) -> list[Route]:
    """Return all ``/ui/*`` and ``/client/*`` routes.

    ``manager`` is a ``BenchSessionManager`` instance that holds
    ``_run_service`` (RunService) after Phase 1 integration.
    """

    indexer = ResultIndexer(manager.bench_root)
    review_store = ReviewStore(manager.bench_root, indexer)
    auth = AuthService(manager.bench_root)

    def _scope_flags(request: Request, user) -> tuple[bool, bool]:
        scope = request.query_params.get("scope", "").strip().lower()
        include_all = bool(getattr(user, "is_admin", False)) and scope in ("", "all")
        include_org = scope == "org"
        return include_all, include_org

    def _run_access_from_cookie(run_service, run_id: str, user):
        if user is None:
            return None
        try:
            return run_service.assert_run_owner_or_admin(run_id, user)
        except PermissionError:
            return None
        except ValueError:
            raise

    async def auth_login(request: Request):
        return await auth.login(request)

    async def auth_callback(request: Request):
        try:
            return await auth.callback(request)
        except PermissionError as exc:
            return JSONResponse({"error": str(exc)}, status_code=401)
        except Exception as exc:
            logger.warning("OAuth callback failed: %s", exc, exc_info=True)
            return JSONResponse({"error": "OAuth callback failed"}, status_code=502)

    async def auth_logout(request: Request):
        return await auth.logout(request)

    async def ui_me(request: Request) -> JSONResponse:
        return JSONResponse(auth.me_payload(request))

    async def rest_agent_skill_page(request: Request) -> HTMLResponse:
        relative = Path("docs/skills/quanttutorbench-rest-agent/SKILL.md")
        candidates = [
            manager.bench_root / relative,
            manager.bench_root.parent / relative,
        ]
        skill_path = next((path for path in candidates if path.exists()), candidates[0])
        try:
            markdown = skill_path.read_text(encoding="utf-8")
        except OSError:
            return HTMLResponse("Skill page not found.", status_code=404)

        body = html.escape(markdown)
        return HTMLResponse(
            "<!doctype html>"
            '<html lang="en">'
            "<head>"
            '<meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            "<title>QuantTutorBench REST Agent Skill</title>"
            "<style>"
            "body{font-family:Inter,system-ui,-apple-system,sans-serif;margin:0;background:#f8f7f4;color:#20201f;}"
            "main{max-width:960px;margin:0 auto;padding:40px 24px 72px;}"
            "a{color:#006d77;} pre{white-space:pre-wrap;background:#fff;border:1px solid #ddd8cf;border-radius:8px;padding:24px;line-height:1.55;overflow:auto;}"
            ".back{display:inline-block;margin-bottom:24px;text-decoration:none;font-weight:600;}"
            "</style>"
            "</head>"
            "<body><main>"
            '<a class="back" href="/">QuantTutorBench</a>'
            "<h1>REST Agent Skill</h1>"
            "<p>Use this first-party skill page when connecting an external agent to the benchmark service.</p>"
            f"<pre>{body}</pre>"
            "</main></body></html>"
        )

    async def get_api_key(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        return JSONResponse(auth.store.get_api_key_status(user))

    async def rotate_api_key(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        raw_key, record = auth.store.rotate_api_key(user)
        record_event(
            manager.bench_root,
            user,
            "api_key.rotate",
            request=request,
            payload={"key_hint": record.get("key_hint", "")},
        )
        payload = dict(record)
        payload["has_key"] = True
        payload["api_key"] = raw_key
        return JSONResponse(payload)

    async def revoke_api_key(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        payload = auth.store.revoke_api_key(user)
        record_event(
            manager.bench_root,
            user,
            "api_key.revoke",
            request=request,
            payload={"revoked": payload.get("revoked", False)},
        )
        return JSONResponse(payload)

    # -----------------------------------------------------------------------
    # Existing: /ui/tasks, /ui/results/*
    # -----------------------------------------------------------------------

    async def list_tasks(request: Request) -> JSONResponse:
        _, err = auth.require_user(request)
        if err is not None:
            return err
        return JSONResponse(_sanitize_for_json(indexer.list_tasks()))

    async def list_results(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        include_all, include_org = _scope_flags(request, user)
        return JSONResponse(
            _sanitize_for_json(
                {
                    "results": indexer.list_results(
                        category=request.query_params.get("category"),
                        task_id=request.query_params.get("task_id"),
                        eval_status=request.query_params.get("eval_status"),
                        owner_user_id=None if include_all else user.user_id,
                        user=user,
                        include_all=include_all,
                        include_org=include_org,
                    ),
                }
            )
        )

    async def get_detail(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        include_all, include_org = _scope_flags(request, user)
        try:
            detail = indexer.get_detail(
                request.path_params["session_id"],
                user=user,
                include_all=include_all,
                include_org=include_org,
            )
        except PermissionError:
            return JSONResponse({"error": "Result access denied"}, status_code=403)
        if detail is None:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        record_event(
            manager.bench_root,
            user,
            "result.view",
            request=request,
            run_id=str(detail.get("run_id") or ""),
            session_id=str(detail.get("session_id") or ""),
            task_id=str(detail.get("task_id") or ""),
        )
        return JSONResponse(_sanitize_for_json(detail))

    async def get_workspace(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        include_all, include_org = _scope_flags(request, user)
        try:
            payload = indexer.get_workspace_index(
                request.path_params["session_id"],
                user=user,
                include_all=include_all,
                include_org=include_org,
            )
        except PermissionError:
            return JSONResponse({"error": "Result access denied"}, status_code=403)
        if payload is None:
            return JSONResponse({"error": "Session not found"}, status_code=404)
        return JSONResponse(_sanitize_for_json(payload))

    async def export_run_state(request: Request) -> JSONResponse | FileResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        include_all, include_org = _scope_flags(request, user)
        session_id = request.path_params["session_id"]
        try:
            full_path = indexer.resolve_run_state_file(
                session_id,
                user=user,
                include_all=include_all,
                include_org=include_org,
            )
        except PermissionError:
            return JSONResponse({"error": "Result access denied"}, status_code=403)

        if full_path is None:
            return JSONResponse({"error": "Session not found"}, status_code=404)

        safe_session_id = "".join(
            ch if ch.isalnum() or ch in ("-", "_") else "_" for ch in session_id
        )
        record_event(
            manager.bench_root,
            user,
            "result.export",
            request=request,
            session_id=session_id,
            payload={"file": "run_state.json"},
        )
        return FileResponse(
            str(full_path),
            media_type="application/json",
            filename=f"{safe_session_id}_run_state.json",
        )

    async def get_workspace_preview(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        include_all, include_org = _scope_flags(request, user)
        try:
            payload = indexer.get_workspace_preview(
                request.path_params["session_id"],
                request.path_params["path"],
                user=user,
                include_all=include_all,
                include_org=include_org,
            )
        except ValueError:
            return JSONResponse({"error": "Invalid path"}, status_code=400)
        except PermissionError:
            return JSONResponse({"error": "Result access denied"}, status_code=403)

        if payload is None:
            return JSONResponse({"error": "Not found"}, status_code=404)
        return JSONResponse(_sanitize_for_json(payload))

    async def get_file(request: Request) -> JSONResponse | FileResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        include_all, include_org = _scope_flags(request, user)
        try:
            full_path = indexer.resolve_agent_file(
                request.path_params["session_id"],
                request.path_params["path"],
                user=user,
                include_all=include_all,
                include_org=include_org,
            )
        except ValueError:
            return JSONResponse({"error": "Invalid path"}, status_code=400)
        except PermissionError:
            return JSONResponse({"error": "Result access denied"}, status_code=403)

        if full_path is None:
            return JSONResponse({"error": "Not found"}, status_code=404)

        record_event(
            manager.bench_root,
            user,
            "result.file_read",
            request=request,
            session_id=request.path_params["session_id"],
            payload={"path": request.path_params["path"]},
        )
        return FileResponse(str(full_path))

    # -----------------------------------------------------------------------
    # New: /ui/tasks/catalog
    # -----------------------------------------------------------------------

    async def task_catalog(request: Request) -> JSONResponse:
        """Public task catalog — labels + category + difficulty.

        Used by Results UI. Do not wire this into the Run/exam UI.
        """
        _, err = auth.require_user(request)
        if err is not None:
            return err
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)
        return JSONResponse({"tasks": run_service.catalog.list_public()})

    async def task_catalog_labels(request: Request) -> JSONResponse:
        """Run/exam-mode catalog — labels only, no category or difficulty."""
        _, err = auth.require_user(request)
        if err is not None:
            return err
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)
        return JSONResponse({"tasks": run_service.catalog.list_labels_only()})

    # -----------------------------------------------------------------------
    # New: /ui/review/*
    # -----------------------------------------------------------------------

    async def list_review_bundles(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        include_all, include_org = _scope_flags(request, user)
        return JSONResponse(
            _sanitize_for_json(
                {
                    "bundles": review_store.list_bundles(
                        user,
                        include_all=include_all,
                        include_org=include_org,
                    )
                }
            )
        )

    async def get_review_bundle(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        bundle_id = request.path_params["bundle_id"]
        include_all, include_org = _scope_flags(request, user)
        try:
            payload = review_store.get_bundle(
                bundle_id,
                user,
                include_all=include_all,
                include_org=include_org,
            )
        except PermissionError:
            return JSONResponse({"error": "Bundle access denied"}, status_code=403)
        if payload is None:
            return JSONResponse({"error": "Bundle not found"}, status_code=404)
        record_event(
            manager.bench_root,
            user,
            "review.bundle_view",
            request=request,
            session_id=bundle_id,
        )
        return JSONResponse(_sanitize_for_json(payload))

    async def append_review_opinion(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        bundle_id = request.path_params["bundle_id"]
        include_all, include_org = _scope_flags(request, user)
        try:
            existing = review_store.get_bundle(
                bundle_id,
                user,
                include_all=include_all,
                include_org=include_org,
            )
        except PermissionError:
            return JSONResponse({"error": "Bundle access denied"}, status_code=403)
        if existing is None:
            return JSONResponse({"error": "Bundle not found"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        raw_card = body.get("opinion") if isinstance(body, dict) else body
        if not isinstance(raw_card, dict):
            return JSONResponse({"error": "Opinion card is required"}, status_code=400)
        try:
            review = review_store.append_opinion(
                bundle_id,
                user,
                raw_card,
                include_all=include_all,
                include_org=include_org,
            )
        except PermissionError:
            return JSONResponse({"error": "Bundle access denied"}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        payload = review_store.get_bundle(
            bundle_id,
            user,
            include_all=include_all,
            include_org=include_org,
        )
        record_event(
            manager.bench_root,
            user,
            "review.opinion_create",
            request=request,
            session_id=bundle_id,
            payload={"section": raw_card.get("section")},
        )
        if payload is None:
            return JSONResponse(_sanitize_for_json({"review": review}))
        return JSONResponse(_sanitize_for_json(payload))

    async def replace_review_opinions(request: Request) -> JSONResponse:
        user, err = auth.require_user(request)
        if err is not None:
            return err
        bundle_id = request.path_params["bundle_id"]
        include_all, include_org = _scope_flags(request, user)
        try:
            existing = review_store.get_bundle(
                bundle_id,
                user,
                include_all=include_all,
                include_org=include_org,
            )
        except PermissionError:
            return JSONResponse({"error": "Bundle access denied"}, status_code=403)
        if existing is None:
            return JSONResponse({"error": "Bundle not found"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, status_code=400)
        opinions = body.get("opinions") if isinstance(body, dict) else body
        if not isinstance(opinions, list):
            return JSONResponse({"error": "opinions must be a list"}, status_code=400)
        try:
            review_store.replace_opinions(
                bundle_id,
                user,
                opinions,
                include_all=include_all,
                include_org=include_org,
            )
        except PermissionError:
            return JSONResponse({"error": "Bundle access denied"}, status_code=403)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        payload = review_store.get_bundle(
            bundle_id,
            user,
            include_all=include_all,
            include_org=include_org,
        )
        return JSONResponse(_sanitize_for_json(payload or {"bundle_id": bundle_id}))

    # -----------------------------------------------------------------------
    # New: /ui/runs/*
    # -----------------------------------------------------------------------

    async def create_run(request: Request) -> JSONResponse:
        """``POST /ui/runs`` — create a new run assignment."""
        user, err = auth.require_user(request)
        if err is not None:
            return err
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, 400)

        task = body.get("task", "")
        if not task:
            return JSONResponse({"error": "Missing required field: task"}, 400)

        mode = body.get("mode", "agent")
        persona_policy = body.get("persona_policy", "auto")
        token_ttl = body.get("token_ttl_minutes", 30)
        visibility = body.get("visibility", "private")

        try:
            assignment, raw_token, raw_control_token = run_service.create_run(
                task=task,
                mode=mode,
                persona_policy=persona_policy,
                token_ttl_minutes=token_ttl,
                owner_user_id=user.user_id,
                owner_github_login=user.github_login,
                owner_email=user.email,
                visibility=visibility,
            )
        except QuotaExceeded as exc:
            record_event(
                manager.bench_root,
                user,
                "run.create",
                request=request,
                success=False,
                payload={"error": str(exc), "task": task},
            )
            return JSONResponse({"error": str(exc)}, 429)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

        # Build base URL from request
        base_url = str(request.base_url).rstrip("/")
        mcp_url = f"{base_url}/mcp"

        record_event(
            manager.bench_root,
            user,
            "run.create",
            request=request,
            run_id=assignment.run_id,
            task_id=assignment.task_id,
            payload={"visibility": assignment.visibility},
        )
        return JSONResponse(
            {
                "run_id": assignment.run_id,
                "status": assignment.status.value,
                "public_task_label": assignment.public_task_label,
                "token": raw_token,
                "control_token": raw_control_token,
                "token_expires_at": assignment.token_expires_at,
                "mcp_url": mcp_url,
                "launch_command": (
                    f"python -m client attach "
                    f"--server {base_url} "
                    f"--run-token {raw_token}"
                ),
            }
        )

    async def list_runs(request: Request) -> JSONResponse:
        """``GET /ui/runs`` — list owner-visible runs with optional filters."""
        user, err = auth.require_user(request)
        if err is not None:
            return err
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)

        status = request.query_params.get("status")
        task = request.query_params.get("task")
        include_all, include_org = _scope_flags(request, user)
        runs = run_service.list_runs(
            status=status,
            task=task,
            owner_user_id=user.user_id,
            include_all=include_all,
            include_org=include_org,
        )
        return JSONResponse({"runs": [r.public_dict() for r in runs]})

    async def get_run(request: Request) -> JSONResponse:
        """``GET /ui/runs/{run_id}`` — query run status. Owner-only."""
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)

        run_id = request.path_params["run_id"]
        user = auth.get_current_user(request) if auth.enabled else None
        try:
            run = _run_access_from_cookie(run_service, run_id, user)
        except ValueError:
            return JSONResponse({"error": "Run not found"}, 404)
        if run is not None:
            return JSONResponse(run.public_dict())

        err, run = _authorize_control_token(request, run_service, run_id)
        if err is not None:
            if user is not None:
                return JSONResponse({"error": "Run access denied"}, status_code=403)
            return err
        return JSONResponse(run.public_dict())

    async def get_run_live(request: Request) -> JSONResponse:
        """``GET /ui/runs/{run_id}/live`` — real-time conversation + tool logs."""
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)

        run_id = request.path_params["run_id"]
        user = auth.get_current_user(request) if auth.enabled else None
        try:
            run = _run_access_from_cookie(run_service, run_id, user)
        except ValueError:
            return JSONResponse({"error": "Run not found"}, 404)
        if run is None:
            err, run = _authorize_control_token(request, run_service, run_id)
            if err is not None:
                if user is not None:
                    return JSONResponse({"error": "Run access denied"}, status_code=403)
                return err

        # Terminal or pre-active states
        if run.status.value in (
            "completed",
            "failed",
            "cancelled",
            "waiting",
            "claimed",
        ):
            return JSONResponse(
                {
                    "run_status": run.status.value,
                    "session_id": run.session_id,
                    "session_phase": None,
                }
            )

        # Active — read from in-memory session
        if not run.session_id:
            return JSONResponse(
                {
                    "run_status": run.status.value,
                    "session_id": run.session_id,
                    "session_phase": None,
                }
            )

        session = manager.get_session(run.session_id)
        if not session:
            return JSONResponse(
                {
                    "run_status": run.status.value,
                    "session_id": run.session_id,
                    "session_phase": None,
                }
            )

        conversation = []
        turn = 0
        if session.session:
            conversation = getattr(session.session, "conversation", [])
            turn = getattr(session.session, "turn", 0)

        recent_logs = []
        if session.proxy:
            logs = session.proxy.get_logs()
            for log in logs[-20:]:
                if isinstance(log, dict):
                    recent_logs.append(log)
                else:
                    recent_logs.append(asdict(log))

        return JSONResponse(
            _sanitize_for_json(
                {
                    "run_status": run.status.value,
                    "session_id": run.session_id,
                    "session_phase": session.phase.value,
                    "turn": turn,
                    "conversation": conversation,
                    "recent_tool_logs": recent_logs,
                }
            )
        )

    def _live_snapshot_for_run(run) -> dict:
        """Build a read-only live snapshot for the passive Flow monitor."""
        payload = run.public_dict()
        payload.update(
            {
                "observer_status": run.status.value,
                "is_live": run.status.value in ("waiting", "claimed"),
                "session_phase": None,
                "turn": None,
                "conversation": [],
                "recent_tool_logs": [],
            }
        )

        if not run.session_id:
            return payload

        session = manager.get_session(run.session_id)
        if not session:
            if run.status.value == "active":
                payload["observer_status"] = "stale"
                payload["is_live"] = False
            return payload

        payload["is_live"] = run.status.value == "active"
        if session.session:
            payload["conversation"] = getattr(session.session, "conversation", [])[-50:]
            payload["turn"] = getattr(session.session, "turn", 0)
        payload["session_phase"] = session.phase.value

        if session.proxy:
            recent_logs = []
            for log in session.proxy.get_logs()[-20:]:
                if isinstance(log, dict):
                    recent_logs.append(log)
                else:
                    recent_logs.append(asdict(log))
            payload["recent_tool_logs"] = recent_logs

        return payload

    async def observe_runs_live(request: Request) -> JSONResponse:
        """``GET /ui/runs/live`` — passive read-only monitor snapshot."""
        user, err = auth.require_user(request)
        if err is not None:
            return err
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)

        include_all, include_org = _scope_flags(request, user)
        runs = sorted(
            run_service.list_runs(
                owner_user_id=user.user_id,
                include_all=include_all,
                include_org=include_org,
            ),
            key=lambda run: run.created_at or "",
            reverse=True,
        )[:50]
        return JSONResponse(
            _sanitize_for_json(
                {
                    "runs": [_live_snapshot_for_run(run) for run in runs],
                }
            )
        )

    async def cancel_run(request: Request) -> JSONResponse:
        """``POST /ui/runs/{run_id}/cancel`` — owner-only cancel."""
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)

        run_id = request.path_params["run_id"]
        user = auth.get_current_user(request) if auth.enabled else None
        try:
            run = _run_access_from_cookie(run_service, run_id, user)
        except ValueError:
            return JSONResponse({"error": "Run not found"}, 404)
        if run is None:
            err, _ = _authorize_control_token(request, run_service, run_id)
            if err is not None:
                if user is not None:
                    return JSONResponse({"error": "Run access denied"}, status_code=403)
                return err
        try:
            await manager.cancel_run(run_id)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        except Exception as exc:
            logger.error("cancel_run %s failed: %s", run_id, exc, exc_info=True)
            return JSONResponse({"error": f"Cancel failed: {exc}"}, 500)

        run = run_service.get_run(run_id)
        record_event(
            manager.bench_root,
            user,
            "run.cancel",
            request=request,
            run_id=run_id,
            session_id=run.session_id if run else "",
            success=True,
        )
        return JSONResponse(run.public_dict() if run else {"status": "cancelled"})

    # -----------------------------------------------------------------------
    # New: /client/runs/*
    # -----------------------------------------------------------------------

    async def client_claim_run(request: Request) -> JSONResponse:
        """``POST /client/runs/claim`` — client claims a run with token."""
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, 400)

        raw_token = body.get("run_token", "")
        if not raw_token:
            return JSONResponse({"error": "Missing required field: run_token"}, 400)

        client_info = body.get("client")

        try:
            assignment = run_service.claim_run(raw_token, client_info)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 401)

        base_url = str(request.base_url).rstrip("/")
        record_event(
            manager.bench_root,
            None,
            "run.claim",
            request=request,
            run_id=assignment.run_id,
            task_id=assignment.task_id,
            payload={"client": client_info or {}},
        )

        return JSONResponse(
            {
                "run_id": assignment.run_id,
                "mcp_url": f"{base_url}/mcp",
                "public_task_label": assignment.public_task_label,
                "status": assignment.status.value,
            }
        )

    async def client_start_run(request: Request) -> JSONResponse:
        """``POST /client/runs/start`` — create + claim in one step."""
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)

        user, auth_err = auth.resolve_client_user(request)
        if auth_err is not None:
            return auth_err

        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, 400)

        task = body.get("task", "")
        if not task:
            return JSONResponse({"error": "Missing required field: task"}, 400)

        mode = body.get("mode", "agent")
        client_info = body.get("client")

        try:
            assignment, raw_token, raw_control_token = run_service.create_and_claim(
                task=task,
                client_info=client_info,
                mode=mode,
                owner_user_id=user.user_id if user else "",
                owner_github_login=user.github_login if user else "",
                owner_email=user.email if user else "",
            )
        except QuotaExceeded as exc:
            record_event(
                manager.bench_root,
                user,
                "run.create",
                request=request,
                success=False,
                payload={"error": str(exc), "source": "client_start", "task": task},
            )
            return JSONResponse({"error": str(exc)}, 429)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

        base_url = str(request.base_url).rstrip("/")
        record_event(
            manager.bench_root,
            user,
            "run.create",
            request=request,
            run_id=assignment.run_id,
            task_id=assignment.task_id,
            payload={"source": "client_start"},
        )

        return JSONResponse(
            {
                "run_id": assignment.run_id,
                "token": raw_token,
                "control_token": raw_control_token,
                "mcp_url": f"{base_url}/mcp",
                "public_task_label": assignment.public_task_label,
                "status": assignment.status.value,
            }
        )

    async def client_upload_trace(request: Request) -> JSONResponse:
        """``POST /client/runs/{run_id}/trace`` — optional trace upload."""
        run_service = getattr(manager, "_run_service", None)
        if run_service is None:
            return JSONResponse({"error": "Run service not initialized"}, 503)

        run_id = request.path_params["run_id"]
        run = run_service.get_run(run_id)
        if run is None:
            return JSONResponse({"error": "Run not found"}, 404)
        if not run.session_id:
            return JSONResponse({"error": "No session bound to this run"}, 400)

        try:
            trace_data = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON body"}, 400)

        # Save to results/client/{session_id}/client_trace.json
        from pathlib import Path

        client_dir = Path(manager.bench_root) / "results" / "client" / run.session_id
        client_dir.mkdir(parents=True, exist_ok=True)
        trace_path = client_dir / "client_trace.json"
        trace_path.write_text(
            json.dumps(trace_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Client trace uploaded for run %s → %s", run_id, trace_path)
        return JSONResponse({"status": "uploaded", "path": str(trace_path)})

    # -----------------------------------------------------------------------
    # All routes
    # -----------------------------------------------------------------------

    return [
        # Auth
        Route("/auth/login", auth_login, methods=["GET"]),
        Route("/auth/callback", auth_callback, methods=["GET"]),
        Route("/auth/logout", auth_logout, methods=["POST"]),
        Route(
            "/skills/quanttutorbench-rest-agent",
            rest_agent_skill_page,
            methods=["GET"],
        ),
        Route("/ui/me", ui_me, methods=["GET"]),
        Route("/ui/api-key", get_api_key, methods=["GET"]),
        Route("/ui/api-key", rotate_api_key, methods=["POST"]),
        Route("/ui/api-key", revoke_api_key, methods=["DELETE"]),
        # Existing: results + tasks
        Route("/ui/tasks", list_tasks, methods=["GET"]),
        Route("/ui/results", list_results, methods=["GET"]),
        Route("/ui/results/{session_id}", get_detail, methods=["GET"]),
        Route("/ui/results/{session_id}/export", export_run_state, methods=["GET"]),
        Route("/ui/results/{session_id}/workspace", get_workspace, methods=["GET"]),
        Route(
            "/ui/results/{session_id}/workspace/preview/{path:path}",
            get_workspace_preview,
            methods=["GET"],
        ),
        Route("/ui/results/{session_id}/files/{path:path}", get_file, methods=["GET"]),
        # New: public task catalog
        Route("/ui/tasks/catalog", task_catalog, methods=["GET"]),
        Route("/ui/tasks/catalog/labels", task_catalog_labels, methods=["GET"]),
        # New: human review console
        Route("/ui/review/bundles", list_review_bundles, methods=["GET"]),
        Route("/ui/review/bundles/{bundle_id}", get_review_bundle, methods=["GET"]),
        Route(
            "/ui/review/bundles/{bundle_id}/opinions",
            append_review_opinion,
            methods=["POST"],
        ),
        Route(
            "/ui/review/bundles/{bundle_id}/opinions",
            replace_review_opinions,
            methods=["PUT"],
        ),
        # New: Run management (UI)
        Route("/ui/runs", create_run, methods=["POST"]),
        Route("/ui/runs", list_runs, methods=["GET"]),
        Route("/ui/runs/live", observe_runs_live, methods=["GET"]),
        Route("/ui/runs/{run_id}", get_run, methods=["GET"]),
        Route("/ui/runs/{run_id}/live", get_run_live, methods=["GET"]),
        Route("/ui/runs/{run_id}/cancel", cancel_run, methods=["POST"]),
        # New: Run management (Client)
        Route("/client/runs/claim", client_claim_run, methods=["POST"]),
        Route("/client/runs/start", client_start_run, methods=["POST"]),
        Route("/client/runs/{run_id}/trace", client_upload_trace, methods=["POST"]),
    ]
