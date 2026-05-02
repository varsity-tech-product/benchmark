"""Tests for TaskCatalog list_public vs list_labels_only."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.run.catalog import TaskCatalog


def _write_task(root: Path, task_id: str, category: str, difficulty: str) -> None:
    path = root / "tasks" / "layer2" / category / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "category": category,
                "difficulty": difficulty,
                "description": "demo",
                "persona_id": "dev",
                "max_turns": 4,
            }
        ),
        encoding="utf-8",
    )


class TaskCatalogTests(unittest.TestCase):
    def test_labels_only_hides_category_and_difficulty(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task(root, "D01_demo_one", "data_analysis", "easy")
            _write_task(root, "I02_demo_two", "implementation", "hard")

            cat = TaskCatalog(root)

            public = cat.list_public()
            self.assertEqual(
                {"label", "category", "difficulty"}, set(public[0].keys())
            )

            labels = cat.list_labels_only()
            self.assertEqual([{"label": "D01"}, {"label": "I02"}], labels)
            for row in labels:
                self.assertEqual({"label"}, set(row.keys()))


if __name__ == "__main__":
    unittest.main()
