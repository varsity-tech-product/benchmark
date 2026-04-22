"""Result persistence for QuantTutorBench Server.

Saves session data as ``run_state.json`` only.

::

    results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:12]}/
        run_state.json
        .session_id
        agent_files/

"""

import json
import logging
import os
import shutil
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
) -> Path:
    """Save ``run_state.json`` and ``.session_id``."""
    result_dir = Path(result_dir)
    result_dir.mkdir(parents=True, exist_ok=True)

    tool_logs_dicts = []
    for log in tool_logs:
        if isinstance(log, dict):
            tool_logs_dicts.append(log)
        else:
            tool_logs_dicts.append(asdict(log))

    step_count = sum(
        1 for log in tool_logs_dicts if log.get("name") not in NON_SUBSTANTIVE_TOOLS
    )

    workspace_files = []
    if workspace_path and os.path.isdir(workspace_path):
        workspace_files = sorted(
            os.path.relpath(os.path.join(root, f), workspace_path)
            for root, _, files in os.walk(workspace_path)
            for f in files
        )

    agent_files_dir = result_dir / "agent_files"
    if workspace_path and os.path.isdir(workspace_path):
        if agent_files_dir.exists():
            shutil.rmtree(agent_files_dir)
        shutil.copytree(workspace_path, str(agent_files_dir))

    key_results = {}
    trace_summary = []
    try:
        from server.storage.trace_utils import build_trace_summary, extract_key_results

        ws = str(agent_files_dir) if agent_files_dir.exists() else ""
        key_results = extract_key_results(ws, tool_logs_dicts)
        trace_summary = build_trace_summary(tool_logs_dicts)
    except Exception as exc:
        logger.debug("Could not build reference fields: %s", exc)

    from server.storage.format_validator import validate_run_state as _validate

    fmt_state = {"conversation": conversation, "tool_logs": tool_logs_dicts}
    fmt_ok, fmt_errors = _validate(fmt_state)

    state = {
        "run_id": run_id,
        "public_task_label": public_task_label,
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
    }

    state_path = result_dir / "run_state.json"
    state_path.write_text(
        json.dumps(state, indent=2, default=str),
        encoding="utf-8",
    )

    if session_id:
        (result_dir / ".session_id").write_text(session_id.strip(), encoding="utf-8")

    logger.info("Saved run_state.json to %s", state_path.parent)
    return state_path
