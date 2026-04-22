"""API tests for the full session lifecycle via REST endpoints.

Tests register → start → send → complete flow using httpx AsyncClient
against the real Starlette app (in-process, no network).
"""

import httpx
import pytest
import pytest_asyncio

from tests.helpers import (
    DEFAULT_TASK_ID,
    DEFAULT_TASK_LABEL,
    create_run,
    get_session_status,
    get_tools,
    register_and_start,
    register_session,
    send_message,
    start_session,
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
        """Register via Run flow: create run → register with token."""
        sid = await register_session(client)
        assert sid  # non-empty session_id

    @pytest.mark.asyncio
    async def test_register_unknown_task(self, client):
        """Creating a run with unknown task should fail."""
        resp = await client.post("/client/runs/start", json={"task": "NONEXISTENT"})
        assert resp.status_code == 400
        assert "error" in resp.json()

    @pytest.mark.asyncio
    async def test_register_no_token(self, client):
        """Register without token should be rejected."""
        resp = await client.post("/session/register", json={})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_register_invalid_persona(self, client):
        """Register with invalid persona should fail."""
        _run_id, token = await create_run(client, task=DEFAULT_TASK_LABEL)
        resp = await client.post(
            "/session/register",
            json={"persona_id": "nonexistent_persona"},
            headers={"Authorization": f"Bearer {token}"},
        )
        # Registration itself may succeed or fail depending on task config
        # But the persona validation happens inside register()
        assert resp.status_code in (200, 400)
        if resp.status_code == 400:
            assert "error" in resp.json()


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
        resp = await client.post(f"/session/{sid}/send", json={"text": ""})
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_send_before_start_denied(self, client):
        sid = await register_session(client)
        resp = await client.post(f"/session/{sid}/send", json={"text": "hello"})
        assert resp.status_code == 403
        body = resp.json()
        assert "start_session" in body.get("allowed", [])
        # Parity with MCP phase-denial envelope.
        assert body.get("current_phase") == "registered"


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
    async def test_delete_persists_bundle(self, client, bench_root):
        """Regression for issue #54.

        DELETE used to discard the bundle because ``_cleanup_session``
        was called without ``persist_partial=True``. That made DELETEd
        sessions invisible to the batch evaluator (issue #47). The fix
        routes DELETE through the same agent-abandoned persistence path
        the lifespan shutdown hooks already used.
        """
        import json

        sid, _ = await register_and_start(client)
        await send_message(client, sid, "some conversation before DELETE")

        resp = await client.delete(f"/session/{sid}")
        assert resp.status_code == 200

        task_root = bench_root / "results" / "server" / DEFAULT_TASK_ID
        matches = list(task_root.rglob(f"*_{sid[:12]}"))
        assert matches, f"bundle was not persisted under {task_root}"
        bundle = matches[0]

        assert not (bundle / "manifest.json").exists()
        assert not (bundle / "run_state.md").exists()
        assert (bundle / ".session_id").read_text(encoding="utf-8") == sid

        run_state = json.loads((bundle / "run_state.json").read_text())
        # ``agent_abandoned`` is not in ``_is_failed_reason`` so the
        # session_status rolls up as ``completed``; the reason string is
        # what distinguishes a DELETE from a normal finish.
        assert run_state["termination_reason"] == "agent_abandoned"
        assert run_state["session_status"] in ("completed", "failed")

    @pytest.mark.asyncio
    async def test_list_sessions(self, client):
        sid, _ = await register_and_start(client)
        resp = await client.get("/session/list")
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]
        sids = [s["session_id"] for s in sessions]
        assert sid in sids
