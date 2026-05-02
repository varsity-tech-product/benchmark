"""Conversation enrichment with tool activity summaries.

Two enrichment modes:
- **Full**: Tool names + truncated args + truncated results.
  Used by D4/D5/D7 where tool output content matters.
- **Lightweight**: Tool names + status only.
  Used by D3 where knowing *that* tools were used matters,
  but the content of tool output does not.
"""

from collections import defaultdict

from eval.tool_filters import NON_SUBSTANTIVE_TOOLS

_MAX_RESULT_CHARS = 500
_MAX_SUMMARY_CHARS = 1500


def _brief_args(args: dict) -> str:
    if not args:
        return ""
    parts = []
    for k, v in args.items():
        v_str = str(v)
        if len(v_str) > 60:
            v_str = v_str[:57] + "..."
        parts.append(f"{k}={v_str}")
    return ", ".join(parts[:3])


def _summarize_tool_calls(logs: list) -> str:
    lines = []
    for log in logs:
        if log.name in NON_SUBSTANTIVE_TOOLS:
            continue
        result_str = str(log.result or "")
        if len(result_str) <= _MAX_RESULT_CHARS:
            result_preview = result_str
        else:
            half = _MAX_RESULT_CHARS // 2
            result_preview = result_str[:half] + " ... " + result_str[-half:]
        status = "OK" if log.success else "ERROR"
        lines.append(
            f"- {log.name}({_brief_args(log.args)}) -> [{status}] {result_preview}"
        )

    if not lines:
        return ""

    summary = "[Tool Activity]\n" + "\n".join(lines)
    if len(summary) > _MAX_SUMMARY_CHARS:
        summary = summary[:_MAX_SUMMARY_CHARS] + "\n... (truncated)"
    return summary


def _summarize_tool_calls_lightweight(logs: list) -> str:
    """Lightweight summary: tool name + status + output size. No content."""
    lines = []
    for log in logs:
        if log.name in NON_SUBSTANTIVE_TOOLS:
            continue
        status = "OK" if log.success else "ERROR"
        result_len = len(str(log.result or ""))
        # Show key path/command arg (truncated) for context
        args = getattr(log, "args", {}) or {}
        hint = ""
        for key in ("path", "command", "data_path", "file_path"):
            if key in args:
                val = str(args[key])
                hint = f"{key}={val[:60]}"
                break
        line = f"- {log.name}({hint}) -> [{status}]"
        if result_len > 0:
            line += f" ({result_len:,} chars output)"
        lines.append(line)

    if not lines:
        return ""
    return "[Tool Activity Summary]\n" + "\n".join(lines)


def enrich_conversation_with_tools(
    conversation: list[dict],
    tool_logs: list,
    mode: str = "full",
) -> list[dict]:
    """Append tool-activity summaries to assistant turns.

    Args:
        mode: "full" — tool names + truncated args + truncated results.
              "lightweight" — tool names + status + output size only.
    """
    if not tool_logs:
        return conversation

    summarize_fn = (
        _summarize_tool_calls_lightweight
        if mode == "lightweight"
        else _summarize_tool_calls
    )

    logs_by_turn = defaultdict(list)
    for log in tool_logs:
        logs_by_turn[log.turn_index].append(log)

    enriched = []
    assistant_idx = 0
    for turn in conversation:
        if turn["role"] != "assistant":
            enriched.append(turn)
            continue

        turn_logs = logs_by_turn.get(assistant_idx, [])
        summary = summarize_fn(turn_logs) if turn_logs else ""
        if summary:
            enriched.append(
                {
                    **turn,
                    "content": turn["content"] + "\n\n" + summary,
                }
            )
        else:
            enriched.append(turn)
        assistant_idx += 1

    return enriched
