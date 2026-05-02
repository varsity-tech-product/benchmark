"""Shared async utilities for metric evaluation."""

import asyncio
import threading

import nest_asyncio

# ──────────────────────────────────────────────────────────────
# Concurrency control (shared by Tutor 6D and QP evaluators)
# ──────────────────────────────────────────────────────────────
_CONCURRENCY = 20


def set_eval_concurrency(n: int) -> None:
    """Set per-worker concurrency limit (called by parallel runner)."""
    global _CONCURRENCY
    _CONCURRENCY = max(3, n)


def get_eval_concurrency() -> int:
    """Return current concurrency limit."""
    return _CONCURRENCY


# ──────────────────────────────────────────────────────────────
# Abort sentinel and guarded gather
# ──────────────────────────────────────────────────────────────
ABORT_SENTINEL = object()


async def guarded_gather(
    coros: list,
    abort_event: threading.Event | None = None,
    concurrency: int | None = None,
) -> tuple[list, list[Exception]]:
    """Run coroutines with abort protection and concurrency limit.

    Returns (results, errors). Each result is either the coroutine's
    return value or ``ABORT_SENTINEL`` if it was cancelled/aborted.

    Args:
        coros: List of awaitable coroutines.
        abort_event: Shared threading.Event for cross-thread abort.
            If None, a local Event is created.
        concurrency: Max parallel coroutines. Defaults to _CONCURRENCY.
    """
    _abort = abort_event if abort_event is not None else threading.Event()
    _sem = asyncio.Semaphore(concurrency or _CONCURRENCY)
    _first_error: list[Exception] = []

    async def _guarded(c):
        if _abort.is_set():
            return ABORT_SENTINEL
        async with _sem:
            if _abort.is_set():
                return ABORT_SENTINEL
            try:
                return await c
            except Exception as e:
                _abort.set()
                if not _first_error:
                    _first_error.append(e)
                return ABORT_SENTINEL

    raw = await asyncio.gather(*[_guarded(c) for c in coros], return_exceptions=True)
    return list(raw), _first_error


# ──────────────────────────────────────────────────────────────
# Sync-from-async bridge
# ──────────────────────────────────────────────────────────────


def run_async(coro):
    """Run an async coroutine from synchronous code, handling existing event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    else:
        return asyncio.run(coro)
