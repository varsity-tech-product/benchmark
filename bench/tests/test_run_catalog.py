"""Tests for TaskCatalog list_public vs list_labels_only."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.run.catalog import TaskCatalog


def _write_task(
    root: Path, task_id: str, category: str, difficulty: str, layer: str = "L2"
) -> None:
    path = root / "tasks" / layer / category / f"{task_id}.json"
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
            _write_task(root, "L2_DAT_01_demo_one", "data_analysis", "easy")
            _write_task(root, "L2_IMP_02_demo_two", "implementation", "hard")

            cat = TaskCatalog(root)

            public = cat.list_public()
            self.assertEqual(
                {"label", "category", "difficulty"}, set(public[0].keys())
            )

            labels = cat.list_labels_only()
            self.assertEqual(
                [
                    {"label": "L2_DAT_01_demo_one"},
                    {"label": "L2_IMP_02_demo_two"},
                ],
                labels,
            )
            for row in labels:
                self.assertEqual({"label"}, set(row.keys()))

    def test_legacy_shaped_ids_inside_active_layers_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task(root, "A01_demo", "adversarial", "easy")

            cat = TaskCatalog(root)

            self.assertEqual([], cat.list_labels_only())
            self.assertIsNone(cat.resolve("A01_demo"))

    def test_persona_less_l1_tasks_are_ignored_for_run_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task(
                root,
                "L1_DAT_01_demo",
                "data_engineering",
                "easy",
                layer="L1",
            )
            _write_task(root, "L2_ADV_01_demo", "adversarial", "easy")

            cat = TaskCatalog(root)

            self.assertEqual([{"label": "L2_ADV_01_demo"}], cat.list_labels_only())
            self.assertIsNone(cat.resolve("L1_DAT_01_demo"))

    def test_public_plugin_tasks_are_available_for_run_creation(self):
        class PublicPluginSuite:
            def supported_tasks(self):
                return {"IMPLB_JSON_01_summary"}

            def get_task(self, task_id):
                return SimpleNamespace(
                    task_id=task_id,
                    metadata={"public_run_task": True},
                    payload={
                        "impl_b_task": {
                            "category": "data_engineering",
                            "difficulty": "easy",
                            "persona_id": "impl_b_trivial",
                            "max_turns": 2,
                            "timeout_minutes": 5,
                        }
                    },
                )

        with tempfile.TemporaryDirectory() as tmp:
            cat = TaskCatalog(
                Path(tmp),
                plugin_task_suites=(PublicPluginSuite(),),
            )

            entry = cat.resolve("IMPLB_JSON_01_summary")

            self.assertIsNotNone(entry)
            self.assertEqual("IMPLB_JSON_01_summary", entry.task_id)
            self.assertEqual(
                [{"label": "IMPLB_JSON_01_summary"}],
                cat.list_labels_only(),
            )


if __name__ == "__main__":
    unittest.main()
