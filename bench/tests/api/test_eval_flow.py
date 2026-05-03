"""API tests for the server-side evaluation boundary.

Public session endpoints can read exported results and score summaries, but
only the operator surface may trigger scoring.
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
    state = app._manager.get_session(sid)
    assert state is not None
    return state


async def _complete_session(client):
    """Drive a session to COMPLETED via repeat detection."""
    sid, _ = await register_and_start(client)
    msg = "Repeated message for completion"
    for _ in range(3):
        await send_message(client, sid, msg)
    return sid


def _wait_eval_done(state):
    for _ in range(50):
        with state._eval_lock:
            if state._eval_status in ("completed", "failed"):
                return
        time.sleep(0.1)
    raise AssertionError("evaluation did not finish")


async def _complete_and_wait_for_auto_eval(app, client):
    """Drive completion and block until the P0 auto-eval has settled.

    Post-#126 P0 every terminal transition kicks off a server-internal
    eval keyed ``auto:{session_id}``. Tests that want to drive operator
    re-evaluation deterministically need the auto-eval to finish first
    so the next ops_evaluate allocates a fresh ``score_n``.
    """
    sid = await _complete_session(client)
    state = _get_state(app, sid)
    _wait_eval_done(state)
    return sid, state


class TestPublicEvaluationBoundary:
    @pytest.mark.asyncio
    async def test_public_evaluate_route_absent_after_completion(self, client):
        sid = await _complete_session(client)

        resp = await client.post(f"/session/{sid}/evaluate")

        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_public_evaluate_route_absent_before_completion(self, client):
        sid, _ = await register_and_start(client)

        resp = await client.post(f"/session/{sid}/evaluate")

        assert resp.status_code == 404


class TestOperatorEvaluation:
    @pytest.mark.asyncio
    async def test_ops_evaluate_appends_score_run_after_auto_eval(
        self, app, client, mock_eval_pipeline
    ):
        # Auto-eval (P0) takes score_1, operator gets score_2.
        sid, state = await _complete_and_wait_for_auto_eval(app, client)

        resp = await client.post(f"/ops/session/{sid}/evaluate")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("running", "completed")
        assert body["score_id"] == "score_2"

        _wait_eval_done(state)
        with state._eval_lock:
            assert state._eval_status == "completed"
            assert state._active_score_id == "score_2"

    @pytest.mark.asyncio
    async def test_ops_evaluate_denied_before_completion(self, client):
        sid, _ = await register_and_start(client)

        resp = await client.post(f"/ops/session/{sid}/evaluate")

        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_ops_evaluate_appends_two_score_runs(
        self, app, client, mock_eval_pipeline
    ):
        sid, state = await _complete_and_wait_for_auto_eval(app, client)

        first = await client.post(f"/ops/session/{sid}/evaluate")
        assert first.json()["score_id"] == "score_2"
        _wait_eval_done(state)

        second = await client.post(f"/ops/session/{sid}/evaluate")
        assert second.status_code == 200
        assert second.json()["score_id"] == "score_3"


class TestOperatorEvalParameters:
    @pytest.mark.asyncio
    async def test_eval_mode_passed(self, app, client, mock_eval_pipeline):
        sid, state = await _complete_and_wait_for_auto_eval(app, client)

        await client.post(f"/ops/session/{sid}/evaluate?eval_mode=qp")
        _wait_eval_done(state)

        assert state._eval_mode == "qp"
        assert mock_eval_pipeline.call_args.kwargs["eval_mode"] == "qp"

    @pytest.mark.asyncio
    async def test_default_eval_mode_full(self, app, client, mock_eval_pipeline):
        sid, state = await _complete_and_wait_for_auto_eval(app, client)

        await client.post(f"/ops/session/{sid}/evaluate")
        _wait_eval_done(state)

        assert mock_eval_pipeline.call_args.kwargs["eval_mode"] == "full"


class TestGetScores:
    @pytest.mark.asyncio
    async def test_public_scores_show_auto_eval_after_completion(
        self, app, client
    ):
        # Post-#126 P0 the auto-eval is already in flight by the time the
        # client sees the completion turn — so /scores never lingers in
        # 'pending'. It transitions through 'running' to 'completed' or
        # 'failed' with no client trigger.
        sid = await _complete_session(client)

        resp = await client.get(f"/session/{sid}/scores")

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("running", "completed", "failed")
        assert body["score_id"] == "score_1"

    @pytest.mark.asyncio
    async def test_public_scores_completed_without_private_cost(
        self, app, client, mock_eval_pipeline
    ):
        sid, state = await _complete_and_wait_for_auto_eval(app, client)

        resp = await client.get(f"/session/{sid}/scores")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["score_id"] == "score_1"
        assert data["scores"]["overall_score"] == 0.775
        assert data["scores"]["judge_reliability"]["validation_run_id"]
        assert "score" not in data
        assert "cost" not in data
        assert "eval_cost_usd" not in data["scores"]

    @pytest.mark.asyncio
    async def test_ops_scores_include_private_cost(
        self, app, client, mock_eval_pipeline
    ):
        sid, state = await _complete_and_wait_for_auto_eval(app, client)

        resp = await client.get(f"/ops/session/{sid}/scores")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["score_id"] == "score_1"
        assert data["cost"]["eval_cost_usd"] == 0.003

    @pytest.mark.asyncio
    async def test_scores_history_after_multiple_evals(
        self, app, client, mock_eval_pipeline
    ):
        sid, state = await _complete_and_wait_for_auto_eval(app, client)

        await client.post(f"/ops/session/{sid}/evaluate")
        _wait_eval_done(state)
        await client.post(f"/ops/session/{sid}/evaluate")
        _wait_eval_done(state)

        resp = await client.get(f"/session/{sid}/scores?history=true")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "history"
        assert [s["score_id"] for s in data["scores"]] == [
            "score_1",
            "score_2",
            "score_3",
        ]
        assert all("cost" not in s for s in data["scores"])


class TestGetResults:
    @pytest.mark.asyncio
    async def test_public_results_after_completion_are_export_scoped(self, client):
        sid = await _complete_session(client)

        resp = await client.get(f"/session/{sid}/results")

        assert resp.status_code == 200
        data = resp.json()
        assert "conversation" in data
        assert "tool_logs" not in data
        assert "tc_debug_history" not in data

    @pytest.mark.asyncio
    async def test_ops_results_after_completion_include_full_state(self, client):
        sid = await _complete_session(client)

        resp = await client.get(f"/ops/session/{sid}/results")

        assert resp.status_code == 200
        data = resp.json()
        assert "conversation" in data
        assert "tool_logs" in data

    @pytest.mark.asyncio
    async def test_results_not_found_before_register(self, client):
        resp = await client.get("/session/nonexistent/results")

        assert resp.status_code == 404
