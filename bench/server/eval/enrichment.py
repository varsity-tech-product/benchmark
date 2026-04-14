"""Conversation enrichment with tool activity summaries.

Used by Tutor 7D evaluation (D4/D5/D7 dimensions) to provide the judge
with tool execution context alongside the tutor's conversational text.

Copied from orchestrator.orchestrator._enrich_conversation_with_tools
to maintain server/ independence from orchestrator/.
"""

from collections import defaultdict

from server.tool_filters import NON_SUBSTANTIVE_TOOLS

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


def enrich_conversation_with_tools(
    conversation: list[dict],
    tool_logs: list,
) -> list[dict]:
    """Append tool-activity summaries to assistant turns for tutor evaluation."""
    if not tool_logs:
        return conversation

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
        summary = _summarize_tool_calls(turn_logs) if turn_logs else ""
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
