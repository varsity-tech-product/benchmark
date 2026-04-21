"""REST transport — plain HTTP calls to /session/* endpoints.

Validates that the server's REST API is fully functional as an
alternative to MCP. Uses httpx for async HTTP.
"""

import asyncio
import json
import logging
from typing import Optional

import httpx

from .base import SessionTransport

logger = logging.getLogger(__name__)

# Poll cadence for async tool jobs (heavy tools return 202 + job_id).
_JOB_POLL_INTERVAL_S = 5.0
# Upper bound so a wedged server cannot hang the client forever; the
# server's own tool timeout (600s for backtests) will terminate the job
# well before this.
_JOB_POLL_MAX_S = 900.0


class RESTTransport(SessionTransport):
    """Plain HTTP REST transport."""

    def __init__(self):
        self._client: Optional[httpx.AsyncClient] = None
        self._base_url: str = ""
        self._session_id: Optional[str] = None
        self._headers: dict = {}

    async def connect(self, url: str, headers: Optional[dict] = None) -> None:
        """Connect to the server. URL should be the base URL (e.g. http://localhost:8000)."""
        self._base_url = url.rstrip("/")
        self._headers = dict(headers or {})
        self._client = httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=600.0,  # generous for long tools like run_backtest
        )
        logger.info("REST connected to %s", self._base_url)

    async def register_session(self, task_id: str = "", persona_id: str = "") -> dict:
        payload = {}
        if task_id:
            payload["task_id"] = task_id
        if persona_id:
            payload["persona_id"] = persona_id

        resp = await self._client.post("/session/register", json=payload)
        data = resp.json()

        if resp.status_code != 200:
            return {"error": data.get("error", f"HTTP {resp.status_code}")}

        if data.get("session_id"):
            self._session_id = data["session_id"]
        return data

    async def start_session(self) -> dict:
        resp = await self._client.post(f"/session/{self._session_id}/start", json={})
        return resp.json()

    async def list_tools(self) -> list[dict]:
        resp = await self._client.get(f"/session/{self._session_id}/tools")
        data = resp.json()
        tools = data.get("tools", [])
        # Filter protocol tools (same as MCP transport)
        protocol = {
            "register_session",
            "start_session",
        }
        # Normalize key names: REST returns "inputSchema", adapter expects "input_schema"
        normalized = []
        for t in tools:
            if t["name"] in protocol:
                continue
            tool = dict(t)
            if "inputSchema" in tool and "input_schema" not in tool:
                tool["input_schema"] = tool.pop("inputSchema")
            normalized.append(tool)
        return normalized

    async def call_tool(self, name: str, arguments: dict) -> str:
        # send_message has a dedicated endpoint (blocked on /tool/send_message)
        if name == "send_message":
            result = await self.send_message(
                text=arguments.get("text", ""),
                attachments=arguments.get("attachments"),
                reasoning=arguments.get("reasoning"),
            )
            return json.dumps(result)

        resp = await self._client.post(
            f"/session/{self._session_id}/tool/{name}", json=arguments
        )

        # Heavy tools (run_backtest, run_lean_backtest) return 202 with a
        # job_id to avoid holding a 10-minute HTTP connection open. Poll
        # until the job reaches a terminal state, then return its result
        # as if the call had been synchronous.
        if resp.status_code == 202:
            data = resp.json()
            job_id = data.get("job_id")
            if not job_id:
                return json.dumps(data)
            return await self._await_job(job_id)

        data = resp.json()
        # REST returns parsed JSON directly; convert back to string for adapter
        return json.dumps(data)

    async def _await_job(self, job_id: str) -> str:
        """Poll an async tool job until it completes and return its payload."""
        url = f"/session/{self._session_id}/tool/jobs/{job_id}"
        deadline = asyncio.get_event_loop().time() + _JOB_POLL_MAX_S
        while True:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                return json.dumps(
                    {"error": f"Job poll failed: HTTP {resp.status_code}"}
                )
            data = resp.json()
            status = data.get("status")
            if status == "completed":
                return json.dumps(data.get("result") or {})
            if status == "failed":
                return json.dumps(
                    {"error": data.get("error") or "job failed"}
                )
            if asyncio.get_event_loop().time() >= deadline:
                return json.dumps(
                    {
                        "error": (
                            f"Client gave up polling job {job_id} after "
                            f"{_JOB_POLL_MAX_S:.0f}s"
                        )
                    }
                )
            await asyncio.sleep(_JOB_POLL_INTERVAL_S)

    async def send_message(
        self,
        text: str,
        attachments: Optional[list] = None,
        reasoning: Optional[str] = None,
    ) -> dict:
        payload = {"text": text}
        if attachments:
            payload["attachments"] = attachments
        if reasoning:
            payload["reasoning"] = reasoning
        resp = await self._client.post(
            f"/session/{self._session_id}/send", json=payload
        )
        return resp.json()

    async def close(self) -> None:
        if self._client and self._session_id:
            try:
                await self._client.delete(f"/session/{self._session_id}")
            except Exception:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id
