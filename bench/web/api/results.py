"""Result browsing endpoints."""

import json
import mimetypes
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter()

_BENCH_ROOT = Path(__file__).parent.parent.parent
_RESULTS_DIRS = {
    "run-single": _BENCH_ROOT / "results" / "run-single",
    "run-group": _BENCH_ROOT / "results" / "run-group",
}


def _scan_results_dir(base_dir: Path, source: str) -> list[dict]:
    """Scan a results directory tree and return result entries."""
    results = []
    if not base_dir.exists():
        return results
    for agent_dir in sorted(base_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        agent = agent_dir.name
        for model_dir in sorted(agent_dir.iterdir()):
            if not model_dir.is_dir():
                continue
            model = model_dir.name
            for category_dir in sorted(model_dir.iterdir()):
                if not category_dir.is_dir():
                    continue
                category = category_dir.name
                for task_dir in sorted(category_dir.iterdir()):
                    if not task_dir.is_dir():
                        continue
                    task_id = task_dir.name
                    for persona_dir in sorted(task_dir.iterdir()):
                        if not persona_dir.is_dir():
                            continue
                        persona_id = persona_dir.name
                        run_state = persona_dir / "run_state.json"
                        if not run_state.exists():
                            continue
                        try:
                            state = json.loads(run_state.read_text())
                        except (json.JSONDecodeError, OSError):
                            continue
                        results.append(
                            {
                                "source": source,
                                "agent": agent,
                                "model": model,
                                "category": category,
                                "task_id": task_id,
                                "persona_id": persona_id,
                                "duration_seconds": state.get("duration_seconds", 0),
                                "turn_count": len(state.get("conversation", [])),
                                "tool_count": len(state.get("tool_logs", [])),
                                "has_scores": (persona_dir / "scores.md").exists(),
                                "timestamp": run_state.stat().st_mtime,
                                "path": str(persona_dir.relative_to(_BENCH_ROOT)),
                            }
                        )
    return results


@router.get("/results")
async def list_results():
    """Scan results/run-single/ and results/run-group/ and return all saved runs."""
    results = []
    for source, base_dir in _RESULTS_DIRS.items():
        results.extend(_scan_results_dir(base_dir, source))
    return results


@router.get("/results/{source}/{agent}/{model}/{category}/{task_id}/{persona_id}")
async def get_result(
    source: str,
    agent: str,
    model: str,
    category: str,
    task_id: str,
    persona_id: str,
):
    """Return the full run_state.json for a specific run."""
    base = _RESULTS_DIRS.get(source)
    if not base:
        raise HTTPException(404, f"Unknown source: {source}")
    result_dir = base / agent / model / category / task_id / persona_id
    run_state = result_dir / "run_state.json"
    if not run_state.exists():
        raise HTTPException(404, "Result not found")
    state = json.loads(run_state.read_text())
    state["_timestamp"] = run_state.stat().st_mtime
    state["_source"] = source
    # Attach scores if available
    scores_file = result_dir / "scores.md"
    if scores_file.exists():
        state["_scores_md"] = scores_file.read_text()
    cost_file = result_dir / "cost.md"
    if cost_file.exists():
        state["_cost_md"] = cost_file.read_text()
    return state


@router.get(
    "/results/{source}/{agent}/{model}/{category}/{task_id}/{persona_id}/files/{filename}"
)
async def get_result_file(
    source: str,
    agent: str,
    model: str,
    category: str,
    task_id: str,
    persona_id: str,
    filename: str,
):
    """Serve a file from agent_files/ (e.g. plots, CSVs) for the given run."""
    base = _RESULTS_DIRS.get(source)
    if not base:
        raise HTTPException(404, f"Unknown source: {source}")
    result_dir = base / agent / model / category / task_id / persona_id
    agent_files = result_dir / "agent_files"
    target = agent_files / filename

    # Security: ensure resolved path stays inside agent_files
    try:
        target.resolve().relative_to(agent_files.resolve())
    except (ValueError, RuntimeError):
        raise HTTPException(403, "Access denied")

    if not target.is_file():
        raise HTTPException(404, f"File not found: {filename}")

    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(str(target), media_type=media_type)
