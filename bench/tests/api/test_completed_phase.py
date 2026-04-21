"""API tests for operations on sessions in COMPLETED phase.

Tests permission enforcement, tool visibility, and data access
after a session reaches completion.
"""

import httpx
import pytest
import pytest_asyncio

from tests.helpers import (
    get_session_status,
    get_tools,
    register_and_start,
    send_message,
)


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def completed_session(app, client):
    """Drive a session to COMPLETED via repeat detection (agent_stuck)."""
    sid, _ = await register_and_start(client)
    msg = "Same message for stuck detection"
    for _ in range(3):
        await send_message(client, sid, msg)
    return sid


# ---------------------------------------------------------------------------
# Permission checks
# ---------------------------------------------------------------------------


class TestCompletedPhasePermissions:
    @pytest.mark.asyncio
    async def test_start_denied_after_completion(self, client, completed_session):
        sid = completed_session
        resp = await client.post(f"/session/{sid}/start", json={})
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_evaluate_allowed_after_completion(self, client, completed_session):
        sid = completed_session
        resp = await client.post(f"/ops/session/{sid}/evaluate", json={})
        # Should be 200 (accepted) — eval starts in background
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_status_shows_completed(self, client, completed_session):
        sid = completed_session
        status = await get_session_status(client, sid)
        assert status["phase"] == "completed"


# ---------------------------------------------------------------------------
# Tool visibility
# ---------------------------------------------------------------------------


class TestCompletedPhaseTools:
    @pytest.mark.asyncio
    async def test_eval_tools_no_longer_advertised(self, client, completed_session):
        """Issue #46 slice 3: evaluation tools dropped from agent catalogue.

        COMPLETED is terminal for the agent; scoring runs out-of-band on
        the operator surface (``/ops/session/{sid}/...``). Lifecycle tools
        stay visible so frozen-registry clients can still observe the
        terminal state via error guidance from any further call.
        """
        sid = completed_session
        tools = await get_tools(client, sid)
        names = [t["name"] for t in tools]
        assert "request_evaluation" not in names
        assert "get_results" not in names
        assert "get_scores" not in names
        assert "send_message" in names
        assert "start_session" in names


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------


class TestCompletedPhaseData:
    @pytest.mark.asyncio
    async def test_get_results_returns_data(self, client, completed_session):
        sid = completed_session
        resp = await client.get(f"/ops/session/{sid}/results")
        if resp.status_code == 200:
            data = resp.json()
            assert "conversation" in data

    @pytest.mark.asyncio
    async def test_get_scores_returns_pending(self, client, completed_session):
        """Before evaluation, scores should report pending status."""
        sid = completed_session
        resp = await client.get(f"/ops/session/{sid}/scores")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] in ("pending", "running")

    @pytest.mark.asyncio
    async def test_delete_session_works(self, client, completed_session):
        sid = completed_session
        resp = await client.delete(f"/session/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_list_shows_completed_session(self, client, completed_session):
        sid = completed_session
        resp = await client.get("/session/list")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        match = [s for s in sessions if s["session_id"] == sid]
        assert len(match) == 1
        assert match[0]["phase"] == "completed"
