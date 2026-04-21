"""Tests for async tool-job dispatch (heavy-tool 202 path + polling)."""

from __future__ import annotations

import asyncio
import json
import threading

import httpx
import pytest
import pytest_asyncio

from server.api import limits
from server.run.jobs import (
    JOB_STATUS_COMPLETED,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    JobStore,
)


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_backtest_sem():
    limits.reset_for_tests()
    yield
    limits.reset_for_tests()


async def _register_session(client: httpx.AsyncClient) -> str:
    from tests.helpers import register_session

    return await register_session(client)


# ---------------------------------------------------------------------------
# JobStore unit tests
# ---------------------------------------------------------------------------


class TestJobStore:
    def test_create_returns_pending_job(self, tmp_path):
        store = JobStore(tmp_path / "jobs")
        job = store.create("sess-1", "run_lean_backtest", {"path": "x.cs"})
        assert job["status"] == JOB_STATUS_PENDING
        assert job["session_id"] == "sess-1"
        assert job["tool_name"] == "run_lean_backtest"
        assert job["arguments"] == {"path": "x.cs"}
        assert (tmp_path / "jobs" / f"{job['job_id']}.json").exists()

    def test_update_merges_fields(self, tmp_path):
        store = JobStore(tmp_path / "jobs")
        job = store.create("sess-1", "run_backtest")
        updated = store.update(
            job["job_id"], status=JOB_STATUS_COMPLETED, result={"ok": True}
        )
        assert updated["status"] == JOB_STATUS_COMPLETED
        assert updated["result"] == {"ok": True}
        # Persisted
        reread = store.get(job["job_id"])
        assert reread["status"] == JOB_STATUS_COMPLETED

    def test_get_missing_returns_none(self, tmp_path):
        store = JobStore(tmp_path / "jobs")
        assert store.get("nope") is None

    def test_mark_orphans_failed(self, tmp_path):
        store = JobStore(tmp_path / "jobs")
        j1 = store.create("sess", "run_backtest")
        j2 = store.create("sess", "run_backtest")
        j3 = store.create("sess", "run_backtest")
        store.update(j2["job_id"], status=JOB_STATUS_RUNNING)
        store.update(j3["job_id"], status=JOB_STATUS_COMPLETED, result={"ok": True})

        failed = store.mark_orphans_failed()
        assert failed == 2  # j1 pending + j2 running

        assert store.get(j1["job_id"])["status"] == JOB_STATUS_FAILED
        assert store.get(j2["job_id"])["status"] == JOB_STATUS_FAILED
        # Terminal states untouched.
        assert store.get(j3["job_id"])["status"] == JOB_STATUS_COMPLETED
        assert "restarted" in store.get(j1["job_id"])["error"]


# ---------------------------------------------------------------------------
# HTTP integration tests
# ---------------------------------------------------------------------------


