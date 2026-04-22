"""Permission state machine for QuantTutorBench HTTP Server.

Defines session phases and enforces which tool calls are permitted in each phase.

State transitions:
    UNREGISTERED  --register_session-->  REGISTERED
    REGISTERED    --start_session-->     IN_SESSION
    IN_SESSION    --status=completed-->  COMPLETED

Tool discovery strategy (see issue #25): MCP ``list_tools`` returns the
static union of all lifecycle tools regardless of phase, so frozen-
registry clients (e.g. Claude Code) can drive the full flow without
depending on ``tools/list_changed`` refreshes. Phase enforcement lives
at call time in ``check_permission`` — wrong-phase calls return an
imperative error naming the correct next hop.

"""

import json
from enum import Enum
from typing import Optional

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
        "When connected via run token, task_id is optional — the server "
        "resolves it from the run assignment. "
        "If persona_id is omitted, the server selects one automatically."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "task_id": {
                "type": "string",
                "description": (
                    "Task identifier (e.g. D01 or D01_load_inspect_ohlcv). "
                    "Optional when connected via run token."
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
        "required": [],
    },
)

START_SESSION_TOOL = Tool(
    name="start_session",
    description=(
        "Start the tutoring session. Returns the session background, "
        "the student's first message, and the list of available tools. "
        "Precondition: register_session must have succeeded. "
        "Called out of order, returns "
        '{"error": ..., "allowed": [...], "current_phase": ...} — '
        'follow the "allowed" list to decide the next call.'
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
)

SEND_MESSAGE_TOOL = Tool(
    name="send_message",
    description=(
        "Send a message to the student and receive their reply. "
        "This is the ONLY way to communicate with the student. "
        "Your text output is NOT delivered to the student — "
        "only messages sent through this tool reach them. "
        "Each call advances the conversation by one turn and cannot be undone. "
        "Returns the student's reply and session status. "
        "When status is 'completed' or 'failed', the session has ended; follow "
        'the returned "next_allowed" hint (request_evaluation). '
        "Precondition: start_session must have succeeded. "
        "Optionally include 'reasoning' to record your private rationale "
        "for this turn — it is logged for analysis and is NOT shown to the student."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "text": {
                "type": "string",
                "description": "Your message to the student.",
            },
            "attachments": {
                "type": "array",
                "description": (
                    "Up to 3 workspace file paths to share with the student. "
                    "Supports text files and images "
                    "(PNG, JPG, GIF, BMP, WebP up to 5MB each). "
                    "Maximum 5 images per session."
                ),
                "items": {"type": "string"},
                "maxItems": 3,
            },
            "reasoning": {
                "type": "string",
                "description": (
                    "Private rationale for this turn — why you chose this "
                    "wording, what you expect to learn from the reply, or "
                    "what hypothesis you are testing. Recorded in tool logs "
                    "for post-hoc analysis. Not delivered to the student."
                ),
            },
        },
        "required": ["text"],
    },
)

GET_BACKGROUND_TOOL = Tool(
    name="get_background",
    description=(
        "Re-read the session background returned by start_session. "
        "Contains the sandbox environment description, communication "
        "constraints, and available systems (data directories, runtime "
        "engines, mounted code). Returns a plain text string."
    ),
    inputSchema={"type": "object", "properties": {}, "required": []},
)

REQUEST_EVALUATION_TOOL = Tool(
    name="request_evaluation",
    description=(
        "Append a new score_n evaluation run for the completed session. "
        "If another evaluation is running, returns that run's status."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "eval_mode": {
                "type": "string",
                "enum": ["full", "qr", "qp", "tutor"],
                "description": "Evaluation scope.",
            },
            "tutor_dims": {
                "type": "string",
                "description": "Comma-separated Tutor dimensions for tutor-mode subsets.",
            },
            "eval_model": {
                "type": "string",
                "description": "Judge model identifier.",
            },
            "idempotency_key": {
                "type": "string",
                "description": "Optional key to reuse an existing running request.",
            },
        },
        "required": [],
    },
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
        "Set history=true to return all score runs, or pass score/score_id."
    ),
    inputSchema={
        "type": "object",
        "properties": {
            "history": {
                "type": "boolean",
                "description": "If true, return all score_n history.",
            },
            "score": {
                "type": "string",
                "description": "Specific score id to return, e.g. score_2.",
            },
            "scores": {
                "type": "string",
                "description": "Comma-separated score ids to return.",
            },
            "status": {
                "type": "string",
                "description": "Comma-separated status filter for history.",
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


# Lifecycle tools that MCP ``list_tools`` exposes in every phase (static union).
LIFECYCLE_TOOL_NAMES: tuple[str, ...] = (
    "register_session",
    "start_session",
    "send_message",
    "get_background",
    "request_evaluation",
    "get_results",
    "get_scores",
)


# Recommended next-hop tool(s) per phase — the state-machine signal the agent
# should follow to advance.
_NEXT_ALLOWED: dict[SessionPhase, list[str]] = {
    SessionPhase.UNREGISTERED: ["register_session"],
    SessionPhase.REGISTERED: ["start_session"],
    SessionPhase.IN_SESSION: ["send_message"],
    SessionPhase.COMPLETED: ["request_evaluation"],
}


def next_allowed_for_phase(phase: SessionPhase) -> list[str]:
    """Return the recommended next tool(s) to call from ``phase``."""
    return list(_NEXT_ALLOWED.get(phase, []))


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
        Error messages are imperative — they name the next tool the agent
        should call — so a frozen-registry MCP client can drive the state
        machine from error guidance alone.
    """
    if phase == SessionPhase.UNREGISTERED:
        if tool_name == "register_session":
            return True, "", []
        return (
            False,
            "Wrong phase. Session not registered — call register_session next.",
            ["register_session"],
        )

    if phase == SessionPhase.REGISTERED:
        if tool_name == "start_session":
            return True, "", []
        return (
            False,
            "Wrong phase. Session registered but not started — call start_session next.",
            ["start_session"],
        )

    if phase == SessionPhase.IN_SESSION:
        # send_message + all domain tools allowed; session management tools blocked
        if tool_name not in SESSION_API_TOOLS or tool_name == "send_message":
            return True, "", []
        return (
            False,
            (
                f"Wrong phase. Cannot call '{tool_name}' during active session — "
                "continue with send_message or a domain tool."
            ),
            ["send_message", "(domain tools)"],
        )

    if phase == SessionPhase.COMPLETED:
        if tool_name in _COMPLETED_TOOLS:
            return True, "", []
        return (
            False,
            (
                "Wrong phase. Session completed — call request_evaluation, "
                "then get_results / get_scores."
            ),
            sorted(_COMPLETED_TOOLS),
        )

    return False, "Unknown phase.", []


def make_error_response(
    error: str,
    allowed: list[str],
    *,
    current_phase: Optional[SessionPhase] = None,
) -> str:
    """Build a JSON error response with state-machine hints.

    ``allowed`` names the tools the caller may try from the current phase.
    When ``current_phase`` is provided (phase-denial errors) it is included
    so the agent can map the error back to the state machine.
    """
    payload: dict = {"error": error, "allowed": allowed}
    if current_phase is not None:
        payload["current_phase"] = current_phase.value
    return json.dumps(payload)
