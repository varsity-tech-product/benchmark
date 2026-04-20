"""Shared concurrency limits for tool dispatch.

Keeps resource-intensive tool invocations (LEAN backtests especially)
bounded so a burst of concurrent callers cannot exhaust the single VPS.
The semaphore is shared across REST and MCP dispatch paths.
"""

from __future__ import annotations

import asyncio
import os

HEAVY_TOOLS: frozenset[str] = frozenset({"run_backtest", "run_lean_backtest"})

_sem: asyncio.Semaphore | None = None


def _max_concurrent() -> int:
    # Read env on demand so values loaded by load_server_env() are honoured
    # even when this module is imported before bootstrap.
    return max(1, int(os.environ.get("QTB_MAX_CONCURRENT_BACKTESTS", "2")))


def backtest_sem() -> asyncio.Semaphore:
    """Return the process-wide backtest semaphore, creating it lazily."""
    global _sem
    if _sem is None:
        _sem = asyncio.Semaphore(_max_concurrent())
    return _sem


def reset_for_tests() -> None:
    """Drop the cached semaphore so a fresh one is created per event loop."""
    global _sem
    _sem = None
