"""Permission state machine for QuantTutorBench HTTP Server.

Defines session phases and enforces which tool calls are permitted in each phase.

State transitions:
    UNREGISTERED  --register_session-->  REGISTERED
    REGISTERED    --start_session-->     IN_SESSION
    IN_SESSION    --status=completed-->  COMPLETED

"""

import json
from enum import Enum

from mcp.types import Tool


class SessionPhase(str, Enum):
    """Session lifecycle phases."""

    UNREGISTERED = "unregistered"
    REGISTERED = "registered"
    IN_SESSION = "in_session"
    COMPLETED = "completed"


# ---------------------------------------------------------------------------
# Session API tool definitions (MCP Tool objects)
# ---------------------------------------------------------------------------

REGISTER_SESSION_TOOL = Tool(
    name="register_session",
    description=(
        "Register a task for benchmarking. Server creates a sandbox environment "
        "and assigns a student persona. Returns session_id on success. "
        "If persona_id is omitted, the server selects one automatically."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": (
                    "Task identifier (e.g. X01_ma_offbyone). "
                    "See TASKS.md for the full list."
                ),
            },
            "persona_id": {
                "type": "string",
                "description": (
                    "Optional explicit student persona ID for deterministic reruns. "
                    "Must be one of the task's supported persona_ids."
                ),
            },
        },
        "required": ["task_id"],
    },
)

START_SESSION_TOOL = Tool(
    name="start_session",
    description=(
        "Start the tutoring session. Returns the student's first message. "
        "Can only be called once after register_session."
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
)

SEND_MESSAGE_TOOL = Tool(
    name="send_message",
    description=(
        "Send a message to the student. Returns the student's reply and "
        "session status. When status is 'completed', the session has ended."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Your message to the student.",
            },
        },
        "required": ["text"],
    },
)

REQUEST_EVALUATION_TOOL = Tool(
    name="request_evaluation",
    description=(
        "Request evaluation of the completed session. "
        "First call triggers evaluation; subsequent calls return status or results."
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
)

GET_RESULTS_TOOL = Tool(
    name="get_results",
    description="Return the session run_state (conversation, tool_logs, metrics).",
    inputSchema={"type": "object", "properties": {}, "required": []},
)

GET_SCORES_TOOL = Tool(
    name="get_scores",
    description=(
        "Return evaluation scores. "
        "Set history=true to return all evaluation runs instead of just the latest."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "history": {
                "type": "boolean",
                "description": "If true, return all evaluation history.",
            },
        },
        "required": [],
    },
)

# Names of the four session API tools (benchmark interaction).
SESSION_API_TOOLS = frozenset(
    {
        "register_session",
        "start_session",
        "send_message",
        "request_evaluation",
    }
)

# Tools allowed in COMPLETED phase.
_COMPLETED_TOOLS = frozenset(
    {
        "request_evaluation",
        "get_results",
        "get_scores",
    }
)

# Tool names that REST /tool/{name} must reject (use dedicated endpoints instead).
TOOL_ENDPOINT_BLOCKED = SESSION_API_TOOLS | _COMPLETED_TOOLS


# ---------------------------------------------------------------------------
# Permission checking
# ---------------------------------------------------------------------------


def check_permission(
    phase: SessionPhase,
    tool_name: str,
) -> tuple[bool, str, list[str]]:
    """Check if a tool call is permitted in the current phase.

    Args:
        phase: Current session phase.
        tool_name: Name of the tool being called.

    Returns:
        (allowed, error_message, allowed_operations).
        error_message and allowed_operations are empty when allowed is True.
    """
    if phase == SessionPhase.UNREGISTERED:
        if tool_name == "register_session":
            return True, "", []
        return (
            False,
            "Session not registered. Call register_session first.",
            ["register_session"],
        )

    if phase == SessionPhase.REGISTERED:
        if tool_name == "start_session":
            return True, "", []
        return (
            False,
            "Session not started. Call start_session first.",
            ["start_session"],
        )

    if phase == SessionPhase.IN_SESSION:
        # send_message + all domain tools allowed; session management tools blocked
        if tool_name not in SESSION_API_TOOLS or tool_name == "send_message":
            return True, "", []
        return (
            False,
            f"Cannot call '{tool_name}' during active session.",
            ["send_message", "(domain tools)"],
        )

    if phase == SessionPhase.COMPLETED:
        if tool_name in _COMPLETED_TOOLS:
            return True, "", []
        return (
            False,
            "Session completed.",
            sorted(_COMPLETED_TOOLS),
        )

    return False, "Unknown phase.", []


def make_error_response(error: str, allowed: list[str]) -> str:
    """Build a JSON error response with allowed operations hint."""
    return json.dumps({"error": error, "allowed": allowed})
