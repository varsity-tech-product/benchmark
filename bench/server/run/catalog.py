"""TaskCatalog — public label to internal task_id resolution.

Scans bench/tasks/ at startup, builds a cached mapping from public labels
(e.g. 'D01') to full task_ids (e.g. 'D01_load_inspect_ohlcv').

Extraction rule: ``re.match(r'^([A-Z]\\d{2})_', task_id)``
Duplicate labels cause a startup-time error (fail fast).
"""

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_LABEL_RE = re.compile(r"^([A-Z]\d{2})_")


@dataclass
class TaskEntry:
    """Cached metadata for one task."""

    public_label: str  # "D01"
    task_id: str  # "D01_load_inspect_ohlcv"
    category: str  # "data_analysis"
    difficulty: str  # "medium"
    persona_id: str
    max_turns: int
    timeout_minutes: int
    source_path: Path


class TaskCatalog:
    """Immutable task registry built once at server startup.

    Accepts both public labels and full task_ids via :meth:`resolve`.
    """

    def __init__(self, bench_root: Path):
        self._entries: dict[str, TaskEntry] = {}  # label -> entry
        self._by_id: dict[str, TaskEntry] = {}  # task_id -> entry
        self._scan(bench_root / "tasks")
        logger.info(
            "TaskCatalog: %d tasks loaded (%d labels)",
            len(self._by_id),
            len(self._entries),
        )

    def _scan(self, tasks_dir: Path) -> None:
        if not tasks_dir.is_dir():
            logger.warning("TaskCatalog: tasks directory not found: %s", tasks_dir)
            return

        for json_path in sorted(tasks_dir.rglob("*.json")):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("TaskCatalog: skipping %s: %s", json_path, exc)
                continue

            task_id = data.get("task_id", "")
            if not task_id:
                continue

            m = _LABEL_RE.match(task_id)
            if not m:
                logger.debug("TaskCatalog: no label pattern in %s", task_id)
                continue

            label = m.group(1)

            if label in self._entries:
                existing = self._entries[label]
                raise ValueError(
                    f"TaskCatalog: duplicate public label '{label}' — "
                    f"'{task_id}' conflicts with '{existing.task_id}'"
                )

            entry = TaskEntry(
                public_label=label,
                task_id=task_id,
                category=data.get("category", ""),
                difficulty=data.get("difficulty", ""),
                persona_id=data.get("persona_id", ""),
                max_turns=data.get("max_turns", 30),
                timeout_minutes=data.get("timeout_minutes", 15),
                source_path=json_path,
            )

            self._entries[label] = entry
            self._by_id[task_id] = entry

    def resolve(self, label_or_id: str) -> TaskEntry | None:
        """Accept both 'D01' and 'D01_load_inspect_ohlcv'."""
        return self._entries.get(label_or_id) or self._by_id.get(label_or_id)

    def list_public(self) -> list[dict]:
        """Return public task info for UI/client. No internal details."""
        return sorted(
            [
                {
                    "label": e.public_label,
                    "category": e.category,
                    "difficulty": e.difficulty,
                }
                for e in self._entries.values()
            ],
            key=lambda x: x["label"],
        )

    def list_labels_only(self) -> list[dict]:
        """Return labels only — used by Run/exam UI where category and
        difficulty must not be revealed to the student before selection."""
        return sorted(
            [{"label": e.public_label} for e in self._entries.values()],
            key=lambda x: x["label"],
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, label_or_id: str) -> bool:
        return self.resolve(label_or_id) is not None
