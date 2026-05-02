"""Server-local reference data persistence layer."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

_DEFAULT_REFS_DIR = Path(__file__).resolve().parents[2] / "reference" / "refs"


class ReferenceStore:
    """Load and save reference execution data."""

    def __init__(self, refs_dir: Optional[str] = None):
        self.refs_dir = Path(refs_dir) if refs_dir else _DEFAULT_REFS_DIR
        self.refs_dir.mkdir(parents=True, exist_ok=True)

    def save(self, reference: dict) -> Path:
        task_id = reference["task_id"]
        persona_id = reference.get("persona_id", "default")
        path = self.refs_dir / f"{task_id}_{persona_id}.json"
        path.write_text(json.dumps(reference, indent=2, default=str), encoding="utf-8")
        return path

    def load(self, task_id: str, persona_id: Optional[str] = None) -> Optional[dict]:
        if persona_id:
            exact = self.refs_dir / f"{task_id}_{persona_id}.json"
            if exact.exists():
                return self._read(exact)

        for path in sorted(self.refs_dir.glob(f"{task_id}_*.json")):
            return self._read(path)
        return None

    def has_reference(self, task_id: str, persona_id: Optional[str] = None) -> bool:
        return self.load(task_id, persona_id) is not None

    def list_available(self) -> list[dict]:
        refs = []
        for path in sorted(self.refs_dir.glob("*.json")):
            stem = path.stem
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

    @staticmethod
    def _read(path: Path) -> dict:
        return json.loads(path.read_text(encoding="utf-8"))
