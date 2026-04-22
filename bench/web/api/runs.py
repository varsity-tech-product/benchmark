"""Run execution endpoint — wraps the existing CLI run-single path."""

import concurrent.futures
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

# ── Run state (concurrent single runs) ───────────────────────────────

_single_runs: dict[str, dict] = {}  # key: "task_id__persona_id" → run info
_single_cancels: dict[str, threading.Event] = {}  # key → cancel event
_run_lock = threading.Lock()

# ── Group run cancel ──────────────────────────────────────────────────
_group_cancel: threading.Event | None = None  # cancel event for the active group run

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
    timeout_minutes: Optional[int] = None
    model: Optional[str] = None
    skip_eval: bool = True


class RunGroupRequest(BaseModel):
    group: str
    agent: str = "anthropic"
    persona: Optional[str] = None
    docker: bool = True
    max_turns: Optional[int] = None
    timeout_minutes: Optional[int] = None
    model: Optional[str] = None
    workers: int = 3
    skip_eval: bool = True


class EvalRequest(BaseModel):
    source: str = "run-single"
    agent: str
    model: str
    category: str
    task_id: str
    persona_id: str


class RunStopRequest(BaseModel):
    job_key: str


@router.post("/run")
async def start_run(req: RunRequest):
    key = f"{req.task_id}__{req.persona_id}"
    # [DIAG]
    _prev_running = _single_runs.get(key, {}).get("running", "N/A")
    log.info("[DIAG] POST /run key=%s prev_running=%s", key, _prev_running)
    with _run_lock:
        if key in _single_runs and _single_runs[key].get("running"):
            log.info("[DIAG] POST /run REJECTED 409 key=%s", key)
            raise HTTPException(
                409,
                f"Already running: {req.task_id} / {req.persona_id}",
            )
        cancel_ev = threading.Event()
        _single_runs[key] = {
            "running": True,
            "task_id": req.task_id,
            "persona_id": req.persona_id,
            "agent": req.agent,
            "model": req.model,
            "started_at": time.time(),
            "error": None,
        }
        _single_cancels[key] = cancel_ev

    thread = threading.Thread(
        target=_execute_run,
        args=(req, key, cancel_ev),
        daemon=True,
        name=f"web-run-{key}",
    )
    thread.start()
    log.info("[DIAG] POST /run thread started key=%s thread=%s", key, thread.name)
    return {
        "status": "started",
        "task_id": req.task_id,
        "persona_id": req.persona_id,
        "job_key": key,
    }


@router.post("/run/stop")
async def stop_run(req: RunStopRequest):
    with _run_lock:
        info = _single_runs.get(req.job_key)
        if not info or not info.get("running"):
            raise HTTPException(404, f"No active run for key: {req.job_key}")
        cancel_ev = _single_cancels.get(req.job_key)
        if cancel_ev:
            cancel_ev.set()
    return {"status": "stopping", "job_key": req.job_key}


@router.get("/run/active")
async def list_active_runs():
    """Return list of currently running single runs (for frontend sync)."""
    with _run_lock:
        return [
            {
                "job_key": k,
                "task_id": v["task_id"],
                "persona_id": v["persona_id"],
                "agent": v.get("agent"),
                "running": v.get("running", False),
            }
            for k, v in _single_runs.items()
            if v.get("running")
        ]


@router.get("/agent-models")
async def get_agent_models():
    """Return available models per agent type. First item is the default."""
    from config.llm_config import AGENT_MODEL_MAP

    return {agent: [native] for agent, (native, _or) in AGENT_MODEL_MAP.items()}


@router.get("/status")
async def get_status():
    with _run_lock:
        any_running = any(v.get("running") for v in _single_runs.values())
    info = {
        "running": any_running,
        "active_runs": len([v for v in _single_runs.values() if v.get("running")]),
        "active_evals": list(_active_evals.values()),
        "group_running": _group_run.get("running", False),
    }
    return info


