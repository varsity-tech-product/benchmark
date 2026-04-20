"""Tests for Step 4: control_token auth on /ui/runs/* endpoints."""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.run.catalog import TaskCatalog
from server.run.service import RunService
from server.run.store import RunStore
from server.web.ui_app import ui_routes


def _write_task(root: Path) -> None:
    (root / "tasks" / "layer2" / "data").mkdir(parents=True, exist_ok=True)
    (root / "tasks" / "layer2" / "data" / "D01_demo.json").write_text(
        json.dumps(
            {
                "task_id": "D01_demo",
                "category": "data",
                "difficulty": "easy",
                "description": "d",
                "persona_ids": ["p"],
                "max_turns": 4,
            }
        ),
        encoding="utf-8",
    )


class _StubManager:
    def __init__(self, root: Path):
        self.bench_root = root
        self._run_service = RunService(TaskCatalog(root), RunStore(root / "runs"))

    def get_session(self, _sid):
        return None

    async def cancel_run(self, run_id):
        self._run_service.cancel_run(run_id)


def _build_client(root: Path) -> tuple[TestClient, _StubManager]:
    manager = _StubManager(root)
    app = Starlette(routes=ui_routes(manager))
    return TestClient(app), manager


class ControlTokenTests(unittest.TestCase):
    def test_create_run_returns_control_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, _ = _build_client(Path(tmp))
            r = client.post("/ui/runs", json={"task": "D01"})
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertIn("control_token", body)
            self.assertTrue(body["control_token"].startswith("qtc_"))
            self.assertTrue(body["token"].startswith("qtb_"))

    def test_get_run_without_control_token_returns_401(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, _ = _build_client(Path(tmp))
            body = client.post("/ui/runs", json={"task": "D01"}).json()
            r = client.get(f"/ui/runs/{body['run_id']}")
            self.assertEqual(r.status_code, 401)

    def test_get_run_with_wrong_control_token_returns_401(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, _ = _build_client(Path(tmp))
            body = client.post("/ui/runs", json={"task": "D01"}).json()
            r = client.get(
                f"/ui/runs/{body['run_id']}",
                headers={"Authorization": "Bearer qtc_not_real"},
            )
            self.assertEqual(r.status_code, 401)

    def test_get_run_with_correct_control_token_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, _ = _build_client(Path(tmp))
            body = client.post("/ui/runs", json={"task": "D01"}).json()
            r = client.get(
                f"/ui/runs/{body['run_id']}",
                headers={"Authorization": f"Bearer {body['control_token']}"},
            )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["run_id"], body["run_id"])

    def test_run_token_does_not_authorize_control_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, _ = _build_client(Path(tmp))
            body = client.post("/ui/runs", json={"task": "D01"}).json()
            # Use the run_token (qtb_) against a control endpoint — must fail.
            r = client.get(
                f"/ui/runs/{body['run_id']}",
                headers={"Authorization": f"Bearer {body['token']}"},
            )
            self.assertEqual(r.status_code, 401)

    def test_cancel_requires_control_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, _ = _build_client(Path(tmp))
            body = client.post("/ui/runs", json={"task": "D01"}).json()
            r = client.post(f"/ui/runs/{body['run_id']}/cancel")
            self.assertEqual(r.status_code, 401)
            r = client.post(
                f"/ui/runs/{body['run_id']}/cancel",
                headers={"Authorization": f"Bearer {body['control_token']}"},
            )
            self.assertEqual(r.status_code, 200)

    def test_store_find_by_token_hash_distinguishes_types(self):
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            _, manager = _build_client(Path(tmp))
            svc = manager._run_service
            a, run_tok, ctrl_tok = svc.create_run("D01")

            h_run = hashlib.sha256(run_tok.encode()).hexdigest()
            h_ctrl = hashlib.sha256(ctrl_tok.encode()).hexdigest()

            self.assertIsNotNone(
                svc._store.find_by_token_hash(h_run, token_type="run")
            )
            self.assertIsNone(
                svc._store.find_by_token_hash(h_run, token_type="control")
            )
            self.assertIsNotNone(
                svc._store.find_by_token_hash(h_ctrl, token_type="control")
            )
            self.assertIsNone(
                svc._store.find_by_token_hash(h_ctrl, token_type="run")
            )


if __name__ == "__main__":
    unittest.main()
