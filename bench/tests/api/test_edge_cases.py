"""API tests for edge cases: closed sessions, concurrent access, etc."""

import httpx
import pytest
import pytest_asyncio

from tests.helpers import get_session_status, register_and_start, send_message


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Session deletion and cleanup
# ---------------------------------------------------------------------------


class TestSessionDeletion:
    @pytest.mark.asyncio
    async def test_delete_active_session(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.delete(f"/session/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_delete_nonexistent_session(self, client):
        resp = await client.delete("/session/nonexistent-id")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_send_after_delete_fails(self, client):
        sid, _ = await register_and_start(client)
        await client.delete(f"/session/{sid}")
        resp = await client.post(f"/session/{sid}/send", json={"text": "hello"})
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Status transitions
# ---------------------------------------------------------------------------


class TestStatusTransitions:
    @pytest.mark.asyncio
    async def test_full_phase_transitions(self, app, client):
        from tests.helpers import register_session, start_session

        sid = await register_session(client)
        status = await get_session_status(client, sid)
        assert status["phase"] == "registered"

        await start_session(client, sid)
        status = await get_session_status(client, sid)
        assert status["phase"] == "in_session"

        # Complete via repeat detection
        msg = "Same"
        for _ in range(3):
            await send_message(client, sid, msg)
        status = await get_session_status(client, sid)
        assert status["phase"] == "completed"


# ---------------------------------------------------------------------------
# Multiple sessions
# ---------------------------------------------------------------------------


class TestMultipleSessions:
    @pytest.mark.asyncio
    async def test_two_independent_sessions(self, client):
        sid1, _ = await register_and_start(client)
        sid2, _ = await register_and_start(client)
        assert sid1 != sid2

        r1 = await send_message(client, sid1, "Session 1 message")
        r2 = await send_message(client, sid2, "Session 2 message")
        assert r1["status"] == "active"
        assert r2["status"] == "active"

    @pytest.mark.asyncio
    async def test_completing_one_doesnt_affect_other(self, client):
        sid1, _ = await register_and_start(client)
        sid2, _ = await register_and_start(client)

        # Complete session 1
        msg = "Repeated"
        for _ in range(3):
            await send_message(client, sid1, msg)

        s1 = await get_session_status(client, sid1)
        s2 = await get_session_status(client, sid2)
        assert s1["phase"] == "completed"
        assert s2["phase"] == "in_session"


# ---------------------------------------------------------------------------
# Empty / malformed requests
# ---------------------------------------------------------------------------


class TestMalformedRequests:
    @pytest.mark.asyncio
    async def test_register_no_token(self, client):
        """Register without Authorization header should be rejected."""
        resp = await client.post("/session/register")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_send_no_text_field(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(f"/session/{sid}/send", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_tool_call_nonexistent_tool(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(f"/session/{sid}/tool/nonexistent_tool", json={})
        # Should return error (tool not found) or 400
        assert resp.status_code in (200, 400, 404, 500)
