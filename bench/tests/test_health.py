"""Tests for /health endpoint and backtest concurrency cap."""

from __future__ import annotations

import asyncio
import subprocess
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio

from server.api import limits


@pytest_asyncio.fixture
async def client(app):
    # Docker mode: the /health endpoint runs its full check matrix.
    app._manager.use_docker = True
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def client_no_docker(app):
    # Local dev mode: Docker probes are skipped.
    app._manager.use_docker = False
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture(autouse=True)
def _reset_backtest_sem():
    """Drop the cached semaphore so each test starts with a fresh one
    bound to the current event loop."""
    limits.reset_for_tests()
    yield
    limits.reset_for_tests()


def _fake_proc(returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=b"", stderr=b"")


class TestHealth:
    @pytest.mark.asyncio
    async def test_ok_when_all_checks_pass(self, client):
        with (
            patch("server.api.http_app.subprocess.run", return_value=_fake_proc(0)),
            patch(
                "server.api.http_app.shutil.disk_usage",
                return_value=type("Usage", (), {"total": 100 * 1024**3, "used": 10 * 1024**3, "free": 90 * 1024**3})(),
            ),
        ):
            resp = await client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"]["docker"]["ok"] is True
        assert body["checks"]["lean_image"]["ok"] is True
        assert body["checks"]["bench_images"]["ok"] is True
        assert body["checks"]["disk"]["ok"] is True

    @pytest.mark.asyncio
    async def test_down_when_docker_missing(self, client):
        def fake_run(cmd, *args, **kwargs):
            if cmd[0] == "docker":
                raise FileNotFoundError("docker not found")
            return _fake_proc(0)

        with (
            patch("server.api.http_app.subprocess.run", side_effect=fake_run),
            patch(
                "server.api.http_app.shutil.disk_usage",
                return_value=type("Usage", (), {"total": 100 * 1024**3, "used": 10 * 1024**3, "free": 90 * 1024**3})(),
            ),
        ):
            resp = await client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "down"
        assert body["checks"]["docker"]["ok"] is False

    @pytest.mark.asyncio
    async def test_down_when_lean_image_missing(self, client):
        def fake_run(cmd, *args, **kwargs):
            # docker info OK, image inspect fails
            if len(cmd) >= 3 and cmd[1] == "image":
                return _fake_proc(1)
            return _fake_proc(0)

        with (
            patch("server.api.http_app.subprocess.run", side_effect=fake_run),
            patch(
                "server.api.http_app.shutil.disk_usage",
                return_value=type("Usage", (), {"total": 100 * 1024**3, "used": 10 * 1024**3, "free": 90 * 1024**3})(),
            ),
        ):
            resp = await client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["checks"]["docker"]["ok"] is True
        assert body["checks"]["lean_image"]["ok"] is False
        assert body["checks"]["bench_images"]["ok"] is False

    @pytest.mark.asyncio
    async def test_down_when_disk_low(self, client):
        with (
            patch("server.api.http_app.subprocess.run", return_value=_fake_proc(0)),
            patch(
                "server.api.http_app.shutil.disk_usage",
                return_value=type("Usage", (), {"total": 100 * 1024**3, "used": 98 * 1024**3, "free": 2 * 1024**3})(),
            ),
        ):
            resp = await client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["checks"]["disk"]["ok"] is False
        assert body["checks"]["disk"]["free_gb"] < 5.0

    @pytest.mark.asyncio
    async def test_health_does_not_require_auth(self, client):
        # No Authorization header; should still respond.
        with (
            patch("server.api.http_app.subprocess.run", return_value=_fake_proc(0)),
            patch(
                "server.api.http_app.shutil.disk_usage",
                return_value=type("Usage", (), {"total": 100 * 1024**3, "used": 10 * 1024**3, "free": 90 * 1024**3})(),
            ),
        ):
            resp = await client.get("/health")
        assert resp.status_code in (200, 503)

    @pytest.mark.asyncio
    async def test_no_docker_mode_skips_docker_probes(self, client_no_docker):
        # In --no-docker mode the endpoint should not fail just because
        # Docker is missing; only disk is checked.
        with (
            patch(
                "server.api.http_app.subprocess.run",
                side_effect=FileNotFoundError("docker not found"),
            ),
            patch(
                "server.api.http_app.shutil.disk_usage",
                return_value=type("Usage", (), {"total": 100 * 1024**3, "used": 10 * 1024**3, "free": 90 * 1024**3})(),
            ),
        ):
            resp = await client_no_docker.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "docker" not in body["checks"]
        assert "lean_image" not in body["checks"]
        assert "bench_images" not in body["checks"]
        assert body["checks"]["disk"]["ok"] is True


class TestBacktestSemaphore:
    @pytest.mark.asyncio
    async def test_semaphore_is_lazy_and_shared(self):
        sem1 = limits.backtest_sem()
        sem2 = limits.backtest_sem()
        assert sem1 is sem2

    @pytest.mark.asyncio
    async def test_semaphore_caps_concurrency(self, monkeypatch):
        # Env var set AFTER module import (mirrors load_server_env order)
        # must still be honoured because backtest_sem() reads it lazily.
        monkeypatch.setenv("QTB_MAX_CONCURRENT_BACKTESTS", "2")
        sem = limits.backtest_sem()

        in_flight = 0
        peak = 0

        async def fake_backtest():
            nonlocal in_flight, peak
            async with sem:
                in_flight += 1
                peak = max(peak, in_flight)
                await asyncio.sleep(0.05)
                in_flight -= 1

        await asyncio.gather(*(fake_backtest() for _ in range(5)))
        assert peak == 2, f"expected at most 2 concurrent, saw {peak}"

    def test_max_concurrent_reads_env_lazily(self, monkeypatch):
        monkeypatch.setenv("QTB_MAX_CONCURRENT_BACKTESTS", "7")
        assert limits._max_concurrent() == 7
        monkeypatch.setenv("QTB_MAX_CONCURRENT_BACKTESTS", "0")
        # Floor of 1 to keep the semaphore usable.
        assert limits._max_concurrent() == 1

    def test_heavy_tools_set_includes_known_backtest_tools(self):
        assert "run_lean_backtest" in limits.HEAVY_TOOLS
        assert "run_backtest" in limits.HEAVY_TOOLS
