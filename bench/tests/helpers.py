"""Factory functions and test helpers for QuantTutorBench server tests.

Provides reusable helpers for creating sessions, seeding workspace files,
and building request payloads. Modelled after backend-service/tests/helpers.py.

All session registration goes through the Run layer: create run → claim → register.
"""

import httpx

# Default task for tests — lightweight debug task, 3 personas, no LEAN needed.
DEFAULT_TASK_ID = "X01_ma_offbyone"
DEFAULT_TASK_LABEL = "X01"
DEFAULT_PERSONA_ID = "fullstack_practitioner"

# A second task for multi-task tests.
SECOND_TASK_ID = "D01_load_inspect_ohlcv"
SECOND_TASK_LABEL = "D01"


# ---------------------------------------------------------------------------
# Run + Session lifecycle helpers
# ---------------------------------------------------------------------------


async def create_run(
    client: httpx.AsyncClient,
    task: str = DEFAULT_TASK_LABEL,
) -> tuple[str, str]:
    """Create a run via /client/runs/start. Returns (run_id, token)."""
    resp = await client.post(
        "/client/runs/start",
        json={"task": task},
    )
    assert resp.status_code == 200, f"Create run failed: {resp.text}"
    body = resp.json()
    assert "run_id" in body, f"Create run failed: {body}"
    assert "token" in body, f"Create run failed: {body}"
    return body["run_id"], body["token"]


async def register_session(
    client: httpx.AsyncClient,
    task_id: str = DEFAULT_TASK_ID,
    persona_id: str | None = DEFAULT_PERSONA_ID,
) -> str:
    """Create run + register session. Returns session_id.

    Goes through the full Run flow: /client/runs/start → /session/register.
    The task_id parameter is used to derive the public label for run creation;
    if it's a full task_id like 'X01_ma_offbyone', the label 'X01' is extracted.
    """
    # Extract public label from task_id (e.g. "X01_ma_offbyone" → "X01")
    import re

    m = re.match(r"^([A-Z]\d{2})_?", task_id)
    label = m.group(1) if m else task_id

    # 1. Create run + claim
    _run_id, token = await create_run(client, task=label)

    # 2. Register session with token
    payload: dict = {}
    if persona_id:
        payload["persona_id"] = persona_id
    resp = await client.post(
        "/session/register",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 200, f"Register failed: {resp.text}"
    body = resp.json()
    assert "session_id" in body, f"Register failed: {body}"
    return body["session_id"]


async def start_session(client: httpx.AsyncClient, sid: str) -> dict:
    """Start a session and return the response body (student_message, etc.)."""
    resp = await client.post(f"/session/{sid}/start", json={})
    assert resp.status_code == 200, f"Start failed: {resp.text}"
    return resp.json()


async def send_message(
    client: httpx.AsyncClient,
    sid: str,
    text: str,
    attachments: list[str] | None = None,
) -> dict:
    """Send a message and return the response body."""
    payload: dict = {"text": text}
    if attachments is not None:
        payload["attachments"] = attachments
    resp = await client.post(f"/session/{sid}/send", json=payload)
    assert resp.status_code == 200, f"Send failed: {resp.text}"
    return resp.json()


async def get_tools(client: httpx.AsyncClient, sid: str) -> list[dict]:
    """Get available tools for a session."""
    resp = await client.get(f"/session/{sid}/tools")
    assert resp.status_code == 200, f"Tools failed: {resp.text}"
    return resp.json()["tools"]


async def call_tool(client: httpx.AsyncClient, sid: str, name: str, **kwargs) -> dict:
    """Call a domain tool and return the response body."""
    resp = await client.post(f"/session/{sid}/tool/{name}", json=kwargs)
    assert resp.status_code == 200, f"Tool call {name} failed: {resp.text}"
    return resp.json()


async def get_session_status(client: httpx.AsyncClient, sid: str) -> dict:
    """Get session status."""
    resp = await client.get(f"/session/{sid}")
    assert resp.status_code == 200, f"Status failed: {resp.text}"
    return resp.json()


async def register_and_start(
    client: httpx.AsyncClient,
    task_id: str = DEFAULT_TASK_ID,
    persona_id: str | None = DEFAULT_PERSONA_ID,
) -> tuple[str, dict]:
    """Register + start in one call. Returns (session_id, start_response)."""
    sid = await register_session(client, task_id, persona_id)
    start_resp = await start_session(client, sid)
    return sid, start_resp


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------


def seed_workspace_file(workspace_path: str, filename: str, content: str) -> str:
    """Write a file into a workspace directory. Returns the full path."""
    import os

    path = os.path.join(workspace_path, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
