"""Unified context construction for all LLM judges.

Single entry point: format conversation from any source (run_state.json or
live session), then apply dimension-specific enrichment to build the context
string passed to EwanConvGEval.

Each ``build_*_context()`` function returns a pre-formatted string that
becomes ``EvalTestCase.context``.
"""

import json as _json
import os
import re as _re
from typing import Optional

from server.tool_filters import NON_SUBSTANTIVE_TOOLS, PROTOCOL_ONLY_TOOLS

# ──────────────────────────────────────────────────────────────
# Conversation formatting (unified entry point)
# ──────────────────────────────────────────────────────────────


def format_conversation(conversation: list[dict]) -> str:
    """Format raw conversation into numbered JSON lines.

    This is the single entry point for conversation formatting,
    regardless of whether data comes from run_state.json or a live session.
    Input format: [{"role": str, "content": str}, ...]
    """
    lines = []
    for i, turn in enumerate(conversation):
        d = _json.dumps(
            {"role": turn["role"], "content": turn["content"]},
            ensure_ascii=False,
        )
        lines.append(f"{i + 1}. {d}")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Conversation preprocessing (moved from tutor_conv_geval.py)
# ──────────────────────────────────────────────────────────────

_CODE_FENCE_RE = _re.compile(
    r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$",
    _re.MULTILINE | _re.DOTALL,
)


def _strip_code_blocks(content: str) -> str:
    """Replace code fences with a placeholder showing line count."""

    def _replacement(match):
        code = match.group(1)
        line_count = code.count("\n") + 1
        return f"[code block: {line_count} lines]"

    return _CODE_FENCE_RE.sub(_replacement, content)


def preprocess_conversation(conversation: list[dict], mode: str = "none") -> list[dict]:
    """Apply preprocessing to assistant turns.

    Args:
        mode: "strip_code" to replace code blocks with placeholders,
              "none" to pass through unchanged.
    """
    if mode == "none":
        return conversation
    fn = _strip_code_blocks
    processed = []
    for t in conversation:
        if t["role"] != "assistant":
            processed.append(t)
        else:
            processed.append({**t, "content": fn(t["content"])})
    return processed


# ──────────────────────────────────────────────────────────────
# Tutor 6D context
# ──────────────────────────────────────────────────────────────

# Dimensions that require enriched conversation (tool outputs appended)
ENRICHED_DIMS = {"D4_instructional_accuracy", "D6_safety_boundaries"}

# Per-dimension preprocessing mode
DIMENSION_PREPROCESS: dict[str, str] = {
    "D1_finance_adaptation": "strip_code",
    "D2_code_adaptation": "none",
    "D3_pedagogical_method": "strip_code",
    "D4_instructional_accuracy": "none",
    "D5_empathetic_response": "strip_code",
    "D6_safety_boundaries": "strip_code",
}


def build_tutor_context(
    conversation: list[dict],
    dimension_name: str,
    enriched_conversation: Optional[list[dict]] = None,
    _cache: Optional[dict] = None,
) -> str:
    """Build context for a Tutor 6D dimension evaluation.

    Selects source (enriched vs. original) based on dimension,
    applies preprocessing (strip_code), and formats as text.

    Pass a shared ``_cache`` dict across calls within one evaluation
    to avoid redundant preprocessing + formatting for dimensions that
    share the same (source, mode) combination.
    """
    use_enriched = dimension_name in ENRICHED_DIMS and enriched_conversation is not None
    mode = DIMENSION_PREPROCESS.get(dimension_name, "none")
    cache_key = (id(enriched_conversation) if use_enriched else id(conversation), mode)

    if _cache is not None and cache_key in _cache:
        return _cache[cache_key]

    src = enriched_conversation if use_enriched else conversation
    processed = preprocess_conversation(src, mode)
    result = format_conversation(processed)

    if _cache is not None:
        _cache[cache_key] = result
    return result


# ──────────────────────────────────────────────────────────────
# QR Result Judge context
# ──────────────────────────────────────────────────────────────


def _extract_agent_key_outputs(tool_logs: list) -> str:
    """Extract key outputs from agent's tool logs for result evaluation.

    Focuses on outputs from substantive tools (shell_exec results,
    backtest metrics, computed indicators, etc.).
    """
    key_outputs = []
    for log in tool_logs:
        if log.name in PROTOCOL_ONLY_TOOLS:
            continue
        if not log.result:
            continue
        result_preview = str(log.result)[:1500]
        status = "OK" if log.success else "FAIL"
        key_outputs.append(f"  {log.name} [{status}]: {result_preview}")
    return "\n".join(key_outputs[-15:]) if key_outputs else "  (no tool outputs)"


def _extract_agent_summary(conversation: list) -> str:
    """Extract last 3 assistant messages as agent summary."""
    assistant_msgs = [
        t["content"] for t in (conversation or []) if t.get("role") == "assistant"
    ]
    if not assistant_msgs:
        return "(no agent responses)"
    recent = assistant_msgs[-3:]
    per_msg_limit = 6000 // len(recent)
    return "\n---\n".join(str(msg)[:per_msg_limit] for msg in recent)


def _list_workspace_files(workspace_path: str) -> list[str]:
    """List files in the workspace directory."""
    if not workspace_path or not os.path.isdir(workspace_path):
        return []
    try:
        return sorted(os.listdir(workspace_path))
    except OSError:
        return []


