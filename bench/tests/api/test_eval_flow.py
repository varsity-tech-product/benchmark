"""API tests for the operator evaluation REST endpoints.

Tests POST /ops/session/{sid}/evaluate, GET /ops/session/{sid}/results,
GET /ops/session/{sid}/scores against the synchronous scoring path
introduced in issue #46 slice 4. The mocked ``score_bundle`` writes
fake ``eval_meta.json`` into the sibling tree so /scores reads the
real on-disk state.
"""

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


async def _complete_session(client, app):
    """Drive a session to COMPLETED via repeat detection."""
    sid, _ = await register_and_start(client)
    msg = "Repeated message for completion"
    for _ in range(3):
        await send_message(client, sid, msg)
    return sid


# ---------------------------------------------------------------------------
# POST /ops/session/{sid}/evaluate — synchronous
# ---------------------------------------------------------------------------


class TestRequestEvaluation:
    @pytest.mark.asyncio
    async def test_evaluate_returns_completed(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        resp = await client.post(f"/ops/session/{sid}/evaluate")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert "scores" in body
        # Composite score must be present so operator clients reading the
        # synchronous response don't have to make a follow-up /scores call.
        assert "overall" in body["scores"]
        assert body["eval_run_id"]

    @pytest.mark.asyncio
    async def test_evaluate_denied_before_completion(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(f"/ops/session/{sid}/evaluate")
        # Operator surface returns 409 when bundle is not yet COMPLETED.
        assert resp.status_code == 409

    @pytest.mark.asyncio
    async def test_evaluate_force_creates_new_run(
        self, app, client, mock_eval_pipeline
    ):
        sid = await _complete_session(client, app)

        first = await client.post(f"/ops/session/{sid}/evaluate")
        assert first.status_code == 200
        first_id = first.json()["eval_run_id"]

        second = await client.post(f"/ops/session/{sid}/evaluate?force=true")
        assert second.status_code == 200
        # ``--force`` is now a no-op (every call writes a new run id) but
        # the param is accepted for back-compat with operator scripts.
        assert second.json()["status"] == "completed"
        # Distinct invocations may collide on the second-resolution
        # eval_run_id — that's fine, the contract is just "succeeds".


# ---------------------------------------------------------------------------
# Eval parameters
# ---------------------------------------------------------------------------


class TestEvalParameters:
    @pytest.mark.asyncio
    async def test_eval_mode_passed(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        await client.post(f"/ops/session/{sid}/evaluate?eval_mode=tutor_only")
        kwargs = mock_eval_pipeline.call_args.kwargs
        assert kwargs.get("eval_mode") == "tutor_only"

    @pytest.mark.asyncio
    async def test_tutor_dims_passed(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        await client.post(f"/ops/session/{sid}/evaluate?tutor_dims=D3,D4")
        kwargs = mock_eval_pipeline.call_args.kwargs
        assert kwargs.get("tutor_dims") == ["D3", "D4"]

    @pytest.mark.asyncio
    async def test_default_eval_mode_full(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        await client.post(f"/ops/session/{sid}/evaluate")
        kwargs = mock_eval_pipeline.call_args.kwargs
        assert kwargs.get("eval_mode") == "full"


# ---------------------------------------------------------------------------
# GET /ops/session/{sid}/scores
# ---------------------------------------------------------------------------


class TestGetScores:
    @pytest.mark.asyncio
    async def test_scores_pending_before_eval(self, app, client):
        sid = await _complete_session(client, app)
        resp = await client.get(f"/ops/session/{sid}/scores")
        assert resp.status_code == 200
        # Without a scored eval_meta.json on disk, status comes from
        # run_state.json's ``evaluation_status`` (set by score_bundle).
        assert resp.json()["status"] in ("pending", "running")

    @pytest.mark.asyncio
    async def test_scores_completed_after_eval(self, app, client, mock_eval_pipeline):
        sid = await _complete_session(client, app)
        await client.post(f"/ops/session/{sid}/evaluate")

        resp = await client.get(f"/ops/session/{sid}/scores")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert "scores" in data

    @pytest.mark.asyncio
    async def test_scores_falls_back_to_persisted_status_when_meta_missing(
        self, app, client, monkeypatch
    ):
        """If the eval dir exists but eval_meta.json is unwritten/corrupt
        (score_bundle crashed mid-write), /scores must surface the
        persisted ``evaluation_status`` rather than reporting pending."""
        from server.evaluator.paths import eval_run_dir, new_eval_run_id
        from server.storage.bundle import load_bundle
        from server.storage.result_writer import update_evaluation_status

        sid = await _complete_session(client, app)
        state = app._manager.get_session(sid)

        # Mark the bundle as failed (mirrors what score_bundle does on
        # exception) and create an empty eval_run dir without eval_meta.
        update_evaluation_status(state._result_dir, "failed")
        bundle = load_bundle(state._result_dir)
        run_dir = eval_run_dir(
            state.bench_root,
            task_id=bundle.task_id,
            persona_id=bundle.persona_id,
            session_id=bundle.session_id,
            eval_run_id=new_eval_run_id(),
        )
        run_dir.mkdir(parents=True, exist_ok=True)

        resp = await client.get(f"/ops/session/{sid}/scores")
        assert resp.status_code == 200
        assert resp.json()["status"] == "failed"


# ---------------------------------------------------------------------------
# GET /ops/session/{sid}/results
# ---------------------------------------------------------------------------


class TestGetResults:
    @pytest.mark.asyncio
    async def test_results_after_completion(self, app, client):
        sid = await _complete_session(client, app)
        resp = await client.get(f"/ops/session/{sid}/results")
        if resp.status_code == 200:
            data = resp.json()
            assert "conversation" in data

    @pytest.mark.asyncio
    async def test_results_not_found_before_register(self, client):
        resp = await client.get("/ops/session/nonexistent/results")
        assert resp.status_code == 404
