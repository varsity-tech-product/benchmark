"""Client-side trace writer.

Saves two files with identical content in different formats:
- ``client_trace.md``   — human-readable Markdown (full content)
- ``client_trace.json`` — machine-readable JSON

Contents (all client-side, no server dependency):
- Agent cost (model, tokens, cost, api_calls)
- Thinking trace (extended thinking / COT blocks) — full text
- Per-turn content blocks (thinking, text, tool_use, tool_result) — full text
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


def save_client_trace(
    result_dir: Path,
    session_id: str,
    task_id: str,
    duration_seconds: float,
    agent_cost: dict,
    thinking_trace: list[dict],
    content_blocks: dict[int, list[dict]],
) -> Path:
    """Save client trace as both MD and JSON.

    Returns:
        Path to the output directory.
    """
    out_dir = Path(result_dir) / session_id
    out_dir.mkdir(parents=True, exist_ok=True)

    segmented_blocks, content_blocks_mode = _normalize_content_blocks(content_blocks)

    trace_data = {
        "task_id": task_id,
        "session_id": session_id,
        "timestamp": datetime.now().isoformat(),
        "duration_seconds": round(duration_seconds, 2),
        "agent_cost": agent_cost,
        "thinking_trace": thinking_trace,
        "content_blocks": {str(k): v for k, v in segmented_blocks.items()},
        "content_blocks_mode": content_blocks_mode,
    }

    json_path = out_dir / "client_trace.json"
    json_path.write_text(
        json.dumps(trace_data, indent=2, default=str),
        encoding="utf-8",
    )

    md_path = out_dir / "client_trace.md"
    md_path.write_text(_render_md(trace_data), encoding="utf-8")

    logger.info("Saved client_trace.json + client_trace.md to %s", out_dir)
    return out_dir


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_md(data: dict) -> str:
    """Render trace data as Markdown with full content."""
    L: list[str] = []

    cost = data.get("agent_cost", {})
    thinking = data.get("thinking_trace", [])
    blocks = data.get("content_blocks", {})

    # ── Header ──
    L.append(f"# Client Trace: {data.get('task_id', '?')}")
    L.append("")
    L.append("| Field | Value |")
    L.append("|-------|-------|")
    L.append(f"| Session | `{data.get('session_id', '?')}` |")
    L.append(f"| Timestamp | {data.get('timestamp', '?')} |")
    L.append(f"| Duration | {data.get('duration_seconds', 0):.1f}s |")
    L.append(f"| Model | `{cost.get('model', 'unknown')}` |")
    L.append(f"| API calls | {cost.get('api_calls', 0)} |")
    L.append(f"| Input tokens | {cost.get('input_tokens', 0):,} |")
    L.append(f"| Output tokens | {cost.get('output_tokens', 0):,} |")
    L.append(f"| Cost | ${cost.get('cost_usd', 0):.4f} |")
    L.append(
        f"| Content blocks mode | `{data.get('content_blocks_mode', 'unknown')}` |"
    )
    L.append("")

    # ── Thinking Trace (full) ──
    if thinking:
        L.append(f"## Thinking Trace ({len(thinking)} entries)")
        L.append("")
        for entry in thinking:
            turn = entry.get("turn_index", "?")
            iteration = entry.get("iteration", "?")
            text = entry.get("thinking", "")
            L.append(f"### Turn {turn} / Iteration {iteration}")
            L.append("")
            L.append("```text")
            L.append(text)
            L.append("```")
            L.append("")
    else:
        L.append("## Thinking Trace")
        L.append("")
        L.append("*No thinking blocks captured.*")
        L.append("")

    # ── Content Blocks (full) ──
    if blocks:
        total = sum(len(v) for v in blocks.values())
        L.append(f"## Content Blocks ({len(blocks)} turns, {total} blocks)")
        L.append("")

        for turn_key in sorted(blocks.keys(), key=lambda k: int(k)):
            blist = blocks[turn_key]
            L.append(f"### Turn {turn_key}")
            L.append("")

            for i, block in enumerate(blist):
                btype = block.get("type", "unknown")

                if btype == "thinking":
                    text = block.get("thinking", "")
                    L.append(f"#### [{i}] Thinking ({len(text)} chars)")
                    L.append("")
                    L.append("```text")
                    L.append(text)
                    L.append("```")
                    L.append("")

                elif btype == "text":
                    text = block.get("text", "")
                    L.append(f"#### [{i}] Text ({len(text)} chars)")
                    L.append("")
                    # Tutor text may contain markdown — wrap in details
                    L.append("<details open><summary>Agent text output</summary>")
                    L.append("")
                    L.append(text)
                    L.append("")
                    L.append("</details>")
                    L.append("")

                elif btype == "tool_use":
                    name = block.get("name", "?")
                    tool_input = block.get("input", block.get("args", {}))
                    L.append(f"#### [{i}] Tool Use: `{name}`")
                    L.append("")
                    L.append("```json")
                    L.append(json.dumps(tool_input, indent=2, default=str))
                    L.append("```")
                    L.append("")

                elif btype == "tool_result":
                    tool_id = block.get("tool_use_id", "?")
                    content = block.get("content", "")
                    if isinstance(content, list):
                        content = "\n".join(
                            c.get("text", str(c)) if isinstance(c, dict) else str(c)
                            for c in content
                        )
                    L.append(f"#### [{i}] Tool Result (id=`{tool_id}`)")
                    L.append("")
                    L.append("```")
                    L.append(str(content))
                    L.append("```")
                    L.append("")

                else:
                    L.append(f"#### [{i}] {btype}")
                    L.append("")
                    L.append("```json")
                    L.append(json.dumps(block, indent=2, default=str))
                    L.append("```")
                    L.append("")
    else:
        L.append("## Content Blocks")
        L.append("")
        L.append("*No content blocks captured.*")
        L.append("")

    return "\n".join(L)


def _normalize_content_blocks(
    content_blocks: dict[int, list[dict]],
) -> tuple[dict[int, list[dict]], str]:
    """Return turn-aligned content blocks for replay.

    Older outer-loop clients already save one block-list per tutor turn.
    The current whole-session baseline captures the entire session into a
    single block-list. For replay, we split that single list at successful
    ``send_message`` boundaries so web can render per-turn traces again.
    """
    if not content_blocks:
        return {}, "empty"

    cleaned = {
        int(k): v
        for k, v in content_blocks.items()
        if isinstance(k, int) and isinstance(v, list) and v
    }
    if not cleaned:
        return {}, "empty"

    if len(cleaned) != 1:
        return cleaned, "per_turn"

    only_turn = next(iter(cleaned))
    only_blocks = cleaned[only_turn]
    send_message_uses = sum(
        1
        for block in only_blocks
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and block.get("name") == "send_message"
    )

    if send_message_uses <= 1:
        return cleaned, "single_turn"

    segmented = _split_blocks_on_send_message(only_blocks)
    if segmented:
        return segmented, "session_split"
    return cleaned, "session_raw"


def _split_blocks_on_send_message(blocks: list[dict]) -> dict[int, list[dict]]:
    """Split a whole-session block list into tutor turns.

    A tutor turn becomes externally visible only when ``send_message``
    succeeds. We therefore finalize a segment only after a successful
    ``send_message`` + immediate ``tool_result`` pair.
    """
    segmented: dict[int, list[dict]] = {}
    current: list[dict] = []
    turn_index = 0
    idx = 0

    while idx < len(blocks):
        block = blocks[idx]
        current.append(block)

        is_send_message = (
            isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name") == "send_message"
        )
        if is_send_message and idx + 1 < len(blocks):
            next_block = blocks[idx + 1]
            if isinstance(next_block, dict) and next_block.get("type") == "tool_result":
                current.append(next_block)
                idx += 1
                if _is_successful_send_message_result(next_block):
                    segmented[turn_index] = current
                    turn_index += 1
                    current = []

        idx += 1

    return segmented


def _is_successful_send_message_result(block: dict) -> bool:
    content = block.get("content")
    payload = None

    if isinstance(content, str):
        try:
            payload = json.loads(content)
        except Exception:
            return False
    elif isinstance(content, dict):
        payload = content
    else:
        return False

    if not isinstance(payload, dict):
        return False
    if payload.get("error"):
        return False
    return bool(
        payload.get("student_message") is not None
        or payload.get("status") in {"active", "completed"}
        or payload.get("reason")
    )
