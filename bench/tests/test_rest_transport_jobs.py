"""Client-side test for the REST transport's async job polling path."""

from __future__ import annotations

import json

import httpx
import pytest

from client.transports import rest_transport
from client.transports.rest_transport import RESTTransport


@pytest.mark.asyncio
async def test_call_tool_polls_job_until_complete(monkeypatch):
    # Keep the test fast regardless of the production poll interval.
    monkeypatch.setattr(rest_transport, "_JOB_POLL_INTERVAL_S", 0.01)

    calls = {"poll_count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/tool/run_lean_backtest") and request.method == "POST":
            return httpx.Response(
                202,
                json={
                    "job_id": "job-abc",
                    "status": "pending",
                    "poll_url": "/session/s1/tool/jobs/job-abc",
                },
            )
        if request.url.path.endswith("/tool/jobs/job-abc"):
            calls["poll_count"] += 1
            if calls["poll_count"] < 3:
                return httpx.Response(200, json={"status": "running"})
            return httpx.Response(
                200,
                json={
                    "status": "completed",
                    "result": {"sharpe": 1.23, "trades": 85},
                },
            )
        return httpx.Response(404)

    transport = RESTTransport()
    transport._base_url = "http://test"
    transport._session_id = "s1"
    transport._client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    try:
        raw = await transport.call_tool("run_lean_backtest", {"path": "x.cs"})
    finally:
        await transport._client.aclose()

    payload = json.loads(raw)
    assert payload == {"sharpe": 1.23, "trades": 85}
    assert calls["poll_count"] == 3


@pytest.mark.asyncio
async def test_call_tool_surfaces_job_failure(monkeypatch):
    monkeypatch.setattr(rest_transport, "_JOB_POLL_INTERVAL_S", 0.01)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            return httpx.Response(
                202, json={"job_id": "job-fail", "status": "pending"}
            )
        return httpx.Response(
            200,
            json={"status": "failed", "error": "RuntimeError: boom"},
        )

    transport = RESTTransport()
    transport._base_url = "http://test"
    transport._session_id = "s1"
    transport._client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    try:
        raw = await transport.call_tool("run_backtest", {})
    finally:
        await transport._client.aclose()
    payload = json.loads(raw)
    assert "boom" in payload["error"]


@pytest.mark.asyncio
async def test_call_tool_sync_200_passthrough():
    # Non-heavy tools still return 200 directly; no polling.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "sync": True})

    transport = RESTTransport()
    transport._base_url = "http://test"
    transport._session_id = "s1"
    transport._client = httpx.AsyncClient(
        base_url="http://test", transport=httpx.MockTransport(handler)
    )
    try:
        raw = await transport.call_tool("shell_exec", {"command": "echo"})
    finally:
        await transport._client.aclose()
    payload = json.loads(raw)
    assert payload == {"ok": True, "sync": True}
