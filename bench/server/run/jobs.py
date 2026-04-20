"""JobStore — JSON-file persistence for async tool invocations.

Storage layout::

    bench/results/jobs/{job_id}.json

Used by the async job pattern (slice 2 of prod-availability work): heavy
tools like ``run_lean_backtest`` return a ``job_id`` immediately so the
client can poll ``GET /session/{sid}/tool/jobs/{job_id}`` instead of
holding a 10-minute HTTP connection open.

Thread-safe: all writes protected by an internal lock.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_COMPLETED = "completed"
JOB_STATUS_FAILED = "failed"

_ACTIVE = {JOB_STATUS_PENDING, JOB_STATUS_RUNNING}


class JobStore:
    """Disk-backed store for async tool-call jobs."""

    def __init__(self, jobs_dir: Path):
        self._dir = Path(jobs_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _path(self, job_id: str) -> Path:
        return self._dir / f"{job_id}.json"

    def create(
        self, session_id: str, tool_name: str, arguments: dict | None = None
    ) -> dict:
        """Create a pending job record and return it."""
        job = {
            "job_id": uuid.uuid4().hex,
            "session_id": session_id,
            "tool_name": tool_name,
            "arguments": arguments or {},
            "status": JOB_STATUS_PENDING,
            "created_at": time.time(),
            "started_at": None,
            "completed_at": None,
            "result": None,
            "error": None,
        }
        self._write(job)
        return job

    def update(self, job_id: str, **fields) -> Optional[dict]:
        """Merge ``fields`` into the stored job record."""
        with self._lock:
            job = self._read(job_id)
            if job is None:
                return None
            job.update(fields)
            self._write_locked(job)
            return job

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            return self._read(job_id)

    def mark_orphans_failed(self) -> int:
        """Any pending/running jobs at startup lost their worker. Fail them.

        Called once during server startup so clients polling a stale job_id
        get a clean terminal state instead of waiting forever.
        """
        count = 0
        with self._lock:
            if not self._dir.is_dir():
                return 0
            for path in self._dir.glob("*.json"):
                try:
                    job = json.loads(path.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    logger.warning("JobStore: corrupt job file %s: %s", path.name, exc)
                    continue
                if job.get("status") in _ACTIVE:
                    job["status"] = JOB_STATUS_FAILED
                    job["error"] = "server restarted before job completed"
                    job["completed_at"] = time.time()
                    self._write_locked(job)
                    count += 1
        if count:
            logger.info("JobStore: marked %d orphaned job(s) as failed", count)
        return count

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _write(self, job: dict) -> None:
        with self._lock:
            self._write_locked(job)

    def _write_locked(self, job: dict) -> None:
        path = self._path(job["job_id"])
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)

    def _read(self, job_id: str) -> Optional[dict]:
        path = self._path(job_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("JobStore: corrupt job %s: %s", job_id, exc)
            return None
