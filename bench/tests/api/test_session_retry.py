"""API tests for the failed-session retry endpoint (issue #126 / P1).

POST /session/{sid}/retry is owner-scoped (run_token). Only sessions whose
``termination_reason`` classifies as ``infrastructure_failure`` are
retryable; everything else returns 409 with the category attached.
"""

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from server.api.http_app import _classify_session_failure
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


def _force_termination_reason(state, reason: str) -> None:
    """Mutate the persisted run_state.json so retry classification reads
    a chosen termination_reason without needing to drive the real failure
    path. The retry endpoint reads from disk via state.get_run_results()."""
    path = Path(state._result_dir) / "run_state.json"
    rs = json.loads(path.read_text(encoding="utf-8"))
    rs["termination_reason"] = reason
    path.write_text(json.dumps(rs), encoding="utf-8")


class TestClassifySessionFailure:
    def test_user_sim_error_is_infrastructure(self):
        assert (
            _classify_session_failure("user_sim_error:network")
            == "infrastructure_failure"
        )
        assert (
            _classify_session_failure("user_sim_error:parse")
            == "infrastructure_failure"
        )

    def test_agent_stuck_is_agent_gave_up(self):
        assert _classify_session_failure("agent_stuck") == "agent_gave_up"
        assert _classify_session_failure("agent_abandoned") == "agent_gave_up"

    def test_terminal_categories(self):
        assert _classify_session_failure("objectives_met") == "terminal_success"
        assert _classify_session_failure("user_satisfied") == "terminal_success"
        assert _classify_session_failure("max_turns") == "max_turns_reached"
        assert _classify_session_failure("timeout") == "max_turns_reached"

    def test_unknown_categories(self):
        assert _classify_session_failure(None) == "unknown"
        assert _classify_session_failure("") == "unknown"
        assert _classify_session_failure("something_else") == "unknown"


class TestRetryEndpoint:
    @pytest.mark.asyncio
    async def test_unknown_session_returns_404(self, client):
        resp = await client.post("/session/no-such-sid/retry")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_active_session_returns_409(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(f"/session/{sid}/retry")
        assert resp.status_code == 409
        body = resp.json()
        assert body["current_phase"] == "in_session"

    @pytest.mark.asyncio
    async def test_agent_stuck_not_retryable(self, app, client):
        sid, _ = await register_and_start(client)
        msg = "Repeated message"
        for _ in range(3):
            await send_message(client, sid, msg)
        # repeat detection → status="failed", reason="agent_stuck"
        resp = await client.post(f"/session/{sid}/retry")
        assert resp.status_code == 409
        body = resp.json()
        assert body["category"] == "agent_gave_up"
        assert body["termination_reason"] == "agent_stuck"

    @pytest.mark.asyncio
    async def test_max_turns_not_retryable(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_state(app, sid)
        state.session._max_turns = 1
        await send_message(client, sid, "Done.")
        resp = await client.post(f"/session/{sid}/retry")
        assert resp.status_code == 409
        body = resp.json()
        assert body["category"] == "max_turns_reached"

    @pytest.mark.asyncio
    async def test_objectives_met_not_retryable(self, app, client):
        # Synthesize objectives_met by patching the persisted reason.
        sid, _ = await register_and_start(client)
        state = _get_state(app, sid)
        state.session._max_turns = 1
        await send_message(client, sid, "Done.")
        _force_termination_reason(state, "objectives_met")
        resp = await client.post(f"/session/{sid}/retry")
        assert resp.status_code == 409
        body = resp.json()
        assert body["category"] == "terminal_success"

    @pytest.mark.asyncio
    async def test_infrastructure_failure_creates_new_session(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_state(app, sid)
        original_run_id = state.run_id
        assert original_run_id

        state.session._max_turns = 1
        await send_message(client, sid, "Done.")
        # Patch persisted reason to simulate an NPC/sandbox crash.
        _force_termination_reason(state, "user_sim_error:network")

        resp = await client.post(f"/session/{sid}/retry")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "retry_started"
        assert body["category"] == "infrastructure_failure"
        assert body["previous_session_id"] == sid
        assert body["run_id"] == original_run_id
        new_sid = body["session_id"]
        assert new_sid != sid

        # Fresh session is bound to the same run, in REGISTERED phase.
        new_state = _get_state(app, new_sid)
        assert new_state.run_id == original_run_id
        assert new_state.phase.value == "registered"

        # Run-level binding now points at the new session.
        run = app._manager._run_service.get_run(original_run_id)
        assert run.session_id == new_sid
        assert run.status.value == "active"

    @pytest.mark.asyncio
    async def test_retry_preserves_original_persona(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_state(app, sid)
        original_persona = state.persona_id
        assert original_persona

        state.session._max_turns = 1
        await send_message(client, sid, "Done.")
        _force_termination_reason(state, "user_sim_error:network")

        resp = await client.post(f"/session/{sid}/retry")
        assert resp.status_code == 200
        new_sid = resp.json()["session_id"]
        new_state = _get_state(app, new_sid)
        assert new_state.persona_id == original_persona

    @pytest.mark.asyncio
    async def test_failed_retry_register_restores_run(
        self, app, client, monkeypatch
    ):
        from server.api.session_api import SessionState

        sid, _ = await register_and_start(client)
        state = _get_state(app, sid)
        original_run_id = state.run_id
        original_session_id = sid
        state.session._max_turns = 1
        await send_message(client, sid, "Done.")
        original_result_dir = str(state._result_dir)
        _force_termination_reason(state, "user_sim_error:network")

        # Force the next register() to fail so the rollback path runs.
        def _fail_register(self, task_id, persona_id=None):
            return {"error": "Simulated sandbox boot failure"}

        monkeypatch.setattr(SessionState, "register", _fail_register)

        resp = await client.post(f"/session/{sid}/retry")
        assert resp.status_code == 500

        run = app._manager._run_service.get_run(original_run_id)
        assert run.status.value == "completed"
        assert run.session_id == original_session_id
        assert run.result_dir == original_result_dir

    @pytest.mark.asyncio
    async def test_infrastructure_retry_then_complete_works(
        self, app, client, monkeypatch
    ):
        # End-to-end: retry, start new session, send to completion.
        from server.api.session_api import SessionState

        monkeypatch.setattr(SessionState, "_run_evaluation", lambda self, sid: None)

        sid, _ = await register_and_start(client)
        state = _get_state(app, sid)
        state.session._max_turns = 1
        await send_message(client, sid, "Done.")
        _force_termination_reason(state, "user_sim_error:network")

        resp = await client.post(f"/session/{sid}/retry")
        assert resp.status_code == 200
        new_sid = resp.json()["session_id"]

        start_resp = await client.post(f"/session/{new_sid}/start", json={})
        assert start_resp.status_code == 200

        new_state = _get_state(app, new_sid)
        new_state.session._max_turns = 1
        body = await send_message(client, new_sid, "Final.")
        assert body["status"] == "completed"
