"""Run execution endpoint — wraps the existing CLI run-single path."""

import json
import logging
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
log = logging.getLogger(__name__)

_BENCH_ROOT = Path(__file__).parent.parent.parent

# Ensure bench root is on sys.path for imports
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

# ── Run state (single run at a time) ─────────────────────────────────

_current_run: dict = {
    "running": False,
    "task_id": None,
    "persona_id": None,
    "agent": None,
    "model": None,
    "started_at": None,
    "error": None,
}
_run_lock = threading.Lock()

# ── Eval state (concurrent evals allowed) ────────────────────────────

_active_evals: dict[str, dict] = {}  # key: "{task_id}__{persona_id}"
_eval_lock = threading.Lock()
_eval_cancel: dict[str, threading.Event] = {}  # key → cancel event


class RunRequest(BaseModel):
    task_id: str
    persona_id: str
    agent: str = "anthropic"
    docker: bool = True
    max_turns: Optional[int] = None
    model: Optional[str] = None
    skip_eval: bool = True


class RunGroupRequest(BaseModel):
    group: str
    agent: str = "anthropic"
    persona: Optional[str] = None
    docker: bool = True
    max_turns: Optional[int] = None
    model: Optional[str] = None
    workers: int = 3


class EvalRequest(BaseModel):
    source: str = "run-single"
    agent: str
    model: str
    category: str
    task_id: str
    persona_id: str


@router.post("/run")
async def start_run(req: RunRequest):
    with _run_lock:
        if _current_run["running"]:
            raise HTTPException(
                409,
                f"A run is already in progress: {_current_run['task_id']}",
            )
        _current_run.update(
            running=True,
            task_id=req.task_id,
            persona_id=req.persona_id,
            agent=req.agent,
            model=req.model,
            started_at=time.time(),
            error=None,
        )

    thread = threading.Thread(
        target=_execute_run,
        args=(req,),
        daemon=True,
        name="web-run",
    )
    thread.start()
    return {"status": "started", "task_id": req.task_id, "persona_id": req.persona_id}


@router.get("/status")
async def get_status():
    info = dict(_current_run)
    if info["started_at"] and info["running"]:
        info["elapsed_seconds"] = round(time.time() - info["started_at"], 1)
    info["active_evals"] = list(_active_evals.values())
    return info


def _execute_run(req: RunRequest):
    """Run in background thread — reuses the existing orchestrator path."""
    from orchestrator.live_monitor import emit

    emit(
        "session_start",
        {
            "task_id": req.task_id,
            "persona_id": req.persona_id,
            "agent": req.agent,
        },
    )

    try:
        from config.model_resolver import get_model_for_agent
        from orchestrator.runners.job_runner import JobSpec, run_single_job
        from orchestrator.schemas import QuantTutorTask, StudentPersona

        # Load task
        task = None
        for category_dir in (_BENCH_ROOT / "tasks" / "layer2").iterdir():
            if not category_dir.is_dir():
                continue
            for f in category_dir.glob("*.json"):
                if req.task_id in f.stem:
                    with open(f) as fh:
                        task = QuantTutorTask(**json.load(fh))
                    break
            if task:
                break
        if not task:
            raise ValueError(f"Task not found: {req.task_id}")

        # Load persona
        persona_path = _BENCH_ROOT / "personas" / f"{req.persona_id}.json"
        with open(persona_path) as f:
            persona = StudentPersona(**json.load(f))

        # Build result dir
        model = req.model or get_model_for_agent(req.agent)
        model_short = model.split("/")[-1] if "/" in model else model
        result_base_dir = (
            _BENCH_ROOT / "results" / "run-single" / req.agent / model_short
        )

        job = JobSpec(
            task=task,
            persona=persona,
            agent_type=req.agent,
            condition_name="agent",
            max_turns=req.max_turns,
            use_docker=req.docker,
            save_result=True,
            result_base_dir=result_base_dir,
            model_override=req.model,
            skip_eval=req.skip_eval,
        )

        job_result = run_single_job(job)
        if job_result.error:
            log.error("Run failed: %s", job_result.error)
            _current_run["error"] = job_result.error

    except Exception as exc:
        log.error("Run exception: %s", exc, exc_info=True)
        _current_run["error"] = str(exc)
    finally:
        _current_run["running"] = False
        # Structured fields for frontend route construction (no local paths)
        end_payload = {
            "task_id": req.task_id,
            "persona_id": req.persona_id,
            "error": _current_run.get("error"),
        }
        try:
            end_payload["source"] = "run-single"
            end_payload["agent"] = req.agent
            end_payload["model"] = model_short
            end_payload["category"] = task.category.value
        except Exception:
            pass
        emit("session_end", end_payload)


