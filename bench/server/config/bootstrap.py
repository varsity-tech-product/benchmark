"""Server bootstrap helpers.

Keeps environment loading shared across CLI entrypoints and embedded ASGI use.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_BENCH_ROOT = Path(__file__).resolve().parents[2]


def load_server_env(bench_root: str | Path | None = None) -> Path | None:
    """Load server environment variables from ``.env`` if present."""
    try:
        from dotenv import load_dotenv
    except ImportError:
        logger.debug("python-dotenv not installed; skipping .env bootstrap")
        return None

    root = (
        Path(bench_root).expanduser().resolve() if bench_root else _DEFAULT_BENCH_ROOT
    )
    candidates = [root / ".env", root.parent / ".env"]

    for path in candidates:
        if path.exists():
            load_dotenv(path, override=False)
            logger.debug("Loaded server environment from %s", path)
            return path

    logger.debug("No .env file found for bench root %s", root)
    return None