class TestHeavyToolDispatch:
    @pytest.mark.asyncio
    async def test_heavy_tool_returns_202_with_job_id(self, client, monkeypatch):
        sid = await _register_session(client)

        # Replace call_domain_tool with a fast fake so the whole flow can
        # complete in-process without touching Docker.
        manager = client._transport.app._manager

        def fake_tool(name, **kwargs):
            return json.dumps({"ok": True, "tool": name, "args": kwargs})

        state = manager.get_session(sid)
        monkeypatch.setattr(state, "call_domain_tool", fake_tool)

        # Start the session so the tool-call permission check passes.
        await client.post(f"/session/{sid}/start", json={})

        resp = await client.post(
            f"/session/{sid}/tool/run_lean_backtest",
            json={"algorithm_path": "x.cs"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["status"] == JOB_STATUS_PENDING
        assert "job_id" in body
        assert body["poll_url"].endswith(f"/tool/jobs/{body['job_id']}")

        # Poll until completion.
        job_id = body["job_id"]
        for _ in range(50):
            poll = await client.get(f"/session/{sid}/tool/jobs/{job_id}")
            assert poll.status_code == 200
            data = poll.json()
            if data["status"] == JOB_STATUS_COMPLETED:
                assert data["result"]["ok"] is True
                assert data["result"]["tool"] == "run_lean_backtest"
                return
            if data["status"] == JOB_STATUS_FAILED:
                pytest.fail(f"Job unexpectedly failed: {data['error']}")
            await asyncio.sleep(0.05)
        pytest.fail("Job never reached terminal state")

    @pytest.mark.asyncio
    async def test_heavy_tool_job_captures_exception(self, client, monkeypatch):
        sid = await _register_session(client)
        manager = client._transport.app._manager
        state = manager.get_session(sid)

        def exploding_tool(name, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(state, "call_domain_tool", exploding_tool)
        await client.post(f"/session/{sid}/start", json={})

        resp = await client.post(
            f"/session/{sid}/tool/run_lean_backtest", json={}
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        for _ in range(50):
            poll = await client.get(f"/session/{sid}/tool/jobs/{job_id}")
            data = poll.json()
            if data["status"] == JOB_STATUS_FAILED:
                assert "boom" in data["error"]
                return
            if data["status"] == JOB_STATUS_COMPLETED:
                pytest.fail("Job unexpectedly completed")
            await asyncio.sleep(0.05)
        pytest.fail("Job never reached terminal state")

    @pytest.mark.asyncio
    async def test_job_lookup_rejects_wrong_session(self, client, monkeypatch):
        sid_a = await _register_session(client)
        manager = client._transport.app._manager
        state_a = manager.get_session(sid_a)
        monkeypatch.setattr(
            state_a,
            "call_domain_tool",
            lambda name, **kw: json.dumps({"ok": True}),
        )
        await client.post(f"/session/{sid_a}/start", json={})

        resp = await client.post(
            f"/session/{sid_a}/tool/run_lean_backtest", json={}
        )
        job_id = resp.json()["job_id"]

        # Different session asking about A's job must 404.
        sid_b = await _register_session(client)
        poll = await client.get(f"/session/{sid_b}/tool/jobs/{job_id}")
        assert poll.status_code == 404

    @pytest.mark.asyncio
    async def test_non_heavy_tool_stays_synchronous(self, client, monkeypatch):
        sid = await _register_session(client)
        manager = client._transport.app._manager
        state = manager.get_session(sid)
        monkeypatch.setattr(
            state,
            "call_domain_tool",
            lambda name, **kw: json.dumps({"ok": True, "sync": True}),
        )
        await client.post(f"/session/{sid}/start", json={})

        resp = await client.post(
            f"/session/{sid}/tool/shell_exec", json={"command": "echo hi"}
        )
        assert resp.status_code == 200
        # No 202, no job_id.
        body = resp.json()
        assert body.get("sync") is True

    @pytest.mark.asyncio
    async def test_orphaned_job_still_pollable_without_session(self, client):
        """After a restart the in-memory session is gone, but the failed
        job record on disk must still be reachable so the client sees
        the terminal state instead of a generic 404."""
        manager = client._transport.app._manager
        job = manager._job_store.create(
            "sess-ghost", "run_lean_backtest", {"x": 1}
        )
        manager._job_store.update(
            job["job_id"],
            status=JOB_STATUS_FAILED,
            error="server restarted before job completed",
        )
        # Note: no session named sess-ghost exists in the manager.
        resp = await client.get(
            f"/session/sess-ghost/tool/jobs/{job['job_id']}"
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["status"] == JOB_STATUS_FAILED
        assert "restarted" in data["error"]

    @pytest.mark.asyncio
    async def test_queued_job_blocks_same_session_mutation(
        self, client, monkeypatch
    ):
        """A heavy tool accepted first must hold the session lock until
        it runs, so a later same-session write cannot race ahead and
        tear the session down before the backtest executes."""
        sid = await _register_session(client)
        manager = client._transport.app._manager
        state = manager.get_session(sid)

        order: list[str] = []
        tool_started = threading.Event()
        release_tool = threading.Event()

        def slow_tool(name, **kw):
            order.append("tool_start")
            tool_started.set()
            # Block until the test lets the backtest finish. Runs on the
            # to_thread worker, so a threading.Event (not asyncio.Event)
            # is required.
            release_tool.wait(timeout=5.0)
            order.append("tool_end")
            return json.dumps({"ok": True})

        monkeypatch.setattr(state, "call_domain_tool", slow_tool)
        await client.post(f"/session/{sid}/start", json={})

        # 1. Accept a heavy tool — returns 202 immediately, job queued.
        resp = await client.post(
            f"/session/{sid}/tool/run_lean_backtest", json={}
        )
        assert resp.status_code == 202
        job_id = resp.json()["job_id"]

        # Wait until the background worker is actually inside the tool
        # (it already holds state._request_lock at this point).
        await asyncio.get_event_loop().run_in_executor(
            None, tool_started.wait, 2.0
        )
        assert tool_started.is_set(), "background worker never started"

        # 2. Fire a mutating request concurrently. It must wait for the
        #    running backtest to release the session lock.
        async def later_send():
            order.append("send_issued")
            r = await client.post(f"/session/{sid}/send", json={"text": "hi"})
            order.append(f"send_done:{r.status_code}")

        send_task = asyncio.create_task(later_send())
        await asyncio.sleep(0.1)  # give send a chance to block on the lock
        assert not any(s.startswith("send_done") for s in order), (
            "send completed before backtest released the lock"
        )

        # Release the backtest and wait for both to finish.
        release_tool.set()
        for _ in range(200):
            poll = await client.get(f"/session/{sid}/tool/jobs/{job_id}")
            if poll.json()["status"] in (JOB_STATUS_COMPLETED, JOB_STATUS_FAILED):
                break
            await asyncio.sleep(0.02)
        await asyncio.wait_for(send_task, timeout=5.0)

        assert "tool_end" in order
        send_done_idx = next(
            i for i, v in enumerate(order) if v.startswith("send_done")
        )
        assert order.index("tool_end") < send_done_idx
