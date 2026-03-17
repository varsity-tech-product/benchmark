"""Reference data persistence layer.

Handles loading and saving per-task reference JSON files that serve as
scoring anchors for later evaluation phases (step efficiency, result
comparison, process alignment).

Reference files are stored as ``{task_id}_{persona_id}.json`` under the
``refs/`` directory.  A lookup by ``task_id`` alone returns the first
matching file (useful when persona doesn't matter, e.g. key_results
comparison).
"""

import json
from pathlib import Path
from typing import Optional

_DEFAULT_REFS_DIR = Path(__file__).parent.parent / "refs"


class ReferenceStore:
    """Load and save reference execution data."""

    def __init__(self, refs_dir: Optional[str] = None):
        self.refs_dir = Path(refs_dir) if refs_dir else _DEFAULT_REFS_DIR
        self.refs_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def save(self, reference: dict) -> Path:
        """Save reference data for a task.

        Args:
            reference: Must contain at least ``task_id``.  If
                ``persona_id`` is present it is used in the filename.

        Returns:
            Path to the saved JSON file.
        """
        task_id = reference["task_id"]
        persona_id = reference.get("persona_id", "default")
        path = self.refs_dir / f"{task_id}_{persona_id}.json"
        with open(path, "w") as f:
            json.dump(reference, f, indent=2, default=str)
        return path

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def load(self, task_id: str, persona_id: Optional[str] = None) -> Optional[dict]:
        """Load reference data for a task.

        Tries ``{task_id}_{persona_id}.json`` first, then falls back to
        any file matching ``{task_id}_*.json``.

        Returns:
            The parsed reference dict, or ``None`` if not found.
        """
        if persona_id:
            exact = self.refs_dir / f"{task_id}_{persona_id}.json"
            if exact.exists():
                return self._read(exact)

        # Fallback: any persona reference for this task
        for path in sorted(self.refs_dir.glob(f"{task_id}_*.json")):
            return self._read(path)

        return None

    def has_reference(self, task_id: str, persona_id: Optional[str] = None) -> bool:
        """Check whether a reference exists for this task."""
        return self.load(task_id, persona_id) is not None

    def list_available(self) -> list[dict]:
        """List all available references as ``{task_id, persona_id, path}``."""
        refs = []
        for path in sorted(self.refs_dir.glob("*.json")):
            stem = path.stem  # e.g. "S01_ma_crossover_beginner_no_finance"
            try:
                data = self._read(path)
                refs.append(
                    {
                        "task_id": data.get("task_id", stem),
                        "persona_id": data.get("persona_id", "unknown"),
                        "oracle_model": data.get("oracle_model", "unknown"),
                        "step_count": data.get("step_count", 0),
                        "path": str(path),
                    }
                )
            except Exception:
                refs.append({"task_id": stem, "path": str(path), "error": True})
        return refs

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    @staticmethod
    def _read(path: Path) -> dict:
        with open(path) as f:
            return json.load(f)
