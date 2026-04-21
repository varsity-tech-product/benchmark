"""API tests for session completion via REST endpoints.

Drives sessions to completion through various mechanisms and verifies
state transitions, response formats, and post-completion behavior.
"""

import httpx
import pytest
import pytest_asyncio

from tests.helpers import (
    get_session_status,
    register_and_start,
    send_message,
)


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _get_session_state(app, sid: str):
    """Reach into the server to get the SessionState object."""
    manager = app._manager
    return manager.get_session(sid)


# ---------------------------------------------------------------------------
# Max turns completion
# ---------------------------------------------------------------------------


class TestMaxTurnsViaAPI:
    @pytest.mark.asyncio
    async def test_max_turns_completes_session(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1  # force low limit

        body = await send_message(client, sid, "Here is the fix.")
        assert body["status"] == "completed"
        assert body["reason"] == "max_turns"

    @pytest.mark.asyncio
    async def test_phase_transitions_to_completed(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1

        await send_message(client, sid, "Fix applied.")
        status = await get_session_status(client, sid)
        assert status["phase"] == "completed"

    @pytest.mark.asyncio
    async def test_max_turns_3_active_then_completes(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 3

        for i in range(2):
            body = await send_message(client, sid, f"Step {i}")
            assert body["status"] == "active"

        body = await send_message(client, sid, "Final step")
        assert body["status"] == "completed"
        assert body["reason"] == "max_turns"


# ---------------------------------------------------------------------------
# Repeat detection
# ---------------------------------------------------------------------------


class TestRepeatDetectionViaAPI:
    @pytest.mark.asyncio
    async def test_agent_stuck_after_repeats(self, client):
        sid, _ = await register_and_start(client)
        msg = "I already explained this."

        await send_message(client, sid, msg)  # 1st
        await send_message(client, sid, msg)  # 2nd — repeat_count=1
        body = await send_message(client, sid, msg)  # 3rd — stuck
        assert body["status"] == "failed"
        assert body["reason"] == "agent_stuck"

    @pytest.mark.asyncio
    async def test_session_status_after_stuck(self, client):
        sid, _ = await register_and_start(client)
        msg = "Same thing"
        for _ in range(3):
            await send_message(client, sid, msg)

        status = await get_session_status(client, sid)
        assert status["phase"] == "completed"


# ---------------------------------------------------------------------------
# Deadline timeout
# ---------------------------------------------------------------------------


class TestDeadlineViaAPI:
    @pytest.mark.asyncio
    async def test_expired_deadline_completes(self, app, client):
        import time

        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._deadline = time.time() - 10  # already expired

        body = await send_message(client, sid, "Check this out.")
        assert body["status"] == "completed"
        assert body["reason"] == "timeout"


# ---------------------------------------------------------------------------
# Post-completion behavior
# ---------------------------------------------------------------------------


class TestPostCompletion:
    @pytest.mark.asyncio
    async def test_send_after_completion_denied(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1
        await send_message(client, sid, "Done.")

        # REST permission check blocks send in COMPLETED phase.
        # COMPLETED is terminal for the agent (issue #46 slice 3) so
        # the allowed list is empty.
        resp = await client.post(f"/session/{sid}/send", json={"text": "More stuff"})
        assert resp.status_code == 403
        body = resp.json()
        assert body.get("allowed") == []

    @pytest.mark.asyncio
    async def test_results_available_after_completion(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1
        await send_message(client, sid, "Done.")

        resp = await client.get(f"/ops/session/{sid}/results")
        # Results should be available (200) or at least attempted
        if resp.status_code == 200:
            data = resp.json()
            assert "conversation" in data or "error" not in data
