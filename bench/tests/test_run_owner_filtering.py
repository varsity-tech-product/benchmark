import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from starlette.applications import Starlette
from starlette.testclient import TestClient

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.auth import AuthService, SESSION_COOKIE, UserContext
from server.run.catalog import TaskCatalog
from server.run.service import RunService
from server.run.store import RunStore
from server.web.ui_app import ui_routes


def _write_task(root: Path) -> None:
    task_path = root / "tasks" / "layer2" / "data" / "D01_demo.json"
    task_path.parent.mkdir(parents=True, exist_ok=True)
    task_path.write_text(
        json.dumps(
            {
                "task_id": "D01_demo",
                "category": "data",
                "difficulty": "easy",
                "description": "demo",
                "persona_ids": ["p"],
                "max_turns": 4,
            }
        ),
        encoding="utf-8",
    )


class _Manager:
    def __init__(self, root: Path):
        self.bench_root = root
        self._run_service = RunService(TaskCatalog(root), RunStore(root / "runs"))
        self._sessions = {}

    def get_session(self, sid):
        return self._sessions.get(sid)

    async def cancel_run(self, run_id):
        self._run_service.cancel_run(run_id)


def _session_cookie(root: Path, user: UserContext) -> str:
    return AuthService(root).store.create_session(user)


class RunOwnerFilteringTests(unittest.TestCase):
    def test_user_lists_only_owned_runs_and_admin_lists_all(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task(root)
            with patch.dict(
                "os.environ",
                {
                    "QTB_AUTH_MODE": "github",
                    "QTB_MAX_ACTIVE_RUNS_PER_USER": "10",
                },
                clear=False,
            ):
                manager = _Manager(root)
                client = TestClient(Starlette(routes=ui_routes(manager)))
                alice = UserContext("github:alice", "alice", "a@example.com", "Alice", "")
                bob = UserContext("github:bob", "bob", "b@example.com", "Bob", "")
                admin = UserContext(
                    "github:admin",
                    "admin",
                    "admin@example.com",
                    "Admin",
                    "",
                    role="admin",
                )

                client.cookies.set(SESSION_COOKIE, _session_cookie(root, alice))
                alice_run = client.post("/ui/runs", json={"task": "D01"}).json()

                client.cookies.set(SESSION_COOKIE, _session_cookie(root, bob))
                bob_run = client.post("/ui/runs", json={"task": "D01"}).json()

                client.cookies.set(SESSION_COOKIE, _session_cookie(root, alice))
                owned = client.get("/ui/runs").json()["runs"]
                self.assertEqual([run["run_id"] for run in owned], [alice_run["run_id"]])
                self.assertNotIn("owner_email", owned[0])

                denied = client.get(f"/ui/runs/{bob_run['run_id']}")
                self.assertEqual(denied.status_code, 403)

                client.cookies.set(SESSION_COOKIE, _session_cookie(root, admin))
                all_runs = client.get("/ui/runs?scope=all").json()["runs"]
                self.assertEqual(
                    {run["run_id"] for run in all_runs},
                    {alice_run["run_id"], bob_run["run_id"]},
                )
                self.assertTrue(all("owner_email" not in run for run in all_runs))

    def test_control_token_still_authorizes_owner_monitor_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task(root)
            with patch.dict(
                "os.environ",
                {"QTB_AUTH_MODE": "github", "QTB_MAX_ACTIVE_RUNS_PER_USER": "10"},
                clear=False,
            ):
                manager = _Manager(root)
                client = TestClient(Starlette(routes=ui_routes(manager)))
                alice = UserContext("github:alice", "alice", "a@example.com", "Alice", "")
                client.cookies.set(SESSION_COOKIE, _session_cookie(root, alice))
                body = client.post("/ui/runs", json={"task": "D01"}).json()
                client.cookies.clear()

                response = client.get(
                    f"/ui/runs/{body['run_id']}",
                    headers={"Authorization": f"Bearer {body['control_token']}"},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["run_id"], body["run_id"])


if __name__ == "__main__":
    unittest.main()
