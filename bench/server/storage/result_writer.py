"""Result persistence for QuantTutorBench Server.

Saves session data as ``run_state.json`` + ``run_state.md``.

::

    results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:8]}/
        run_state.json
        run_state.md
        agent_files/

"""

import json
import logging
import os
import shutil
from collections import Counter
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

from server.tool_filters import NON_SUBSTANTIVE_TOOLS

logger = logging.getLogger(__name__)


def save_run_state(
    result_dir: Path,
    conversation: list[dict],
    tool_logs: list,
    workspace_path: Optional[str] = None,
    simulator_cost: float = 0.0,
    tc_checker_cost: float = 0.0,
    duration_seconds: float = 0.0,
    distractor_names: Optional[list[str]] = None,
    task_id: str = "",
    session_id: str = "",
    persona_id: str = "",
    session_status: str = "",
    termination_reason: Optional[str] = None,
    tc_coverage: Optional[dict] = None,
    tc_debug_history: Optional[list[dict]] = None,
    artifact_debug_history: Optional[list[dict]] = None,
    run_id: str = "",
    public_task_label: str = "",
    owner_user_id: str = "",
    owner_github_login: str = "",
    owner_email: str = "",
    visibility: str = "private",
) -> Path:
    """Save ``run_state.json`` + ``run_state.md``."""
    result_dir = Path(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)

    # Convert ToolCallLog objects to dicts if needed
    tool_logs_dicts = []
    for log in tool_logs:
        if isinstance(log, dict):
            tool_logs_dicts.append(log)
        else:
            tool_logs_dicts.append(asdict(log))

    # Substantive step count (excludes session tools)
    step_count = sum(
        1 for log in tool_logs_dicts if log.get("name") not in NON_SUBSTANTIVE_TOOLS
    )

    # Workspace files list
    workspace_files = []
    if workspace_path and os.path.isdir(workspace_path):
        workspace_files = sorted(
            os.path.relpath(os.path.join(root, f), workspace_path)
            for root, _, files in os.walk(workspace_path)
            for f in files
        )

    # Copy workspace to agent_files/
    agent_files_dir = result_dir / "agent_files"
    if workspace_path and os.path.isdir(workspace_path):
        if agent_files_dir.exists():
            shutil.rmtree(agent_files_dir)
        shutil.copytree(workspace_path, str(agent_files_dir))

    # Build key_results and trace_summary if available
    key_results = {}
    trace_summary = []
    try:
        from server.eval.trace_utils import build_trace_summary, extract_key_results

        ws = str(agent_files_dir) if agent_files_dir.exists() else ""
        key_results = extract_key_results(ws, tool_logs_dicts)
        trace_summary = build_trace_summary(tool_logs_dicts)
    except Exception as exc:
        logger.debug("Could not build reference fields: %s", exc)

    # Format validation
    from server.storage.format_validator import validate_run_state as _validate

    fmt_state = {"conversation": conversation, "tool_logs": tool_logs_dicts}
    fmt_ok, fmt_errors = _validate(fmt_state)

    state = {
        "run_id": run_id,
        "public_task_label": public_task_label,
        "owner_user_id": owner_user_id,
        "owner_github_login": owner_github_login,
        "owner_email": owner_email,
        "visibility": visibility,
        "task_id": task_id,
        "session_id": session_id,
        "persona_id": persona_id,
        "timestamp": datetime.now().isoformat(),
        "session_status": session_status or "completed",
        "termination_reason": termination_reason,
        "conversation": conversation,
        "tool_logs": tool_logs_dicts,
        "distractor_names": distractor_names or [],
        "workspace_files": workspace_files,
        "simulator_cost": simulator_cost,
        "tc_checker_cost": tc_checker_cost,
        "tc_coverage": tc_coverage,
        "tc_debug_history": tc_debug_history or [],
        "artifact_debug_history": artifact_debug_history or [],
        "duration_seconds": duration_seconds,
        "key_results": key_results,
        "trace_summary": trace_summary,
        "step_count": step_count,
        "format_validation": {"passed": fmt_ok, "errors": fmt_errors},
        "evaluation_status": "pending",
    }

    state_path = result_dir / "run_state.json"
    state_path.write_text(
        json.dumps(state, indent=2, default=str),
        encoding="utf-8",
    )

    md_path = result_dir / "run_state.md"
    md_path.write_text(_render_run_state_md(state), encoding="utf-8")

    from server.storage.bundle import write_manifest

    write_manifest(result_dir, state)

    logger.info("Saved run_state.json + run_state.md to %s", state_path.parent)
    return state_path


