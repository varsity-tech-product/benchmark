"""Server bootstrap helpers.

Keeps environment loading shared across CLI entrypoints and embedded ASGI use.
"""

from __future__ import annotations

import logging
import os
import shlex
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_BENCH_ROOT = Path(__file__).resolve().parents[2]


def _load_env_file(path: Path) -> None:
    """Small .env fallback for KEY=VALUE lines.

    Existing non-empty environment values win, matching python-dotenv's default
    non-override behavior while still allowing .env to fill empty variables.
    """
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or os.environ.get(key):
            continue
        try:
            parts = shlex.split(value, comments=False, posix=True)
        except ValueError:
            parts = [value.strip().strip("'\"")]
        os.environ[key] = parts[0] if parts else ""


def load_server_env(bench_root: str | Path | None = None) -> Path | None:
    """Load server environment variables from ``.env`` if present."""
    root = (
        Path(bench_root).expanduser().resolve() if bench_root else _DEFAULT_BENCH_ROOT
    )
    candidates = [root / ".env", root.parent / ".env"]

    for path in candidates:
        if path.exists():
            try:
                from dotenv import load_dotenv
            except ImportError:
                _load_env_file(path)
            else:
                load_dotenv(path, override=False)
                _load_env_file(path)
            logger.debug("Loaded server environment from %s", path)
            return path

    logger.debug("No .env file found for bench root %s", root)
    return None
