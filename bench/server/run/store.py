"""RunStore — JSON file persistence for RunAssignment.

Storage layout::

    bench/results/runs/{run_id}/run.json

Thread-safe: all writes protected by an internal lock.
"""

import json
import logging
import threading
from pathlib import Path
from typing import Optional

from .models import RunAssignment, RunStatus

logger = logging.getLogger(__name__)


class RunStore:
    """JSON-file persistence for RunAssignment objects."""

    def __init__(self, runs_dir: Path):
        self._runs_dir = Path(runs_dir)
        self._runs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def _run_path(self, run_id: str) -> Path:
        return self._runs_dir / run_id / "run.json"

    def save(self, assignment: RunAssignment) -> None:
        """Write assignment to disk. Creates directory if needed."""
        with self._lock:
            path = self._run_path(assignment.run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(assignment.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

    def get(self, run_id: str) -> Optional[RunAssignment]:
        """Load a single run by ID. Returns None if not found."""
        path = self._run_path(run_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return RunAssignment.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            logger.warning("RunStore: corrupt run.json for %s: %s", run_id, exc)
            return None

    def find_by_token_hash(self, token_hash: str) -> Optional[RunAssignment]:
        """Find a run by token hash. Scans all run.json files."""
        if not self._runs_dir.is_dir():
            return None
        for run_dir in self._runs_dir.iterdir():
            if not run_dir.is_dir():
                continue
            path = run_dir / "run.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("token_hash") == token_hash:
                    return RunAssignment.from_dict(data)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return None

    def list_runs(self, status: Optional[RunStatus] = None) -> list[RunAssignment]:
        """List all runs, optionally filtered by status."""
        results = []
        if not self._runs_dir.is_dir():
            return results
        for run_dir in sorted(self._runs_dir.iterdir(), reverse=True):
            if not run_dir.is_dir():
                continue
            path = run_dir / "run.json"
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                run = RunAssignment.from_dict(data)
                if status is None or run.status == status:
                    results.append(run)
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return results
