"""QuantTutorBench Web Dashboard — FastAPI application.

Start with:
    cd bench && python -m web.server [--port 8765]
"""

import argparse
import asyncio
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
from fastapi.staticfiles import StaticFiles

from web.api import events, results, runs, tasks


@asynccontextmanager
async def lifespan(app):
    """Initialize async emit mode for the live monitor on startup."""
    from orchestrator.live_monitor import init_async

    loop = asyncio.get_event_loop()
    init_async(loop, events.event_queue)
    yield


app = FastAPI(title="QuantTutorBench Dashboard", lifespan=lifespan)

# API routers
app.include_router(tasks.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(results.router, prefix="/api")
app.include_router(events.router, prefix="/api")

# Static files
_STATIC_DIR = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(_STATIC_DIR / "index.html"))


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
