"""SSE event stream endpoint with broadcast + per-client replay."""

import asyncio
import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

router = APIRouter()

# ── Broadcast + replay buffer ────────────────────────────────────────
_subscribers: set[asyncio.Queue] = set()
_event_buffer: list[dict] = []
_seq: int = 0
_MAX_BUFFER = 1000


def broadcast(event: dict):
    """Push event to all SSE clients and append to replay buffer.

    Must be called from the asyncio event loop (via call_soon_threadsafe).
    """
    global _seq
    _seq += 1
    event = {**event, "_seq": _seq}

    # [DIAG] Log session events with seq and subscriber count
    etype = event.get("type", "")
    if etype in ("session_start", "session_end"):
        import logging as _log

        _log.getLogger("events").info(
            "[DIAG] broadcast seq=%d type=%s key=%s error=%s subs=%d buf=%d",
            _seq,
            etype,
            event.get("job_key", "?"),
            event.get("error", "none"),
            len(_subscribers),
            len(_event_buffer),
        )

    _event_buffer.append(event)
    if len(_event_buffer) > _MAX_BUFFER:
        _event_buffer.pop(0)

    dead = []
    for q in _subscribers:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _subscribers.discard(q)


def load_buffer(events: list[dict]):
    """Populate replay buffer from persisted event log (crash recovery)."""
    global _seq
    _event_buffer.clear()
    for ev in events:
        _seq += 1
        ev["_seq"] = _seq
        _event_buffer.append(ev)


async def _generator(replay: bool = False):
    """SSE generator: optionally replay buffer, then stream live events."""
    q: asyncio.Queue = asyncio.Queue(maxsize=1000)

    # Subscribe first so events emitted during replay are not lost
    _subscribers.add(q)
    snapshot = list(_event_buffer) if replay else []
    last_replayed_seq = snapshot[-1]["_seq"] if snapshot else 0

    try:
        # Phase 1: replay buffered events
        for event in snapshot:
            yield {"data": json.dumps(event, default=str)}

        # Phase 2: live stream (skip events already sent during replay)
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=10.0)
                if event.get("_seq", 0) <= last_replayed_seq:
                    continue
                yield {"data": json.dumps(event, default=str)}
            except asyncio.TimeoutError:
                yield {"data": json.dumps({"type": "heartbeat"})}
    finally:
        _subscribers.discard(q)


@router.get("/events")
async def event_stream(request: Request):
    replay = request.query_params.get("replay") == "1"
    return EventSourceResponse(_generator(replay=replay))
