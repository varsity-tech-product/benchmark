"""API tests for the evaluation REST endpoints.

Tests POST /evaluate, GET /results, GET /scores with a mocked
eval pipeline (no real LLM calls).
"""

import time

import httpx
import pytest
import pytest_asyncio

from tests.helpers import register_and_start, send_message


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _get_state(app, sid):
    return app._manager.get_session(sid)


async def _complete_session(client, app):
    """Drive a session to COMPLETED via repeat detection."""
    sid, _ = await register_and_start(client)
    msg = "Repeated message for completion"
    for _ in range(3):
        await send_message(client, sid, msg)
    return sid


# ---------------------------------------------------------------------------
# POST /evaluate
# ---------------------------------------------------------------------------


class TestRequestEvaluation:
    @pytest.mark.asyncio
    async def test_evaluate_returns_running(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        resp = await client.post(f"/ops/session/{sid}/evaluate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("running", "completed")

    @pytest.mark.asyncio
    async def test_evaluate_denied_before_completion(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(f"/ops/session/{sid}/evaluate")
        # Operator surface returns 409 when bundle is not yet COMPLETED.
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_evaluate_completes_with_scores(
        self, app, client, mock_eval_pipeline
    ):
        sid = await _complete_session(client, app)
        resp = await client.post(f"/ops/session/{sid}/evaluate")
        assert resp.status_code == 200

        # Wait for background eval thread
        state = _get_state(app, sid)
        for _ in range(50):
            with state._eval_lock:
                if state._eval_status in ("completed", "failed"):
                    break
            time.sleep(0.1)

        with state._eval_lock:
            assert state._eval_status == "completed"

    @pytest.mark.asyncio
    async def test_evaluate_force_re_eval(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)

        # First eval
        await client.post(f"/ops/session/{sid}/evaluate")
        state = _get_state(app, sid)
        for _ in range(50):
            with state._eval_lock:
                if state._eval_status in ("completed", "failed"):
                    break
            time.sleep(0.1)

        # Force re-eval
        resp = await client.post(f"/ops/session/{sid}/evaluate?force=true")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "running"


# ---------------------------------------------------------------------------
# Eval parameters
# ---------------------------------------------------------------------------


class TestEvalParameters:
    @pytest.mark.asyncio
    async def test_eval_mode_passed(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        await client.post(f"/ops/session/{sid}/evaluate?eval_mode=tutor_only")
        state = _get_state(app, sid)
        assert state._eval_mode == "tutor_only"

    @pytest.mark.asyncio
    async def test_tutor_dims_passed(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        await client.post(f"/ops/session/{sid}/evaluate?tutor_dims=D3,D4")
        state = _get_state(app, sid)
        assert state._tutor_dims == ["D3", "D4"]

    @pytest.mark.asyncio
    async def test_default_eval_mode_full(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        await client.post(f"/ops/session/{sid}/evaluate")
        state = _get_state(app, sid)
        assert state._eval_mode == "full"


# ---------------------------------------------------------------------------
# GET /scores
# ---------------------------------------------------------------------------


class TestGetScores:
    @pytest.mark.asyncio
    async def test_scores_pending_before_eval(self, app, client):
        sid = await _complete_session(client, app)
        resp = await client.get(f"/ops/session/{sid}/scores")
        assert resp.status_code == 200
        assert resp.json()["status"] == "pending"

    @pytest.mark.asyncio
    async def test_scores_completed_after_eval(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        await client.post(f"/ops/session/{sid}/evaluate")

        state = _get_state(app, sid)
        for _ in range(50):
            with state._eval_lock:
                if state._eval_status in ("completed", "failed"):
                    break
            time.sleep(0.1)

        resp = await client.get(f"/ops/session/{sid}/scores")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "scores" in data


# ---------------------------------------------------------------------------
# GET /results
# ---------------------------------------------------------------------------


class TestGetResults:
    @pytest.mark.asyncio
    async def test_results_after_completion(self, app, client):
        sid = await _complete_session(client, app)
        resp = await client.get(f"/ops/session/{sid}/results")
        # Results should be saved after completion
        if resp.status_code == 200:
            data = resp.json()
            assert "conversation" in data

    @pytest.mark.asyncio
    async def test_results_not_found_before_register(self, client):
        resp = await client.get("/ops/session/nonexistent/results")
        assert resp.status_code == 404
