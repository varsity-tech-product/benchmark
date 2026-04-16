"""API tests for the full session lifecycle via REST endpoints.

Tests register → start → send → complete flow using httpx AsyncClient
against the real Starlette app (in-process, no network).
"""

import pytest
import pytest_asyncio
import httpx

from tests.helpers import (
    DEFAULT_TASK_ID,
    DEFAULT_PERSONA_ID,
    register_session,
    start_session,
    send_message,
    get_tools,
    get_session_status,
    register_and_start,
)


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


class TestRegister:
    @pytest.mark.asyncio
    async def test_register_success(self, client):
        resp = await client.post(
            "/session/register",
            json={"task_id": DEFAULT_TASK_ID, "persona_id": DEFAULT_PERSONA_ID},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["accepted"] is True
        assert "session_id" in body

    @pytest.mark.asyncio
    async def test_register_unknown_task(self, client):
        resp = await client.post(
            "/session/register", json={"task_id": "NONEXISTENT_TASK"}
        )
        assert resp.status_code == 404
        assert resp.json()["accepted"] is False

    @pytest.mark.asyncio
    async def test_register_missing_task_id(self, client):
        resp = await client.post("/session/register", json={})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_register_invalid_persona(self, client):
        resp = await client.post(
            "/session/register",
            json={"task_id": DEFAULT_TASK_ID, "persona_id": "nonexistent_persona"},
        )
        assert resp.status_code == 400
        assert resp.json()["accepted"] is False


class TestStart:
    @pytest.mark.asyncio
    async def test_start_returns_student_message(self, client):
        sid = await register_session(client)
        body = await start_session(client, sid)
        assert "student_message" in body
        assert len(body["student_message"]) > 0

    @pytest.mark.asyncio
    async def test_start_before_register_fails(self, client):
        resp = await client.post("/session/nonexistent/start", json={})
        assert resp.status_code == 404


class TestSendMessage:
    @pytest.mark.asyncio
    async def test_send_returns_student_reply(self, client):
        sid, _ = await register_and_start(client)
        body = await send_message(client, sid, "Let me look at your code.")
        assert "student_message" in body
        assert body["status"] in ("active", "completed")

    @pytest.mark.asyncio
    async def test_send_empty_text_rejected(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(
            f"/session/{sid}/send", json={"text": ""}
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_send_before_start_denied(self, client):
        sid = await register_session(client)
        resp = await client.post(
            f"/session/{sid}/send", json={"text": "hello"}
        )
        assert resp.status_code == 403
        assert "start_session" in resp.json().get("allowed", [])


class TestTools:
    @pytest.mark.asyncio
    async def test_tools_after_start(self, client):
        sid, _ = await register_and_start(client)
        tools = await get_tools(client, sid)
        tool_names = [t["name"] for t in tools]
        assert "send_message" in tool_names
        assert "get_environment_info" in tool_names

    @pytest.mark.asyncio
    async def test_tool_endpoint_blocks_session_api(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.post(
            f"/session/{sid}/tool/send_message",
            json={"text": "sneaky"},
        )
        assert resp.status_code == 400
        assert "dedicated" in resp.json()["error"]


class TestSessionStatus:
    @pytest.mark.asyncio
    async def test_status_after_register(self, client):
        sid = await register_session(client)
        body = await get_session_status(client, sid)
        assert body["phase"] == "registered"
        assert body["task_id"] == DEFAULT_TASK_ID

    @pytest.mark.asyncio
    async def test_status_after_start(self, client):
        sid, _ = await register_and_start(client)
        body = await get_session_status(client, sid)
        assert body["phase"] == "in_session"

    @pytest.mark.asyncio
    async def test_delete_session(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.delete(f"/session/{sid}")
        assert resp.status_code == 200
        assert resp.json()["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_list_sessions(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.get("/session/list")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        sids = [s["session_id"] for s in sessions]
        assert sid in sids
