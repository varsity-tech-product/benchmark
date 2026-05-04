"""API coverage for active run replay and resume."""

import json
from pathlib import Path

import httpx
import pytest
import pytest_asyncio

from tests.helpers import (
    DEFAULT_PERSONA_ID,
    DEFAULT_TASK_ID,
    DEFAULT_TASK_LABEL,
    create_run,
)


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


async def _register_start_send(client, token: str) -> str:
    resp = await client.post(
        "/session/register",
        json={"persona_id": DEFAULT_PERSONA_ID},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, resp.text
    sid = resp.json()["session_id"]

    resp = await client.post(f"/session/{sid}/start", json={})
    assert resp.status_code == 200, resp.text

    resp = await client.post(
        f"/session/{sid}/send",
        json={"text": "Let me inspect the moving average bug."},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "active"
    return sid


@pytest.mark.asyncio
async def test_active_run_state_and_replay_endpoint(client, bench_root):
    run_id, token = await create_run(client, task=DEFAULT_TASK_LABEL)
    sid = await _register_start_send(client, token)

    state_path = Path(bench_root) / "results" / "runs" / run_id / "run_state.json"
    assert state_path.exists()
    active_state = json.loads(state_path.read_text(encoding="utf-8"))
    assert active_state["run_id"] == run_id
    assert active_state["session_id"] == sid
    assert active_state["task_id"] == DEFAULT_TASK_ID
    assert active_state["phase"] == "in_session"
    assert active_state["turn_count"] == 1
    assert active_state["conversation"]
    assert any(log["name"] == "send_message" for log in active_state["tool_logs"])

    replay = await client.get(
        f"/api/runs/{run_id}/replay",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert replay.status_code == 200, replay.text
    replay_body = replay.json()
    assert replay_body["session_id"] == sid
    assert replay_body["turn_count"] == 1
    assert replay_body["conversation"] == active_state["conversation"]

    state = await client.get(
        f"/api/runs/{run_id}/state",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert state.status_code == 200, state.text
    state_body = state.json()
    assert state_body["run_status"] == "active"
    assert state_body["phase"] == "in_session"
    assert state_body["turn_count"] == 1


@pytest.mark.asyncio
async def test_resume_suspended_active_run_continues_send(client, app):
    run_id, token = await create_run(client, task=DEFAULT_TASK_LABEL)
    sid = await _register_start_send(client, token)

    manager = app._manager
    active_state = manager.get_session(sid)
    assert active_state is not None
    workspace_file = Path(active_state.container.workspace_path) / "resume_note.txt"
    workspace_file.write_text("preserve this workspace file", encoding="utf-8")

    await manager._cleanup_session(sid, suspend_active=True)
    assert manager.get_session(sid) is None
    assert manager._run_service.get_run(run_id).status.value == "active"

    state_path = (
        Path(manager.bench_root) / "results" / "runs" / run_id / "run_state.json"
    )
    saved_state = json.loads(state_path.read_text(encoding="utf-8"))
    saved_state["simulator_cost"] = 1.25
    state_path.write_text(json.dumps(saved_state), encoding="utf-8")

    resumed = await client.post(
        f"/api/runs/{run_id}/resume",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["session_id"] == sid
    assert body["phase"] == "in_session"
    resumed_state = manager.get_session(sid)
    assert resumed_state is not None
    resumed_workspace_file = (
        Path(resumed_state.container.workspace_path) / "resume_note.txt"
    )
    assert resumed_workspace_file.read_text(encoding="utf-8") == (
        "preserve this workspace file"
    )
    assert resumed_state.user_sim.total_cost == 1.25

    continued = await client.post(
        f"/session/{sid}/send",
        json={"text": "I found the rolling window issue."},
    )
    assert continued.status_code == 200, continued.text
    assert continued.json()["status"] in ("active", "completed")
