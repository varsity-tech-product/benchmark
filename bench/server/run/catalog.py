"""TaskCatalog — public label to internal task_id resolution.

Scans the active Impl A multi-turn task layer at startup and builds a cached
mapping from public labels to full task_ids. v3 labels use the full task_id.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_ACTIVE_TASK_LAYERS = ("L2",)
_ACTIVE_TASK_PREFIXES = tuple(f"{layer}_" for layer in _ACTIVE_TASK_LAYERS)


@dataclass
class TaskEntry:
    """Cached metadata for one task."""

    public_label: str  # "L2_ADV_01_investment_advice"
    task_id: str  # "L2_ADV_01_investment_advice"
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

    def __init__(
        self,
        bench_root: Path,
        *,
        plugin_task_suites: Iterable[Any] = (),
    ):
        self._entries: dict[str, TaskEntry] = {}  # label -> entry
        self._by_id: dict[str, TaskEntry] = {}  # task_id -> entry
        self._scan(bench_root / "tasks")
        for suite in plugin_task_suites:
            self._scan_plugin_suite(suite)
        logger.info(
            "TaskCatalog: %d tasks loaded (%d labels)",
            len(self._by_id),
            len(self._entries),
        )

    def _scan(self, tasks_dir: Path) -> None:
        if not tasks_dir.is_dir():
            logger.warning("TaskCatalog: tasks directory not found: %s", tasks_dir)
            return

        task_paths: list[Path] = []
        for layer in _ACTIVE_TASK_LAYERS:
            layer_dir = tasks_dir / layer
            if layer_dir.is_dir():
                task_paths.extend(layer_dir.rglob("*.json"))

        for json_path in sorted(task_paths):
            try:
                data = json.loads(json_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("TaskCatalog: skipping %s: %s", json_path, exc)
                continue

            task_id = data.get("task_id", "")
            if not task_id:
                continue

            label = _public_label(task_id)
            if label is None:
                logger.debug("TaskCatalog: no label pattern in %s", task_id)
                continue

            self._add_entry(
                TaskEntry(
                    public_label=label,
                    task_id=task_id,
                    category=data.get("category", ""),
                    difficulty=data.get("difficulty", ""),
                    persona_id=data.get("persona_id", ""),
                    max_turns=data.get("max_turns", 30),
                    timeout_minutes=data.get("timeout_minutes", 15),
                    source_path=json_path,
                )
            )

    def _scan_plugin_suite(self, suite: Any) -> None:
        supported_tasks = getattr(suite, "supported_tasks", None)
        get_task = getattr(suite, "get_task", None)
        if not callable(supported_tasks) or not callable(get_task):
            return

        for task_id in sorted(supported_tasks()):
            try:
                item = get_task(task_id)
            except Exception as exc:
                logger.warning(
                    "TaskCatalog: plugin task %s skipped: %s",
                    task_id,
                    exc,
                )
                continue

            metadata = getattr(item, "metadata", {}) or {}
            if (
                not isinstance(metadata, Mapping)
                or not metadata.get("public_run_task")
            ):
                continue

            payload = getattr(item, "payload", {}) or {}
            if not isinstance(payload, Mapping):
                payload = {}
            business_task = payload.get("impl_b_task", {})
            if not isinstance(business_task, Mapping):
                business_task = {}

            public_label = str(
                metadata.get("public_label")
                or business_task.get("public_label")
                or item.task_id
            )
            self._add_entry(
                TaskEntry(
                    public_label=public_label,
                    task_id=str(item.task_id),
                    category=str(
                        metadata.get("category")
                        or business_task.get("category")
                        or "plugin"
                    ),
                    difficulty=str(
                        metadata.get("difficulty")
                        or business_task.get("difficulty")
                        or "unknown"
                    ),
                    persona_id=str(business_task.get("persona_id") or ""),
                    max_turns=int(business_task.get("max_turns") or 30),
                    timeout_minutes=int(business_task.get("timeout_minutes") or 15),
                    source_path=Path(f"<plugin:{suite.__class__.__name__}>"),
                )
            )

    def _add_entry(self, entry: TaskEntry) -> None:
        if entry.public_label in self._entries:
            existing = self._entries[entry.public_label]
            raise ValueError(
                f"TaskCatalog: duplicate public label '{entry.public_label}' — "
                f"'{entry.task_id}' conflicts with '{existing.task_id}'"
            )

        self._entries[entry.public_label] = entry
        self._by_id[entry.task_id] = entry

    def resolve(self, label_or_id: str) -> TaskEntry | None:
        """Accept both public labels and full task_ids."""
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
        difficulty must not be revealed to the user before selection."""
        return sorted(
            [{"label": e.public_label} for e in self._entries.values()],
            key=lambda x: x["label"],
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, label_or_id: str) -> bool:
        return self.resolve(label_or_id) is not None


def _public_label(task_id: str) -> str | None:
    if task_id.startswith(_ACTIVE_TASK_PREFIXES):
        return task_id
    return None
