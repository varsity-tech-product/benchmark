"""Score-bound human review API routes."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from eval.storage.human_reviews import HumanReviewStore
from server.audit import record_event
from server.auth import AuthService
from server.web.ui_indexer import ResultIndexer
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route


def review_api_routes(manager) -> list[Route]:
    auth = AuthService(manager.bench_root)
    indexer = ResultIndexer(manager.bench_root)
    store = HumanReviewStore()

    def _review_scope_flags(request: Request, user) -> tuple[bool, bool]:
        mine = request.query_params.get("mine", "").strip().lower()
        if mine in ("1", "true", "yes", "on"):
            return False, False
        scope = request.query_params.get("scope", "").strip().lower()
        include_all = bool(getattr(user, "is_reviewer", False)) and scope in ("", "all")
        include_org = scope == "org"
        return include_all, include_org

    async def _body(request: Request) -> dict[str, Any]:
        if request.method == "GET":
            return {}
        try:
            payload = await request.json()
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _session_id(request: Request, payload: dict[str, Any]) -> str:
        return str(
            request.query_params.get("session_id")
            or request.query_params.get("bundle_id")
            or payload.get("session_id")
            or payload.get("bundle_id")
            or ""
        ).strip()

    def _result_dir(
        request: Request,
        user,
        payload: dict[str, Any],
    ) -> tuple[Path | None, str, JSONResponse | None]:
        session_id = _session_id(request, payload)
        if not session_id:
            return None, "", JSONResponse(
                {"error": "session_id or bundle_id is required"},
                status_code=400,
            )
        include_all, include_org = _review_scope_flags(request, user)
        try:
            result_dir = indexer.resolve_result_dir(
                session_id,
                user=user,
                include_all=include_all,
                include_org=include_org,
            )
        except PermissionError:
            return None, "", JSONResponse({"error": "Bundle access denied"}, 403)
        if result_dir is None:
            return None, "", JSONResponse({"error": "Bundle not found"}, 404)
        return result_dir, session_id, None

    def _canonical_review_payload(
        result_dir: Path,
        requested_session_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        run_state = _read_json(result_dir / "run_state.json")
        if not isinstance(run_state, dict):
            run_state = {}
        session_id = str(
            run_state.get("session_id") or requested_session_id or result_dir.name
        ).strip()
        task_id = str(run_state.get("task_id") or payload.get("task_id") or "").strip()
        canonical = dict(payload)
        canonical["session_id"] = session_id
        canonical["bundle_id"] = session_id
        canonical["task_id"] = task_id
        return canonical

    async def get_reviews(request: Request) -> JSONResponse:
        user, err = auth.require_reviewer(request)
        if err is not None:
            return err
        payload = await _body(request)
        result_dir, _, err = _result_dir(request, user, payload)
        if err is not None:
            return err
        score_id = request.path_params["score_id"]
        try:
            reviews = store.list_reviews(result_dir, score_id)
            summary = store.summary(result_dir, score_id)
            current = store.latest_review_for_user(result_dir, score_id, user)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)
        return JSONResponse(
            {
                "score_id": score_id,
                "reviews": reviews,
                "summary": summary,
                "current_user_review": current,
            }
        )

    async def submit_review(request: Request) -> JSONResponse:
        user, err = auth.require_reviewer(request)
        if err is not None:
            return err
        payload = await _body(request)
        if not payload:
            return JSONResponse({"error": "Invalid JSON body"}, 400)
        result_dir, requested_session_id, err = _result_dir(request, user, payload)
        if err is not None:
            return err
        score_id = request.path_params["score_id"]
        canonical_payload = _canonical_review_payload(
            result_dir,
            requested_session_id,
            payload,
        )
        try:
            record = store.submit_review(result_dir, score_id, user, canonical_payload)
            summary = store.summary(result_dir, score_id)
        except FileNotFoundError:
            return JSONResponse({"error": "Score not found"}, 404)
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, 400)

        record_event(
            manager.bench_root,
            user,
            "review.score_submit",
            request=request,
            session_id=str(canonical_payload.get("session_id") or ""),
            task_id=str(canonical_payload.get("task_id") or ""),
            payload={"score_id": score_id, "review_id": record["review_id"]},
        )
        return JSONResponse(
            {
                "score_id": score_id,
                "review": record,
                "summary": summary,
            },
            status_code=201,
        )

    return [
        Route("/api/reviews/{score_id}", get_reviews, methods=["GET"]),
        Route("/api/reviews/{score_id}", submit_review, methods=["POST"]),
    ]


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
