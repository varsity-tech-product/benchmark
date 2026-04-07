"""QuantTutorBench Web Dashboard — FastAPI application.

Start with:
    cd bench && python -m web.server [--port 8765]
"""

import argparse
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Ensure bench root is importable
_BENCH_ROOT = Path(__file__).parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))

from dotenv import load_dotenv

load_dotenv(_BENCH_ROOT.parent / ".env")

from fastapi import FastAPI
from fastapi.responses import FileResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Receive, Scope, Send

from web.api import events, results, runs, tasks

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app):
    """Initialize async emit mode for the live monitor on startup."""
    from orchestrator.live_monitor import init_async

    loop = asyncio.get_event_loop()
    init_async(loop, events.broadcast)

    yield


app = FastAPI(title="QuantTutorBench Dashboard", lifespan=lifespan)

# API routers
app.include_router(tasks.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(results.router, prefix="/api")
app.include_router(events.router, prefix="/api")

# Static files — disable caching for JS/CSS to prevent stale code
_STATIC_DIR = Path(__file__).parent / "static"


class NoCacheStaticMiddleware:
    """Pure-ASGI middleware: add no-cache headers to JS/CSS responses.

    Uses raw ASGI protocol instead of BaseHTTPMiddleware to avoid the
    anyio TaskGroup bug on Python 3.13 that crashes SSE connections.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        needs_nocache = path.startswith("/static/") and any(
            path.endswith(ext) for ext in (".js", ".css")
        )

        if not needs_nocache:
            await self.app(scope, receive, send)
            return

        async def send_with_nocache(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"cache-control", b"no-cache, must-revalidate"))
                headers.append((b"pragma", b"no-cache"))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_nocache)


app.add_middleware(NoCacheStaticMiddleware)
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def index():
    resp = FileResponse(str(_STATIC_DIR / "index.html"))
    resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    return resp


def main():
    parser = argparse.ArgumentParser(description="QuantTutorBench Dashboard")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args()

    import uvicorn

    print(f"\n  QuantTutorBench Dashboard: http://localhost:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
