"""Parallel job execution for QuantTutorBench.

Orchestrates concurrent execution of multiple benchmark jobs using
a ThreadPoolExecutor. Each job runs in full isolation (own agent,
container, proxy, workspace).

Threading fix: DeepEval's ConversationSimulator uses Rich Progress
bars via a global console singleton. Rich only allows one live display
per process, so concurrent simulate() calls raise LiveError and return
empty results. We monkey-patch the progress contexts to thread-safe
no-ops before launching the pool.
"""

import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import contextmanager
from typing import Callable, Optional

from orchestrator.runners.job_runner import JobResult, JobSpec, run_single_job

# ---------------------------------------------------------------------------
# DeepEval Rich-progress thread-safety patch
# ---------------------------------------------------------------------------

_PATCH_LOCK = threading.Lock()
_PATCHED = False


def _apply_deepeval_progress_patch():
    """Replace DeepEval's Rich-based progress contexts with thread-safe no-ops.

    Root cause: DeepEval's `conversation_simulator_progress_context` and
    `metrics_progress_context` return Rich `Progress` objects that are used
    as nested context managers.  Rich enforces a global "only one live
    display" rule via `Console._live`, so the second thread to enter the
    context raises `LiveError` — causing `simulate()` to silently return
    an empty list.

    Fix: replace both context managers with lightweight no-ops that yield
    the same (progress=None, pbar_id=None) tuple so downstream code
    (which already handles None progress) works unchanged.
    """
    global _PATCHED
    with _PATCH_LOCK:
        if _PATCHED:
            return
        try:
            import deepeval.progress_context as _pc
            import deepeval.simulator.conversation_simulator as _cs

            class _DummyProgress:
                """Mimics Rich Progress as a context manager but does nothing.

                DeepEval's simulate() uses a double-context-manager pattern:
                    with ctx_fn(...) as (progress, pbar_id), progress:
                The second ", progress" calls progress.__enter__(), so we
                need an object that supports the context-manager protocol.

                It must also implement add_task() (called by add_pbar()),
                remove_task() (called by remove_pbars()), and .tasks
                (iterated by update_pbar()).
                """

                def __enter__(self):
                    return self

                def __exit__(self, *_):
                    return False

                # update_pbar accesses .tasks; provide an empty list so
                # next(... for t in progress.tasks ...) returns nothing.
                tasks = []

                def add_task(self, description, **kwargs):
                    """No-op: returns None as task ID."""
                    return None

                def remove_task(self, task_id):
                    """No-op."""
                    pass

            _dummy = _DummyProgress()

            @contextmanager
            def _noop_progress(**_kwargs):
                yield _dummy, None

            # Patch the factory functions
            _pc.conversation_simulator_progress_context = _noop_progress
            _cs.conversation_simulator_progress_context = _noop_progress

            # Also patch metrics_progress_context used by GEval etc.
            if hasattr(_pc, "metrics_progress_context"):
                _pc.metrics_progress_context = _noop_progress
        except ImportError:
            pass  # deepeval not installed — nothing to patch
        _PATCHED = True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_jobs_parallel(
    jobs: list[JobSpec],
    max_workers: int = 3,
    progress_callback: Optional[Callable] = None,
    create_agent_fn=None,
    job_fn: Optional[Callable] = None,
) -> list[JobResult]:
    """Run multiple benchmark jobs in parallel.

    Args:
        jobs: List of job specifications.
        max_workers: ThreadPoolExecutor concurrency limit.
        progress_callback: Called with (completed, total, latest_result).
        create_agent_fn: Optional agent factory, forwarded to run_single_job.
        job_fn: Custom job executor. If provided, called as job_fn(job)
            instead of run_single_job. Used by --evalonly.

    Returns:
        List of JobResult in same order as input jobs.
    """
    if not jobs:
        return []

    def _default_fn(job):
        return run_single_job(job, create_agent_fn=create_agent_fn)

    _fn = job_fn or _default_fn

    # Don't spawn more threads than jobs
    max_workers = min(max_workers, len(jobs))

    # Single job: skip pool overhead (no patch needed)
    if len(jobs) == 1:
        result = _fn(jobs[0])
        if progress_callback:
            progress_callback(1, 1, result)
        return [result]

    # Multi-job: patch DeepEval progress to avoid Rich LiveError
    _apply_deepeval_progress_patch()

    results: list[Optional[JobResult]] = [None] * len(jobs)
    completed = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        future_to_idx = {pool.submit(_fn, job): i for i, job in enumerate(jobs)}

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as e:
                results[idx] = JobResult(
                    job=jobs[idx],
                    task_result=None,
                    trace_captured={},
                    error=str(e),
                    duration_seconds=0.0,
                )
            completed += 1
            if progress_callback:
                progress_callback(completed, len(jobs), results[idx])

    return results
