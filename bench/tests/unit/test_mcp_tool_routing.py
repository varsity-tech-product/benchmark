"""Unit tests for MCP tool routing via SessionState.handle_tool_call.

Tests the MCP path directly by calling handle_tool_call(name, arguments)
on a SessionState instance — no HTTP transport, no httpx client.
"""

import json

import pytest

from tests.helpers import DEFAULT_PERSONA_ID, DEFAULT_TASK_ID

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def session_state(bench_root):
    """Create a SessionState for direct handle_tool_call testing."""
    from server.api.session_api import SessionState

    state = SessionState(
        session_id="test-mcp-session-001",
        use_docker=False,
        bench_root=bench_root,
        eval_model="fake-model",
        auto_eval=False,
    )
    return state


async def _call(state, name: str, arguments: dict | None = None) -> dict:
    """Call handle_tool_call and parse the first TextContent as JSON."""
    results = await state.handle_tool_call(name, arguments or {})
    text = results[0].text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"_raw": text}


async def _register(state, task_id=DEFAULT_TASK_ID, persona_id=DEFAULT_PERSONA_ID):
    return await _call(
        state,
        "register_session",
        {
            "task_id": task_id,
            "persona_id": persona_id,
        },
    )


async def _start(state):
    return await _call(state, "start_session", {})


async def _send(state, text="Let me look at the code.", attachments=None):
    args = {"text": text}
    if attachments is not None:
        args["attachments"] = attachments
    return await _call(state, "send_message", args)


# ---------------------------------------------------------------------------
# Register routing
# ---------------------------------------------------------------------------


class TestMCPRegister:
    @pytest.mark.asyncio
    async def test_register_success(self, session_state):
        result = await _register(session_state)
        assert "session_id" in result

    @pytest.mark.asyncio
    async def test_register_bad_task(self, session_state):
        result = await _register(session_state, task_id="NONEXISTENT")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_register_wrong_phase(self, session_state):
        await _register(session_state)
        # Already registered — should be denied
        result = await _register(session_state)
        assert "error" in result


# ---------------------------------------------------------------------------
# Start routing
# ---------------------------------------------------------------------------


class TestMCPStart:
    @pytest.mark.asyncio
    async def test_start_returns_student_message(self, session_state):
        await _register(session_state)
        result = await _start(session_state)
        assert "student_message" in result
        assert len(result["student_message"]) > 0

    @pytest.mark.asyncio
    async def test_start_before_register_denied(self, session_state):
        result = await _start(session_state)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_double_start_denied(self, session_state):
        await _register(session_state)
        await _start(session_state)
        result = await _start(session_state)
        assert "error" in result


# ---------------------------------------------------------------------------
# Send message routing
# ---------------------------------------------------------------------------


class TestMCPSendMessage:
    @pytest.mark.asyncio
    async def test_send_returns_student_reply(self, session_state):
        await _register(session_state)
        await _start(session_state)
        result = await _send(session_state)
        assert "student_message" in result
        assert result["status"] in ("active", "completed")

    @pytest.mark.asyncio
    async def test_send_before_start_denied(self, session_state):
        await _register(session_state)
        result = await _send(session_state)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_send_before_register_denied(self, session_state):
        result = await _send(session_state)
        assert "error" in result


# ---------------------------------------------------------------------------
# Send message attachment validation (MCP path)
# ---------------------------------------------------------------------------


class TestMCPAttachmentValidation:
    @pytest.mark.asyncio
    async def test_attachment_type_must_be_list(self, session_state):
        await _register(session_state)
        await _start(session_state)
        result = await _send(session_state, attachments="not_a_list")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_attachment_max_3(self, session_state):
        await _register(session_state)
        await _start(session_state)
        result = await _send(
            session_state,
            attachments=["a.py", "b.py", "c.py", "d.py"],
        )
        assert "error" in result
        assert "Maximum" in result["error"] or "3" in result["error"]

    @pytest.mark.asyncio
    async def test_empty_attachments_ok(self, session_state):
        await _register(session_state)
        await _start(session_state)
        result = await _send(session_state, attachments=[])
        assert result["status"] in ("active", "completed")


