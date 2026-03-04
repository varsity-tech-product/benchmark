"""Shared async utilities for DeepEval metric evaluation."""

import asyncio

import nest_asyncio


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