def _format_reference_section(reference: dict) -> str:
    """Format the reference execution data for context."""
    ref_key_results = reference.get("key_results", {})
    ref_workspace = reference.get("workspace_files", [])
    ref_trace = reference.get("trace_summary", [])

    if isinstance(ref_trace, list):
        ref_output = "\n".join(f"  {i + 1}. {s}" for i, s in enumerate(ref_trace[-8:]))
    else:
        ref_output = str(ref_trace)[:500]

    parts = [
        "## Reference Result (Expert Baseline)",
        f"Key metrics: {_json.dumps(ref_key_results, indent=2, default=str)}",
        f"Files produced: {', '.join(ref_workspace) if ref_workspace else '(none)'}",
        f"Execution trace (last steps):\n{ref_output}",
    ]
    return "\n".join(parts)


def build_result_judge_context(
    task_description: str,
    category: str,
    rc_list: list[str],
    tool_logs: list,
    workspace_path: str,
    conversation: list[dict],
    reference: Optional[dict] = None,
) -> str:
    """Build context for QR result_judge evaluation.

    Assembles: task + RC checklist + reference (optional) + agent outputs.
    """
    sections = []

    # Task
    sections.append(f"## Task\n{task_description}\nCategory: {category}")

    # RC checklist (acceptance criteria)
    if rc_list:
        rc_text = "\n".join(f"- {rc}" for rc in rc_list)
        sections.append(f"## Required Capabilities (Acceptance Criteria)\n{rc_text}")

    # Reference (if available)
    if reference:
        sections.append(_format_reference_section(reference))

    # Agent outputs
    agent_key_outputs = _extract_agent_key_outputs(tool_logs)
    agent_workspace_files = _list_workspace_files(workspace_path)
    agent_summary = _extract_agent_summary(conversation)
    agent_files_str = (
        ", ".join(agent_workspace_files) if agent_workspace_files else "(none)"
    )

    agent_section = (
        "## Agent Result\n"
        f"Files produced: {agent_files_str}\n"
        f"Key tool outputs:\n{agent_key_outputs}\n"
        f"Agent's explanation (summary):\n{agent_summary}"
    )
    sections.append(agent_section)

    context = "\n\n".join(sections)

    # Safety cap: trim agent_key_outputs if context exceeds budget
    _TOTAL_CONTEXT_CAP = 35000
    if len(context) > _TOTAL_CONTEXT_CAP:
        overshoot = len(context) - _TOTAL_CONTEXT_CAP
        trimmed_outputs = agent_key_outputs[
            : max(200, len(agent_key_outputs) - overshoot)
        ]
        agent_section = (
            "## Agent Result\n"
            f"Files produced: {agent_files_str}\n"
            f"Key tool outputs (trimmed):\n{trimmed_outputs}\n"
            f"Agent's explanation (summary):\n{agent_summary}"
        )
        sections[-1] = agent_section
        context = "\n\n".join(sections)

    return context


# ──────────────────────────────────────────────────────────────
# QP Task Planning context
# ──────────────────────────────────────────────────────────────


def build_task_planning_context(
    enriched_conversation: list[dict],
    rc_list: list[str],
    task_description: str,
) -> str:
    """Build context for QP task_planning evaluation.

    Assembles: task description + RC checklist (calibration anchor) +
    enriched conversation (execution trace).
    """
    sections = []
    sections.append(f"## Task\n{task_description}")
    if rc_list:
        rc_text = "\n".join(f"- {rc}" for rc in rc_list)
        sections.append(f"## Required Capabilities (Calibration Anchor)\n{rc_text}")
    sections.append(f"## Execution Trace\n{format_conversation(enriched_conversation)}")
    return "\n\n".join(sections)


# ──────────────────────────────────────────────────────────────
# QP Problem Solving context
# ──────────────────────────────────────────────────────────────


def _format_error_summary(tool_logs: list) -> str:
    """Extract and format explicit errors from tool logs."""
    errors = []
    for i, log in enumerate(tool_logs):
        if log.name in NON_SUBSTANTIVE_TOOLS:
            continue
        if not log.success:
            result_preview = str(log.result or "")[:800]
            args_preview = _json.dumps(log.args, default=str)
            if len(args_preview) > 200:
                args_preview = args_preview[:200] + "..."
            errors.append(
                f"Error {len(errors) + 1}: {log.name}({args_preview})\n"
                f"  Result: {result_preview}"
            )
    return "\n".join(errors) if errors else "(no explicit errors)"


def has_explicit_errors(tool_logs: list) -> bool:
    """Check if any substantive tool call failed.

    Used as trigger check for problem_solving evaluation.
    Returns False → problem_solving gets score=None (excluded from QP).
    """
    return any(
        not log.success for log in tool_logs if log.name not in NON_SUBSTANTIVE_TOOLS
    )


def build_problem_solving_context(
    enriched_conversation: list[dict],
    task_description: str,
    tool_logs: list,
) -> str:
    """Build context for QP problem_solving evaluation.

    Assembles: task description + explicit error summary +
    enriched conversation (full execution trace).
    """
    sections = []
    sections.append(f"## Task\n{task_description}")

    # Explicit error summary (highlighted for judge attention)
    error_summary = _format_error_summary(tool_logs)
    sections.append(f"## Explicit Errors in Execution\n{error_summary}")

    sections.append(
        f"## Full Execution Trace\n{format_conversation(enriched_conversation)}"
    )
    return "\n\n".join(sections)
