"""Regression coverage for MCP request-bound disconnect cleanup."""

import anyio
import pytest

from server.api.http_app import BenchSessionManager
from server.api.protocol import SessionPhase


class _ReceiveOnceTransport:
    async def handle_request(self, scope, receive, send):
        await receive()


class _DisconnectThenRaiseTransport:
    async def handle_request(self, scope, receive, send):
        await receive()
        raise RuntimeError("transport closed")


class _TerminableTransport:
    def __init__(self):
        self.terminated = False

    async def terminate(self):
        self.terminated = True


class _FakeSessionState:
    def __init__(self):
        self.run_id = ""
        self.phase = SessionPhase.UNREGISTERED
        self._request_lock = anyio.Lock()
        self.cleanup_calls = []

    def cleanup(self, *, persist_partial=False):
        self.cleanup_calls.append(persist_partial)


class _FakeRunStatus:
    value = "active"


class _FakeRun:
    status = _FakeRunStatus()


class _FakeRunService:
    def __init__(self):
        self.failed = []

    def get_run(self, run_id):
        return _FakeRun()

    def mark_failed(self, run_id, error):
        self.failed.append((run_id, error))


@pytest.mark.asyncio
async def test_mcp_http_disconnect_persists_partial_cleanup(tmp_path):
    manager = BenchSessionManager(use_docker=False, bench_root=tmp_path)
    manager._sessions["session-1"] = object()
    calls = []

    async def fake_cleanup(session_id, *, persist_partial=False):
        calls.append((session_id, persist_partial))
        manager._sessions.pop(session_id, None)

    manager._cleanup_session = fake_cleanup

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        return None

    await manager._handle_mcp_transport_request(
        _ReceiveOnceTransport(),
        "session-1",
        {"type": "http", "method": "POST", "path": "/mcp"},
        receive,
        send,
    )

    assert calls == [("session-1", True)]


@pytest.mark.asyncio
async def test_mcp_get_disconnect_keeps_session_open(tmp_path):
    manager = BenchSessionManager(use_docker=False, bench_root=tmp_path)
    manager._sessions["session-1"] = object()
    calls = []

    async def fake_cleanup(session_id, *, persist_partial=False):
        calls.append((session_id, persist_partial))

    manager._cleanup_session = fake_cleanup

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        return None

    await manager._handle_mcp_transport_request(
        _ReceiveOnceTransport(),
        "session-1",
        {"type": "http", "method": "GET", "path": "/mcp"},
        receive,
        send,
    )

    assert calls == []
    assert "session-1" in manager._sessions


@pytest.mark.asyncio
async def test_mcp_http_disconnect_cleanup_runs_when_transport_raises(tmp_path):
    manager = BenchSessionManager(use_docker=False, bench_root=tmp_path)
    manager._sessions["session-1"] = object()
    calls = []

    async def fake_cleanup(session_id, *, persist_partial=False):
        calls.append((session_id, persist_partial))
        manager._sessions.pop(session_id, None)

    manager._cleanup_session = fake_cleanup

    async def receive():
        return {"type": "http.disconnect"}

    async def send(_message):
        return None

    with pytest.raises(RuntimeError, match="transport closed"):
        await manager._handle_mcp_transport_request(
            _DisconnectThenRaiseTransport(),
            "session-1",
            {"type": "http", "method": "POST", "path": "/mcp"},
            receive,
            send,
        )

    assert calls == [("session-1", True)]


@pytest.mark.asyncio
async def test_mcp_regular_request_keeps_session_open(tmp_path):
    manager = BenchSessionManager(use_docker=False, bench_root=tmp_path)
    manager._sessions["session-1"] = object()
    calls = []

    async def fake_cleanup(session_id, *, persist_partial=False):
        calls.append((session_id, persist_partial))

    manager._cleanup_session = fake_cleanup

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(_message):
        return None

    await manager._handle_mcp_transport_request(
        _ReceiveOnceTransport(),
        "session-1",
        {"type": "http", "method": "POST", "path": "/mcp"},
        receive,
        send,
    )

    assert calls == []


@pytest.mark.asyncio
async def test_cleanup_session_terminates_transport_before_partial_cleanup(tmp_path):
    manager = BenchSessionManager(use_docker=False, bench_root=tmp_path)
    state = _FakeSessionState()
    transport = _TerminableTransport()
    manager._sessions["session-1"] = state
    manager._transports["session-1"] = transport

    await manager._cleanup_session("session-1", persist_partial=True)

    assert transport.terminated is True
    assert state.cleanup_calls == [True]
    assert "session-1" not in manager._sessions
    assert "session-1" not in manager._transports


@pytest.mark.asyncio
async def test_cleanup_waits_for_inflight_request_before_marking_failed(tmp_path):
    manager = BenchSessionManager(use_docker=False, bench_root=tmp_path)
    run_service = _FakeRunService()
    manager._run_service = run_service
    state = _FakeSessionState()
    state.run_id = "run-1"
    state.phase = SessionPhase.IN_SESSION
    transport = _TerminableTransport()
    manager._sessions["session-1"] = state
    manager._transports["session-1"] = transport

    await state._request_lock.acquire()

    async def cleanup():
        await manager._cleanup_session("session-1", persist_partial=True)

    async with anyio.create_task_group() as tg:
        tg.start_soon(cleanup)
        await anyio.sleep(0)
        assert run_service.failed == []
        state.phase = SessionPhase.COMPLETED
        state._request_lock.release()

    assert run_service.failed == []
    assert transport.terminated is True
    assert state.cleanup_calls == [True]