def _disable_rich_live():
    """Disable DeepEval's Rich-based progress bars for web-mode runs.

    Reuses the same patch that parallel_runner uses for run-group:
    replaces DeepEval's progress context managers with thread-safe no-ops.
    This is process-global and idempotent (guarded by _PATCHED flag + lock).
    """
    from orchestrator.runners.parallel_runner import _apply_deepeval_progress_patch

    _apply_deepeval_progress_patch()


def _execute_run(
    req: RunRequest, job_key: str, cancel_event: threading.Event | None = None
):
    """Run in background thread — reuses the existing orchestrator path."""
    from orchestrator.live_monitor import emit, set_job_key

    # Disable Rich Live display to allow concurrent simulations
    _disable_rich_live()

    # Tag all SSE events from this thread with job_key
    set_job_key(job_key)

    _t0 = time.time()
    # [DIAG] Backend session_start
    log.info(
        "[DIAG] session_start key=%s t=%.3f thread=%s",
        job_key,
        _t0,
        threading.current_thread().name,
    )
    emit(
        "session_start",
        {
            "task_id": req.task_id,
            "persona_id": req.persona_id,
            "agent": req.agent,
        },
    )

    model_short = None
    task = None
    error = None

    try:
        from config.model_resolver import get_model_for_agent
        from orchestrator.runners.job_runner import JobSpec, run_single_job
        from orchestrator.schemas import QuantTutorTask, StudentPersona

        # Load task
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
            timeout_minutes=req.timeout_minutes,
        )

        job_result = run_single_job(job, cancel_event=cancel_event)
        if job_result and job_result.error:
            log.error("Run failed: %s", job_result.error)
            error = job_result.error

    except InterruptedError:
        log.info("Run cancelled: %s / %s", req.task_id, req.persona_id)
        error = "cancelled"
    except Exception as exc:
        log.error("Run exception: %s", exc, exc_info=True)
        error = str(exc)
    finally:
        set_job_key(None)
        with _run_lock:
            if job_key in _single_runs:
                _single_runs[job_key]["running"] = False
                _single_runs[job_key]["error"] = error
            _single_cancels.pop(job_key, None)
        # Structured fields for frontend route construction
        end_payload = {
            "task_id": req.task_id,
            "persona_id": req.persona_id,
            "error": error,
        }
        try:
            end_payload["source"] = "run-single"
            end_payload["agent"] = req.agent
            end_payload["model"] = model_short
            end_payload["category"] = task.category.value
        except Exception:
            pass
        # [DIAG] Backend session_end
        _t1 = time.time()
        log.info(
            "[DIAG] session_end key=%s error=%s elapsed=%.1fs thread=%s",
            job_key,
            error,
            _t1 - _t0,
            threading.current_thread().name,
        )
        # Re-set job_key briefly for the session_end emit
        set_job_key(job_key)
        emit("session_end", end_payload)
        set_job_key(None)


# ── Group run endpoint ──────────────────────────────────────────────

_group_run: dict = {"running": False}
_group_lock = threading.Lock()


@router.post("/run-group")
async def start_group_run(req: RunGroupRequest):
    global _group_cancel
    with _group_lock:
        if _group_run["running"]:
            raise HTTPException(409, "A group run is already in progress")
        _group_run["running"] = True
        _group_cancel = threading.Event()

    thread = threading.Thread(
        target=_execute_group_run,
        args=(req, _group_cancel),
        daemon=True,
        name="web-run-group",
    )
    thread.start()

    # Return estimated job count
    group_dir = _BENCH_ROOT / "tasks" / "layer2" / req.group
    task_count = len(list(group_dir.glob("*.json"))) if group_dir.is_dir() else 0
    estimated_jobs = task_count * (1 if req.persona else 3)  # rough estimate
    return {"status": "started", "group": req.group, "total_jobs": estimated_jobs}


@router.post("/run-group/stop")
async def stop_group_run():
    global _group_cancel
    with _group_lock:
        if not _group_run["running"]:
            raise HTTPException(404, "No group run in progress")
        if _group_cancel:
            _group_cancel.set()
    return {"status": "stopping"}