# ---------------------------------------------------------------------------
# Get background
# ---------------------------------------------------------------------------


class TestMCPGetBackground:
    @pytest.mark.asyncio
    async def test_get_background_in_session(self, session_state):
        await _register(session_state)
        await _start(session_state)
        results = await session_state.handle_tool_call("get_background", {})
        text = results[0].text
        assert "tutoring environment" in text.lower() or "send_message" in text

    @pytest.mark.asyncio
    async def test_get_background_before_start_denied(self, session_state):
        await _register(session_state)
        result = await _call(session_state, "get_background")
        assert "error" in result


# ---------------------------------------------------------------------------
# Eval routing
# ---------------------------------------------------------------------------


class TestMCPEvalRouting:
    @pytest.mark.asyncio
    async def test_eval_denied_in_session(self, session_state):
        await _register(session_state)
        await _start(session_state)
        result = await _call(session_state, "request_evaluation")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_get_results_in_session_returns_error_or_empty(self, session_state):
        """get_results/get_scores route through MCP even in IN_SESSION
        (not blocked by SESSION_API_TOOLS). They return data-level errors
        (no results yet) rather than permission errors."""
        await _register(session_state)
        await _start(session_state)
        result = await _call(session_state, "get_results")
        # No results saved yet — returns error at data level
        assert "error" in result or "Results" in str(result)

    @pytest.mark.asyncio
    async def test_get_scores_in_session_returns_pending(self, session_state):
        await _register(session_state)
        await _start(session_state)
        result = await _call(session_state, "get_scores")
        assert result.get("status") == "pending"


# ---------------------------------------------------------------------------
# Domain tool routing
# ---------------------------------------------------------------------------


class TestMCPDomainTools:
    @pytest.mark.asyncio
    async def test_domain_tool_routes_through_proxy(self, session_state):
        await _register(session_state)
        await _start(session_state)
        result = await _call(
            session_state,
            "get_environment_info",
            {},
        )
        # get_environment_info should return some text, not an error
        raw = result.get("_raw", "")
        assert "error" not in result or raw

    @pytest.mark.asyncio
    async def test_domain_tool_denied_before_start(self, session_state):
        await _register(session_state)
        result = await _call(session_state, "shell_exec", {"command": "ls"})
        assert "error" in result


# ---------------------------------------------------------------------------
# Closed session
# ---------------------------------------------------------------------------


class TestMCPClosedSession:
    @pytest.mark.asyncio
    async def test_closed_session_returns_error(self, session_state):
        await _register(session_state)
        await _start(session_state)
        session_state._closed = True
        result = await _send(session_state)
        assert "error" in result or "closed" in str(result).lower()


# ---------------------------------------------------------------------------
# Phase transitions
# ---------------------------------------------------------------------------


class TestMCPPhaseTransitions:
    @pytest.mark.asyncio
    async def test_full_lifecycle_phases(self, session_state):
        from server.api.protocol import SessionPhase

        assert session_state.phase == SessionPhase.UNREGISTERED

        await _register(session_state)
        assert session_state.phase == SessionPhase.REGISTERED

        await _start(session_state)
        assert session_state.phase == SessionPhase.IN_SESSION

    @pytest.mark.asyncio
    async def test_visible_tools_change_per_phase(self, session_state):
        # UNREGISTERED — only register
        tools = session_state.get_visible_tools()
        names = [t.name for t in tools]
        assert "register_session" in names
        assert "send_message" not in names

        await _register(session_state)
        # REGISTERED — only start
        tools = session_state.get_visible_tools()
        names = [t.name for t in tools]
        assert "start_session" in names
        assert "send_message" not in names

        await _start(session_state)
        # IN_SESSION — send_message + domain tools
        tools = session_state.get_visible_tools()
        names = [t.name for t in tools]
        assert "send_message" in names
        assert "register_session" not in names
