import json
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.auth import UserContext
from server.web.ui_indexer import ResultIndexer


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _write_result(root: Path, session_id: str, owner_user_id: str = "") -> None:
    result_dir = root / "results" / "server" / "D01_demo" / session_id
    _write_json(
        result_dir / "run_state.json",
        {
            "session_id": session_id,
            "task_id": "D01_demo",
            "persona_id": "p",
            "owner_user_id": owner_user_id,
            "owner_github_login": owner_user_id.rsplit(":", 1)[-1] if owner_user_id else "",
            "owner_email": "",
            "visibility": "private",
            "conversation": [],
            "tool_logs": [],
            "workspace_files": ["report.md"],
        },
    )
    files_dir = result_dir / "agent_files"
    files_dir.mkdir(parents=True, exist_ok=True)
    (files_dir / "report.md").write_text("report", encoding="utf-8")


class ResultOwnerFilteringTests(unittest.TestCase):
    def test_user_sees_own_result_and_admin_sees_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "tasks" / "layer2" / "data" / "D01_demo.json",
                {
                    "task_id": "D01_demo",
                    "category": "data",
                    "difficulty": "easy",
                    "description": "demo",
                },
            )
            _write_result(root, "sess-a", "github:alice")
            _write_result(root, "sess-b", "github:bob")
            _write_result(root, "legacy")

            indexer = ResultIndexer(root)
            alice = UserContext("github:alice", "alice", "a@example.com", "Alice", "")
            admin = UserContext(
                "github:admin",
                "admin",
                "admin@example.com",
                "Admin",
                "",
                role="admin",
            )

            alice_results = indexer.list_results(user=alice)
            self.assertEqual([item["session_id"] for item in alice_results], ["sess-a"])
            self.assertNotIn("owner_email", alice_results[0])

            admin_results = indexer.list_results(user=admin)
            self.assertEqual(
                {item["session_id"] for item in admin_results},
                {"sess-a", "sess-b", "legacy"},
            )
            self.assertTrue(all("owner_email" not in item for item in admin_results))

    def test_user_cannot_read_other_user_result_or_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "tasks" / "layer2" / "data" / "D01_demo.json",
                {"task_id": "D01_demo", "category": "data"},
            )
            _write_result(root, "sess-b", "github:bob")
            indexer = ResultIndexer(root)
            alice = UserContext("github:alice", "alice", "a@example.com", "Alice", "")

            with self.assertRaises(PermissionError):
                indexer.get_detail("sess-b", user=alice)
            with self.assertRaises(PermissionError):
                indexer.resolve_agent_file("sess-b", "report.md", user=alice)

    def test_detail_payload_excludes_owner_email(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_json(
                root / "tasks" / "layer2" / "data" / "D01_demo.json",
                {"task_id": "D01_demo", "category": "data"},
            )
            _write_result(root, "sess-a", "github:alice")
            indexer = ResultIndexer(root)
            alice = UserContext("github:alice", "alice", "a@example.com", "Alice", "")

            detail = indexer.get_detail("sess-a", user=alice)
            self.assertNotIn("owner_email", detail)


if __name__ == "__main__":
    unittest.main()
