"""API tests for permission enforcement across session phases.

Verifies that wrong-phase operations return 403 with the correct
``allowed`` hint, matching the state machine in protocol.py.
"""

import pytest
import pytest_asyncio
import httpx

from tests.helpers import (
    register_session,
    register_and_start,
)


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestRegisteredPhasePermissions:
    """After register, only start is allowed."""

    @pytest.mark.asyncio
    async def test_send_denied(self, client):
        sid = await register_session(client)
        resp = await client.post(
            f"/session/{sid}/send", json={"text": "hello"}
        )
        assert resp.status_code == 403
        assert "start_session" in resp.json()["allowed"]

    @pytest.mark.asyncio
    async def test_tool_call_denied(self, client):
        sid = await register_session(client)
        resp = await client.post(
            f"/session/{sid}/tool/shell_exec", json={"command": "ls"}
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_evaluate_not_on_agent_surface(self, client):
        """Issue #46 slice 3: /session/.../evaluate is gone from the
        agent REST surface — eval lives under /ops/* now."""
        sid = await register_session(client)
        resp = await client.post(f"/session/{sid}/evaluate", json={})
        assert resp.status_code == 404


class TestInSessionPhasePermissions:
    """After start, send + domain tools allowed; register/start denied."""

    @pytest.mark.asyncio
    async def test_send_allowed(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(
            f"/session/{sid}/send",
            json={"text": "Let me look at the code."},
        )
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_domain_tool_allowed(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.get(f"/session/{sid}/tools")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        tool_names = [t["name"] for t in tools]
        # At least send_message and some domain tools should be visible
        assert "send_message" in tool_names

    @pytest.mark.asyncio
    async def test_evaluate_not_on_agent_surface(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(f"/session/{sid}/evaluate", json={})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_double_start_denied(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(f"/session/{sid}/start", json={})
        assert resp.status_code == 403


class TestNonexistentSession:
    @pytest.mark.asyncio
    async def test_start_404(self, client):
        resp = await client.post("/session/fake-id/start", json={})
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_send_404(self, client):
        resp = await client.post(
            "/session/fake-id/send", json={"text": "hello"}
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_tools_404(self, client):
        resp = await client.get("/session/fake-id/tools")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_status_404(self, client):
        resp = await client.get("/session/fake-id")
        assert resp.status_code == 404
