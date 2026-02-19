"""Convert proxy logs to DeepEval-compatible data structures."""


def logs_to_deepeval_tool_calls(logs: list[dict]) -> list[dict]:
    """Convert proxy logs to DeepEval MCPToolCall-compatible dicts.

    DeepEval MCPToolCall expects:
        name: str
        args: dict
        result: CallToolResult (we use the string result)

    Returns list of dicts that can be used to construct MCPToolCall objects.
    """
    tool_calls = []
    for log in logs:
        tool_calls.append(
            {
                "name": log["name"],
                "args": log.get("args", {}),
                "result": log.get("result", ""),
                "turn_index": log.get("turn_index", 0),
                "success": log.get("success", True),
                "duration_ms": log.get("duration_ms", 0),
            }
        )
    return tool_calls


def logs_to_turns_with_tools(
    conversation_turns: list[dict], tool_call_logs: list[dict]
) -> list[dict]:
    """Merge conversation turns with their associated tool calls.

    For each assistant turn, attach the tool calls that occurred during that turn.
    This is used to build DeepEval ConversationalTestCase with Turn objects
    that include mcp_tools_called.

    Args:
        conversation_turns: List of {role, content} dicts
        tool_call_logs: List of tool call log dicts with turn_index

    Returns:
        List of turn dicts with tool_calls attached to assistant turns.
    """
    # Group tool calls by turn index
    calls_by_turn: dict[int, list] = {}
    for log in tool_call_logs:
        idx = log.get("turn_index", 0)
        if idx not in calls_by_turn:
            calls_by_turn[idx] = []
        calls_by_turn[idx].append(log)

    result = []
    for i, turn in enumerate(conversation_turns):
        turn_dict = {
            "role": turn["role"],
            "content": turn["content"],
        }
        if turn["role"] == "assistant" and i in calls_by_turn:
            turn_dict["tool_calls"] = calls_by_turn[i]
        result.append(turn_dict)

    return result
