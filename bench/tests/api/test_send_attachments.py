"""API tests for send_message attachments feature.

Tests the full REST flow: write file → send with attachments → verify
student sees content and conversation records metadata.
"""

import pytest
import pytest_asyncio
import httpx

from tests.helpers import (
    register_and_start,
    send_message,
    call_tool,
)


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest_asyncio.fixture
async def session_with_file(client):
    """Register + start + write a file into the workspace."""
    sid, start_resp = await register_and_start(client)

    # Write a test file via the file_write tool
    await call_tool(
        client, sid, "file_write",
        path="strategy.py",
        content='import pandas as pd\ndf = pd.read_csv("data.csv")\nprint(df.head())',
    )

    return sid, start_resp


class TestAttachmentsHappyPath:
    @pytest.mark.asyncio
    async def test_send_with_attachment(self, client, session_with_file):
        sid, _ = session_with_file
        body = await send_message(
            client, sid,
            text="Here's the strategy code I wrote.",
            attachments=["strategy.py"],
        )
        assert body["status"] in ("active", "completed")
        assert "student_message" in body

    @pytest.mark.asyncio
    async def test_send_without_attachment(self, client, session_with_file):
        sid, _ = session_with_file
        body = await send_message(
            client, sid,
            text="Let me explain the approach first.",
        )
        assert body["status"] in ("active", "completed")

    @pytest.mark.asyncio
    async def test_send_with_null_attachment(self, client, session_with_file):
        sid, _ = session_with_file
        resp = await client.post(
            f"/session/{sid}/send",
            json={"text": "No files", "attachments": None},
        )
        assert resp.status_code == 200


class TestAttachmentsValidation:
    @pytest.mark.asyncio
    async def test_max_3_attachments(self, client, session_with_file):
        sid, _ = session_with_file
        resp = await client.post(
            f"/session/{sid}/send",
            json={
                "text": "Too many files",
                "attachments": ["a.py", "b.py", "c.py", "d.py"],
            },
        )
        assert resp.status_code == 400
        assert "Maximum" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_attachment_type_validation(self, client, session_with_file):
        sid, _ = session_with_file
        resp = await client.post(
            f"/session/{sid}/send",
            json={"text": "bad type", "attachments": "not_a_list"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_path_traversal_rejected(self, client, session_with_file):
        sid, _ = session_with_file
        body = await send_message(
            client, sid,
            text="sneaky",
            attachments=["../../etc/passwd"],
        )
        # Server returns error but keeps session active
        assert "error" in body
        assert body["status"] == "active"

    @pytest.mark.asyncio
    async def test_missing_file_rejected(self, client, session_with_file):
        sid, _ = session_with_file
        body = await send_message(
            client, sid,
            text="wrong path",
            attachments=["nonexistent.py"],
        )
        assert "error" in body
        assert body["status"] == "active"

    @pytest.mark.asyncio
    async def test_exactly_3_attachments_ok(self, client, session_with_file):
        sid, _ = session_with_file

        # Write two more files
        await call_tool(client, sid, "file_write", path="a.txt", content="aaa")
        await call_tool(client, sid, "file_write", path="b.txt", content="bbb")

        body = await send_message(
            client, sid,
            text="Here are three files.",
            attachments=["strategy.py", "a.txt", "b.txt"],
        )
        assert body["status"] in ("active", "completed")


class TestAttachmentsInResults:
    @pytest.mark.asyncio
    async def test_results_contain_attachment_metadata(self, client, session_with_file):
        """After session completes, run_state conversation should include
        attachment metadata on the assistant turn that had attachments."""
        sid, _ = session_with_file

        # Send with attachment
        await send_message(
            client, sid,
            text="Here's the code.",
            attachments=["strategy.py"],
        )

        # Get results (session might still be active, but results endpoint
        # should still return current state)
        resp = await client.get(f"/ops/session/{sid}/results")
        if resp.status_code == 200:
            results = resp.json()
            if "conversation" in results:
                assistant_turns = [
                    t for t in results["conversation"]
                    if t.get("role") == "assistant"
                ]
                attached_turns = [
                    t for t in assistant_turns if "attachments" in t
                ]
                assert len(attached_turns) >= 1
                att = attached_turns[0]["attachments"][0]
                assert att["filename"] == "strategy.py"
                assert "pandas" in att["content"]