def _execute_group_run(
    req: RunGroupRequest, cancel_event: threading.Event | None = None
):
    """Run all tasks in a group — reuses the existing parallel runner."""
    global _group_cancel
    from orchestrator.live_monitor import emit

    try:
        from config.model_resolver import get_model_for_agent
        from orchestrator.runners.job_runner import JobResult, JobSpec, run_single_job
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
                        skip_eval=req.skip_eval,
                        timeout_minutes=req.timeout_minutes,
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
            if result and result.error:
                err_count += 1
                emit(
                    "group_task_end",
                    {
                        "task_id": result.job.task.task_id,
                        "persona_id": result.job.persona.persona_id,
                        "error": result.error,
                        "duration": result.duration_seconds,
                    },
                )
            elif result and result.task_result:
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
                        "error": "Unknown error",
                        "duration": result.duration_seconds,
                    },
                )

        # Emit task_start for each job as they begin; skip job if cancelled
        original_run = run_single_job

        def tracked_run(job):
            if cancel_event and cancel_event.is_set():
                # Return an error result immediately without running the job
                return JobResult(
                    job=job,
                    task_result=None,
                    trace_captured={},
                    error="cancelled",
                    duration_seconds=0.0,
                )
            job_key = job.task.task_id + "__" + job.persona.persona_id
            emit(
                "group_task_start",
                {
                    "task_id": job.task.task_id,
                    "persona_id": job.persona.persona_id,
                },
            )
            # Set thread-local job_key so all emit() calls from this job
            # are tagged — allows frontend to route events per-task.
            from orchestrator.live_monitor import set_job_key

            set_job_key(job_key)
            try:
                return original_run(job, cancel_event=cancel_event)
            finally:
                set_job_key(None)

        workers = min(req.workers, len(jobs)) if jobs else 1
        run_jobs_parallel(
            jobs,
            max_workers=workers,
            progress_callback=progress_cb,
            job_fn=tracked_run,
        )

        if cancel_event and cancel_event.is_set():
            raise InterruptedError("Group run cancelled by user")

        emit(
            "group_end",
            {
                "group": req.group,
                "total": len(jobs),
                "ok_count": ok_count,
                "err_count": err_count,
            },
        )

    except InterruptedError:
        log.info("Group run cancelled: %s", req.group)
        emit(
            "group_end",
            {
                "group": req.group,
                "total": len(jobs) if "jobs" in dir() else 0,
                "ok_count": ok_count,
                "err_count": err_count,
                "error": "cancelled",
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
        _group_cancel = None
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


@router.get("/eval/active")
async def list_active_evals():
    """Return list of currently running eval jobs (for frontend sync)."""
    with _eval_lock:
        return list(_active_evals.values())


def _archive_previous_scores(result_base_dir: Path, req: "EvalRequest") -> None:
    """If scores.md exists, append it to score_history.json before overwriting."""
    result_dir = result_base_dir / req.category / req.task_id / req.persona_id
    scores_file = result_dir / "scores.md"
    if not scores_file.exists():
        return
    try:
        history_file = result_dir / "score_history.json"
        history: list = (
            json.loads(history_file.read_text()) if history_file.exists() else []
        )
        history.append(
            {
                "timestamp": scores_file.stat().st_mtime,
                "scores_md": scores_file.read_text(),
            }
        )
        history_file.write_text(json.dumps(history, indent=2, ensure_ascii=False))
    except Exception as exc:
        log.warning(
            "Failed to archive scores for %s/%s: %s", req.task_id, req.persona_id, exc
        )


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

        # Archive any existing scores before overwriting (re-evaluation history)
        _archive_previous_scores(result_base_dir, req)

        # Run eval in a sub-thread so cancel_event can interrupt it within ~0.5 s
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as exe:
            future = exe.submit(eval_single_job, job, cancel_event=cancel_event)
            while not future.done():
                if cancel_event and cancel_event.is_set():
                    # Wait briefly for the eval thread to notice the cancel
                    try:
                        future.result(timeout=3.0)
                    except concurrent.futures.TimeoutError:
                        pass
                    except InterruptedError:
                        pass
                    raise InterruptedError("Evaluation cancelled by user")
                time.sleep(0.5)
        job_result = future.result()

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
