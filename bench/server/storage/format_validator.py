"""Format validation for run_state.json in QuantTutorBench.

Validates that the captured session data has the correct structure
for the evaluation pipeline.  Only checks format — not content quality.
"""

import logging

logger = logging.getLogger(__name__)


def validate_conversation(conversation: list) -> tuple[bool, str]:
    """Validate conversation format: list of {role: str, content: str}.

    Returns:
        (is_valid, error_message). error_message is empty if valid.
    """
    if not isinstance(conversation, list):
        return False, f"conversation must be a list, got {type(conversation).__name__}"
    if len(conversation) < 2:
        return False, (
            f"conversation must have at least 1 complete exchange "
            f"(user + assistant), got {len(conversation)} messages"
        )
    for i, msg in enumerate(conversation):
        if not isinstance(msg, dict):
            return False, f"conversation[{i}] must be a dict, got {type(msg).__name__}"
        if "role" not in msg:
            return False, f"conversation[{i}] missing 'role' key"
        if "content" not in msg:
            return False, f"conversation[{i}] missing 'content' key"
        if not isinstance(msg["role"], str):
            return False, f"conversation[{i}]['role'] must be str"
        if not isinstance(msg["content"], str):
            return False, f"conversation[{i}]['content'] must be str"
        if msg["role"] not in ("user", "assistant", "tutor"):
            return False, (
                f"conversation[{i}]['role'] must be 'user' or 'assistant', "
                f"got {msg['role']!r}"
            )
    return True, ""


def validate_tool_logs(tool_logs: list) -> tuple[bool, str]:
    """Validate tool_logs format: list of dicts with required ToolCallLog fields.

    Required: name (str), args (dict).
    Optional: result, timestamp, duration_ms, success, turn_index, output_files.

    Returns:
        (is_valid, error_message). error_message is empty if valid.
    """
    if not isinstance(tool_logs, list):
        return False, f"tool_logs must be a list, got {type(tool_logs).__name__}"
    for i, log in enumerate(tool_logs):
        if not isinstance(log, dict):
            return False, f"tool_logs[{i}] must be a dict, got {type(log).__name__}"
        if "name" not in log:
            return False, f"tool_logs[{i}] missing 'name' key"
        if not isinstance(log["name"], str):
            return False, f"tool_logs[{i}]['name'] must be str"
        if "args" not in log:
            return False, f"tool_logs[{i}] missing 'args' key"
        if not isinstance(log["args"], dict):
            return False, f"tool_logs[{i}]['args'] must be dict"
    return True, ""


def validate_run_state(state: dict) -> tuple[bool, list[str]]:
    """Validate a complete run_state dict for evaluation readiness.

    Returns:
        (is_valid, list_of_errors). Empty list means all valid.
    """
    errors = []

    # Required: conversation
    if "conversation" not in state:
        errors.append("Missing required field: 'conversation'")
    else:
        ok, err = validate_conversation(state["conversation"])
        if not ok:
            errors.append(f"conversation: {err}")

    # Required: tool_logs
    if "tool_logs" not in state:
        errors.append("Missing required field: 'tool_logs'")
    else:
        ok, err = validate_tool_logs(state["tool_logs"])
        if not ok:
            errors.append(f"tool_logs: {err}")

    return (len(errors) == 0, errors)
