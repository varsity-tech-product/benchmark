"""Per-user quota checks for UI-created benchmark runs and heavy jobs."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


class QuotaExceeded(ValueError):
    """Raised when a user quota would be exceeded."""


def quota_subject_enabled(owner_user_id: str) -> bool:
    owner = str(owner_user_id or "")
    return bool(owner and owner != "local-dev")


def max_active_runs_per_user() -> int:
    return _int_env("QTB_MAX_ACTIVE_RUNS_PER_USER", 2)


class QuotaManager:
    def __init__(self, bench_root: str | Path):
        self.bench_root = Path(bench_root)
        self._dir = self.bench_root / "results" / "quotas"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def reserve_heavy_job(
        self,
        *,
        owner_user_id: str,
        run_id: str,
        session_id: str,
        tool_name: str,
        job_id: str = "",
    ) -> str | None:
        if not quota_subject_enabled(owner_user_id):
            return None

        max_running = _int_env("QTB_MAX_RUNNING_JOBS_PER_USER", 1)
        daily_limit = _int_env("QTB_DAILY_BACKTEST_LIMIT_PER_USER", 20)
        reservation_id = job_id or uuid4().hex
        now = time.time()

        with self._lock:
            path = self._today_path()
            data = self._read_locked(path)
            running = data.setdefault("running_jobs", {})
            daily = data.setdefault("daily_counts", {})
            user_running = [
                job
                for job in running.get(owner_user_id, [])
                if isinstance(job, dict)
            ]

            if max_running > 0 and len(user_running) >= max_running:
                raise QuotaExceeded(
                    f"User has reached the active heavy-job limit ({max_running})"
                )
            current_count = int(daily.get(owner_user_id, 0) or 0)
            if daily_limit > 0 and current_count >= daily_limit:
                raise QuotaExceeded(
                    f"User has reached the daily backtest limit ({daily_limit})"
                )

            user_running.append(
                {
                    "reservation_id": reservation_id,
                    "run_id": run_id,
                    "session_id": session_id,
                    "tool_name": tool_name,
                    "started_at": now,
                }
            )
            running[owner_user_id] = user_running
            daily[owner_user_id] = current_count + 1
            self._write_locked(path, data)
        return reservation_id

    def release_heavy_job(self, *, owner_user_id: str, reservation_id: str | None) -> None:
        if not quota_subject_enabled(owner_user_id) or not reservation_id:
            return
        with self._lock:
            path = self._today_path()
            data = self._read_locked(path)
            running = data.setdefault("running_jobs", {})
            user_running = [
                job
                for job in running.get(owner_user_id, [])
                if isinstance(job, dict)
                and str(job.get("reservation_id") or "") != reservation_id
            ]
            running[owner_user_id] = user_running
            self._write_locked(path, data)

    def _today_path(self) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y%m%d")
        return self._dir / f"{day}.json"

    def _read_locked(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_locked(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default