# ── Group run endpoint ──────────────────────────────────────────────

_group_run: dict = {"running": False}
_group_lock = threading.Lock()


@router.post("/run-group")
async def start_group_run(req: RunGroupRequest):
    with _group_lock:
        if _group_run["running"]:
            raise HTTPException(409, "A group run is already in progress")
        _group_run["running"] = True

    thread = threading.Thread(
        target=_execute_group_run,
        args=(req,),
        daemon=True,
        name="web-run-group",
    )
    thread.start()

    # Return estimated job count
    group_dir = _BENCH_ROOT / "tasks" / "layer2" / req.group
    task_count = len(list(group_dir.glob("*.json"))) if group_dir.is_dir() else 0
    estimated_jobs = task_count * (1 if req.persona else 3)  # rough estimate
    return {"status": "started", "group": req.group, "total_jobs": estimated_jobs}


def _execute_group_run(req: RunGroupRequest):
    """Run all tasks in a group — reuses the existing parallel runner."""
    from orchestrator.live_monitor import emit

    try:
        from config.model_resolver import get_model_for_agent
        from orchestrator.runners.job_runner import JobSpec, run_single_job
        from orchestrator.runners.parallel_runner import run_jobs_parallel
        from orchestrator.schemas import QuantTutorTask, StudentPersona

        group_dir = _BENCH_ROOT / "tasks" / "layer2" / req.group
        if not group_dir.is_dir():
            raise ValueError(f"Group not found: {req.group}")

        # Load all tasks in group
        tasks = []
        for f in sorted(group_dir.glob("*.json")):
            with open(f) as fh:
                tasks.append(QuantTutorTask(**json.load(fh)))

        # Build jobs
        model = req.model or get_model_for_agent(req.agent)
        model_short = model.split("/")[-1] if "/" in model else model
        result_base_dir = (
            _BENCH_ROOT / "results" / "run-group" / req.agent / model_short
        )

        jobs = []
        job_list = []  # for the SSE event
        for task in tasks:
            pids = [req.persona] if req.persona else task.persona_ids
            for pid in pids:
                persona_path = _BENCH_ROOT / "personas" / f"{pid}.json"
                with open(persona_path) as f:
                    persona = StudentPersona(**json.load(f))
                jobs.append(
                    JobSpec(
                        task=task,
                        persona=persona,
                        agent_type=req.agent,
                        condition_name="agent",
                        max_turns=req.max_turns,
                        use_docker=req.docker,
                        save_result=True,
                        result_base_dir=result_base_dir,
                        model_override=req.model,
                        skip_eval=True,
                    )
                )
                job_list.append({"task_id": task.task_id, "persona_id": pid})

        emit(
            "group_start",
            {
                "group": req.group,
                "agent": req.agent,
                "model": model_short,
                "total_jobs": len(jobs),
                "jobs": job_list,
            },
        )

        ok_count = 0
        err_count = 0

        def progress_cb(completed, total, result):
            nonlocal ok_count, err_count
            if result and result.task_result:
                ok_count += 1
                scores = (
                    {
                        "oas": result.task_result.overall_score,
                        "qr": result.task_result.quant_result_score,
                        "qp": result.task_result.quant_process_score,
                    }
                    if not result.job.skip_eval
                    else None
                )
                emit(
                    "group_task_end",
                    {
                        "task_id": result.job.task.task_id,
                        "persona_id": result.job.persona.persona_id,
                        "error": None,
                        "duration": result.duration_seconds,
                        "scores": scores,
                    },
                )
            elif result:
                err_count += 1
                emit(
                    "group_task_end",
                    {
                        "task_id": result.job.task.task_id,
                        "persona_id": result.job.persona.persona_id,
                        "error": result.error or "Unknown error",
                        "duration": result.duration_seconds,
                    },
                )

        # Emit task_start for each job as they begin
        original_run = run_single_job

        def tracked_run(job):
            emit(
                "group_task_start",
                {
                    "task_id": job.task.task_id,
                    "persona_id": job.persona.persona_id,
                },
            )
            return original_run(job)

        workers = min(req.workers, len(jobs)) if jobs else 1
        run_jobs_parallel(
            jobs,
            max_workers=workers,
            progress_callback=progress_cb,
            job_fn=tracked_run,
        )

        emit(
            "group_end",
            {
                "group": req.group,
                "total": len(jobs),
                "ok_count": ok_count,
                "err_count": err_count,
            },
        )

    except Exception as exc:
        log.error("Group run exception: %s", exc, exc_info=True)
        emit(
            "group_end",
            {
                "group": req.group,
                "total": 0,
                "ok_count": 0,
                "err_count": 0,
                "error": str(exc),
            },
        )
    finally:
        with _group_lock:
            _group_run["running"] = False


