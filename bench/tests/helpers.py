"""Factory functions and test helpers for QuantTutorBench server tests.

Provides reusable helpers for creating sessions, seeding workspace files,
and building request payloads. Modelled after backend-service/tests/helpers.py.
"""

import httpx

# Default task for tests — lightweight debug task, 3 personas, no LEAN needed.
DEFAULT_TASK_ID = "X01_ma_offbyone"
DEFAULT_PERSONA_ID = "intermediate_developer"

# A second task for multi-task tests.
SECOND_TASK_ID = "D01_load_inspect_ohlcv"


# ---------------------------------------------------------------------------
# Session lifecycle helpers
# ---------------------------------------------------------------------------


async def register_session(
    client: httpx.AsyncClient,
    task_id: str = DEFAULT_TASK_ID,
    persona_id: str | None = DEFAULT_PERSONA_ID,
) -> str:
    """Register a new session and return the session_id.

    Raises AssertionError if registration fails.
    """
    payload: dict = {"task_id": task_id}
    if persona_id:
        payload["persona_id"] = persona_id
    resp = await client.post("/session/register", json=payload)
    assert resp.status_code == 200, f"Register failed: {resp.text}"
    body = resp.json()
    assert body.get("accepted") is True, f"Register not accepted: {body}"
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


async def call_tool(
    client: httpx.AsyncClient, sid: str, name: str, **kwargs
) -> dict:
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


def seed_workspace_file(
    workspace_path: str, filename: str, content: str
) -> str:
    """Write a file into a workspace directory. Returns the full path."""
    import os

    path = os.path.join(workspace_path, filename)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
