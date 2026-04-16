"""Unit tests for the session permission state machine.

Tests check_permission() logic in isolation — no server needed.
"""

import pytest

from server.api.protocol import (
    SESSION_API_TOOLS,
    SessionPhase,
    check_permission,
)


class TestUnregisteredPhase:
    def test_register_allowed(self):
        ok, err, ops = check_permission(SessionPhase.UNREGISTERED, "register_session")
        assert ok is True

    @pytest.mark.parametrize("tool", [
        "start_session", "send_message", "request_evaluation",
        "read_file", "shell_exec",
    ])
    def test_everything_else_denied(self, tool):
        ok, err, ops = check_permission(SessionPhase.UNREGISTERED, tool)
        assert ok is False
        assert "register_session" in ops


class TestRegisteredPhase:
    def test_start_allowed(self):
        ok, err, ops = check_permission(SessionPhase.REGISTERED, "start_session")
        assert ok is True

    @pytest.mark.parametrize("tool", [
        "register_session", "send_message", "request_evaluation",
        "read_file", "shell_exec",
    ])
    def test_everything_else_denied(self, tool):
        ok, err, ops = check_permission(SessionPhase.REGISTERED, tool)
        assert ok is False
        assert "start_session" in ops


class TestInSessionPhase:
    def test_send_message_allowed(self):
        ok, err, ops = check_permission(SessionPhase.IN_SESSION, "send_message")
        assert ok is True

    def test_domain_tools_allowed(self):
        for tool in ("read_file", "shell_exec", "file_write", "get_environment_info"):
            ok, err, ops = check_permission(SessionPhase.IN_SESSION, tool)
            assert ok is True, f"{tool} should be allowed in IN_SESSION"

    @pytest.mark.parametrize("tool", [
        "register_session", "start_session", "request_evaluation",
    ])
    def test_session_api_tools_denied(self, tool):
        ok, err, ops = check_permission(SessionPhase.IN_SESSION, tool)
        assert ok is False


class TestCompletedPhase:
    @pytest.mark.parametrize("tool", [
        "request_evaluation", "get_results", "get_scores",
    ])
    def test_completed_tools_allowed(self, tool):
        ok, err, ops = check_permission(SessionPhase.COMPLETED, tool)
        assert ok is True

    @pytest.mark.parametrize("tool", [
        "register_session", "start_session", "send_message",
        "read_file", "shell_exec",
    ])
    def test_everything_else_denied(self, tool):
        ok, err, ops = check_permission(SessionPhase.COMPLETED, tool)
        assert ok is False


class TestToolSchema:
    def test_send_message_has_attachments_field(self):
        from server.api.protocol import SEND_MESSAGE_TOOL

        props = SEND_MESSAGE_TOOL.inputSchema["properties"]
        assert "attachments" in props
        assert props["attachments"]["type"] == "array"
        assert props["attachments"]["maxItems"] == 3

    def test_attachments_not_required(self):
        from server.api.protocol import SEND_MESSAGE_TOOL

        required = SEND_MESSAGE_TOOL.inputSchema["required"]
        assert "attachments" not in required
        assert "text" in required