# ── Eval-only endpoint (concurrent) ─────────────────────────────────


@router.post("/eval")
async def start_eval(req: EvalRequest):
    key = f"{req.task_id}__{req.persona_id}"
    with _eval_lock:
        if key in _active_evals:
            raise HTTPException(
                409,
                f"Already evaluating: {req.task_id} / {req.persona_id}",
            )
        _active_evals[key] = {
            "task_id": req.task_id,
            "persona_id": req.persona_id,
            "agent": req.agent,
            "model": req.model,
            "started_at": time.time(),
        }

    cancel_ev = threading.Event()
    with _eval_lock:
        _eval_cancel[key] = cancel_ev

    thread = threading.Thread(
        target=_execute_eval,
        args=(req, cancel_ev),
        daemon=True,
        name=f"web-eval-{req.task_id}",
    )
    thread.start()
    return {"status": "started", "task_id": req.task_id, "persona_id": req.persona_id}


class EvalStopRequest(BaseModel):
    task_id: str
    persona_id: str


@router.post("/eval/stop")
async def stop_eval(req: EvalStopRequest):
    key = f"{req.task_id}__{req.persona_id}"
    with _eval_lock:
        cancel_ev = _eval_cancel.get(key)
        if not cancel_ev:
            raise HTTPException(
                404, f"No active eval for {req.task_id} / {req.persona_id}"
            )
        cancel_ev.set()
    return {"status": "stopping", "task_id": req.task_id, "persona_id": req.persona_id}


def _execute_eval(req: EvalRequest, cancel_event: threading.Event | None = None):
    """Evaluate a previously saved run_state.json result."""
    from orchestrator.live_monitor import emit

    key = f"{req.task_id}__{req.persona_id}"

    emit(
        "session_start",
        {
            "task_id": req.task_id,
            "persona_id": req.persona_id,
            "agent": req.agent,
            "mode": "eval",
        },
    )

    result_base_dir = _BENCH_ROOT / "results" / req.source / req.agent / req.model

    def _check_cancel():
        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Evaluation cancelled by user")

    try:
        _check_cancel()

        from orchestrator.runners.job_runner import JobSpec, eval_single_job
        from orchestrator.schemas import QuantTutorTask, StudentPersona

        # Load task
        task = None
        for category_dir in (_BENCH_ROOT / "tasks" / "layer2").iterdir():
            if not category_dir.is_dir():
                continue
            for f in category_dir.glob("*.json"):
                if req.task_id in f.stem:
                    with open(f) as fh:
                        task = QuantTutorTask(**json.load(fh))
                    break
            if task:
                break
        if not task:
            raise ValueError(f"Task not found: {req.task_id}")

        _check_cancel()

        # Load persona
        persona_path = _BENCH_ROOT / "personas" / f"{req.persona_id}.json"
        with open(persona_path) as f:
            persona = StudentPersona(**json.load(f))

        job = JobSpec(
            task=task,
            persona=persona,
            agent_type=req.agent,
            condition_name="agent",
            max_turns=None,
            use_docker=False,
            save_result=True,
            result_base_dir=result_base_dir,
            model_override=req.model,
            skip_eval=False,
        )

        _check_cancel()

        job_result = eval_single_job(job)

        _check_cancel()

        # Extract final scores for the session_end event
        scores = None
        if job_result.task_result:
            tr = job_result.task_result
            scores = {
                "qr": tr.quant_result_score,
                "qp": tr.quant_process_score,
                "oas": tr.overall_score,
            }

        if job_result.error:
            log.error("Eval failed: %s", job_result.error)

        emit(
            "session_end",
            {
                "task_id": req.task_id,
                "persona_id": req.persona_id,
                "error": job_result.error,
                "mode": "eval",
                "scores": scores,
                "source": req.source,
                "agent": req.agent,
                "model": req.model,
                "category": req.category,
            },
        )

    except InterruptedError:
        log.info("Eval cancelled: %s / %s", req.task_id, req.persona_id)
        emit(
            "session_end",
            {
                "task_id": req.task_id,
                "persona_id": req.persona_id,
                "error": "cancelled",
                "mode": "eval",
                "source": req.source,
                "agent": req.agent,
                "model": req.model,
                "category": req.category,
            },
        )
    except Exception as exc:
        log.error("Eval exception: %s", exc, exc_info=True)
        emit(
            "session_end",
            {
                "task_id": req.task_id,
                "persona_id": req.persona_id,
                "error": str(exc),
                "mode": "eval",
                "source": req.source,
                "agent": req.agent,
                "model": req.model,
                "category": req.category,
            },
        )
    finally:
        with _eval_lock:
            _active_evals.pop(key, None)
            _eval_cancel.pop(key, None)