# ---------------------------------------------------------------------------
# Markdown renderer
# ---------------------------------------------------------------------------


def _render_run_state_md(state: dict) -> str:
    """Render run_state as human-readable Markdown with full content."""
    L: list[str] = []

    # ── Header ──
    L.append(f"# {state.get('task_id', '?')} / {state.get('session_id', '?')[:12]}")
    L.append("")
    L.append("| Field | Value |")
    L.append("|-------|-------|")
    L.append(f"| Persona | `{state.get('persona_id', '?')}` |")
    L.append(f"| Session status | `{state.get('session_status', '?')}` |")
    L.append(f"| Termination reason | `{state.get('termination_reason') or '-'}` |")
    L.append(f"| Duration | {state.get('duration_seconds', 0):.1f}s |")
    L.append(f"| Steps (substantive) | {state.get('step_count', 0)} |")
    L.append(f"| Simulator cost | ${state.get('simulator_cost', 0):.4f} |")
    L.append(f"| TC checker cost | ${state.get('tc_checker_cost', 0):.4f} |")
    L.append(f"| Evaluation | {state.get('evaluation_status', '?')} |")
    L.append(
        f"| Format validation | {'PASS' if state.get('format_validation', {}).get('passed') else 'FAIL'} |"
    )
    L.append("")

    tc_cov = state.get("tc_coverage") or {}
    if tc_cov:
        covered = tc_cov.get("covered", 0)
        total = tc_cov.get("total", 0)
        L.append("## TC Coverage")
        L.append("")
        L.append(f"- Covered: **{covered}/{total}**")
        for idx, item in enumerate(tc_cov.get("items", []), start=1):
            status = "x" if item.get("covered") else " "
            L.append(f"- [{status}] {idx}. {item.get('text', '')}")
        L.append("")

        history = state.get("tc_debug_history") or []
        if history:
            L.append("<details><summary>Per-turn TC debug</summary>")
            L.append("")
            L.append(
                "| Turn | Newly covered | Covered after | Passes | Evidence tools |"
            )
            L.append(
                "|-----:|---------------|--------------|--------|---------------:|"
            )
            for row in history:
                turn = row.get("turn_index", "?")
                newly = (
                    ", ".join(str(x) for x in row.get("newly_covered_indices", []))
                    or "-"
                )
                covered_after = (
                    ", ".join(str(x) for x in row.get("covered_after_indices", []))
                    or "-"
                )
                passes = ", ".join(row.get("passes_used", [])) or "-"
                ev = row.get("evidence_tool_count", 0)
                L.append(f"| {turn} | {newly} | {covered_after} | {passes} | {ev} |")
            L.append("")
            L.append("</details>")
            L.append("")

    artifact_history = state.get("artifact_debug_history") or []
    if artifact_history:
        L.append("## Artifact Visibility Debug")
        L.append("")
        L.append("<details><summary>Per-turn artifact steering</summary>")
        L.append("")
        L.append(
            "| Turn | New code | New output | Ask code | Ask output | Ready not shown | Pending code | Pending output | Avoid new branch |"
        )
        L.append(
            "|-----:|:--------:|:----------:|:--------:|:----------:|:---------------:|:------------:|:--------------:|:----------------:|"
        )
        for row in artifact_history:
            req = row.get("request_signals", {})
            art = row.get("artifact_signals", {})
            steer = row.get("steering_signals", {})
            pending = row.get("pending_visibility_gap", {})
            L.append(
                f"| {row.get('turn_index', '?')} "
                f"| {'yes' if art.get('has_new_code_artifact') else 'no'} "
                f"| {'yes' if art.get('has_new_output_artifact') else 'no'} "
                f"| {'yes' if req.get('asks_for_code') else 'no'} "
                f"| {'yes' if req.get('asks_for_output') else 'no'} "
                f"| {'yes' if steer.get('artifact_ready_but_not_shown') else 'no'} "
                f"| {'yes' if pending.get('needs_code') else 'no'} "
                f"| {'yes' if pending.get('needs_output') else 'no'} "
                f"| {'yes' if steer.get('avoid_new_branch') else 'no'} |"
            )
        L.append("")
        L.append("</details>")
        L.append("")

    # ── Workspace ──
    ws = state.get("workspace_files", [])
    L.append(f"## Workspace ({len(ws)} files)")
    L.append("")
    if ws:
        for f in ws:
            L.append(f"- `{f}`")
    else:
        L.append("*(empty)*")
    L.append("")

    # ── Tool log summary ──
    logs = state.get("tool_logs", [])
    L.append(f"## Tool Calls ({len(logs)})")
    L.append("")
    if logs:
        tool_ok = Counter(entry["name"] for entry in logs if entry.get("success"))
        tool_fail = Counter(entry["name"] for entry in logs if not entry.get("success"))
        all_names = sorted(set(tool_ok) | set(tool_fail))
        L.append("| Tool | OK | Fail |")
        L.append("|------|---:|-----:|")
        for name in all_names:
            L.append(f"| `{name}` | {tool_ok.get(name,0)} | {tool_fail.get(name,0)} |")
        L.append("")

        # Per-turn detail
        L.append("<details><summary>Full tool log</summary>")
        L.append("")
        L.append("| # | Turn | Tool | OK | Duration | Args (truncated) |")
        L.append("|--:|-----:|------|:--:|---------:|------------------|")
        for i, log in enumerate(logs):
            name = log.get("name", "?")
            ok = "yes" if log.get("success") else "**no**"
            dur = log.get("duration_ms", 0)
            turn = log.get("turn_index", "?")
            args = json.dumps(log.get("args", {}), default=str)
            if len(args) > 60:
                args = args[:57] + "..."
            args = args.replace("|", "\\|")
            L.append(f"| {i} | {turn} | `{name}` | {ok} | {dur:.0f}ms | {args} |")
        L.append("")
        L.append("</details>")
        L.append("")

    # ── Distractors ──
    dist = state.get("distractor_names", [])
    if dist:
        L.append(f"## Distractors ({len(dist)})")
        L.append("")
        L.append(", ".join(f"`{d}`" for d in dist))
        L.append("")

    # ── Conversation (full content) ──
    conv = state.get("conversation", [])
    user_count = sum(1 for m in conv if m["role"] == "user")
    asst_count = sum(1 for m in conv if m["role"] != "user")
    L.append(
        f"## Conversation ({user_count} student + {asst_count} tutor = {len(conv)} messages)"
    )
    L.append("")

    for i, msg in enumerate(conv):
        role = msg.get("role", "?")
        content = msg.get("content", "")

        if role == "user":
            L.append("---")
            L.append(f"### Student [{i}]")
            L.append("")
            # Student messages are plain text — render as blockquote
            for line in content.split("\n"):
                L.append(f"> {line}")
            L.append("")
        else:
            L.append(f"### Tutor [{i}]")
            L.append("")
            # Tutor messages may contain markdown (headers, tables, code blocks).
            # Wrap in a <details> so nested markdown doesn't break the outer doc.
            L.append("<details open><summary>Tutor response</summary>")
            L.append("")
            L.append(content)
            L.append("")
            L.append("</details>")
            L.append("")

    return "\n".join(L)


def update_evaluation_status(result_dir: Path, status: str) -> None:
    """Update the ``evaluation_status`` field in an existing run_state.json."""
    state_path = Path(result_dir) / "run_state.json"
    if not state_path.exists():
        return
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        data["evaluation_status"] = status
        state_path.write_text(
            json.dumps(data, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("Failed to update evaluation_status: %s", exc)
