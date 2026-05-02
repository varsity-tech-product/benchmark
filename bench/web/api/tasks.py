"""Task and persona listing endpoints."""

import json
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

_BENCH_ROOT = Path(__file__).parent.parent.parent
_TASKS_DIR = _BENCH_ROOT / "tasks" / "layer2"
_PERSONAS_DIR = _BENCH_ROOT / "personas"


@router.get("/tasks")
async def list_tasks():
    tasks = []
    for category_dir in sorted(_TASKS_DIR.iterdir()):
        if not category_dir.is_dir():
            continue
        for f in sorted(category_dir.glob("*.json")):
            with open(f) as fh:
                t = json.load(fh)
            tasks.append(
                {
                    "task_id": t["task_id"],
                    "category": t.get("category", category_dir.name),
                    "difficulty": t.get("difficulty", ""),
                    "description": t.get("description", ""),
                    "persona_id": t.get("persona_id", ""),
                    "max_turns": t.get("max_turns", 10),
                    "timeout_minutes": t.get("timeout_minutes"),
                    "requires_code": t.get("requires_code", False),
                }
            )
    return tasks


@router.get("/tasks/{task_id}")
async def get_task(task_id: str):
    for category_dir in _TASKS_DIR.iterdir():
        if not category_dir.is_dir():
            continue
        for f in category_dir.glob("*.json"):
            if task_id in f.stem:
                with open(f) as fh:
                    return json.load(fh)
    raise HTTPException(404, f"Task not found: {task_id}")


@router.get("/personas")
async def list_personas():
    personas = []
    for f in sorted(_PERSONAS_DIR.glob("*.json")):
        with open(f) as fh:
            p = json.load(fh)
        personas.append(
            {
                "persona_id": p["persona_id"],
                "knowledge_level": p.get("knowledge_level", ""),
                "description": p.get("description", ""),
            }
        )
    return personas
