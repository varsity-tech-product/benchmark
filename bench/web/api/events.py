"""SSE event stream endpoint."""

import asyncio
import json

from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

# Async event queue — fed by emit() from orchestrator threads.
event_queue: asyncio.Queue = asyncio.Queue()


async def _generator():
    while True:
        try:
            event = await asyncio.wait_for(event_queue.get(), timeout=15.0)
            yield {"data": json.dumps(event, default=str)}
        except asyncio.TimeoutError:
            # Heartbeat keeps the connection alive
            yield {"comment": "heartbeat"}


@router.get("/events")
async def event_stream():
    return EventSourceResponse(_generator())
