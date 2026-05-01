"""Tests for Step 4: control_token auth on /ui/runs/* endpoints."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
        self._sessions = {}

    def get_session(self, sid):
        return self._sessions.get(sid)

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

    def test_get_run_without_cookie_or_control_token_returns_401_when_auth_enabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, manager = _build_client(Path(tmp))
            assignment, _, _ = manager._run_service.create_run("D01")
            with patch.dict(os.environ, {"QTB_AUTH_MODE": "github"}):
                r = client.get(f"/ui/runs/{assignment.run_id}")
            self.assertEqual(r.status_code, 401)

    def test_get_run_with_wrong_control_token_returns_401(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, manager = _build_client(Path(tmp))
            assignment, _, _ = manager._run_service.create_run("D01")
            with patch.dict(os.environ, {"QTB_AUTH_MODE": "github"}):
                r = client.get(
                    f"/ui/runs/{assignment.run_id}",
                    headers={"Authorization": "Bearer qtc_not_real"},
                )
            self.assertEqual(r.status_code, 401)

    def test_get_run_with_correct_control_token_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, manager = _build_client(Path(tmp))
            assignment, _, control_token = manager._run_service.create_run("D01")
            with patch.dict(os.environ, {"QTB_AUTH_MODE": "github"}):
                r = client.get(
                    f"/ui/runs/{assignment.run_id}",
                    headers={"Authorization": f"Bearer {control_token}"},
                )
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["run_id"], assignment.run_id)

    def test_run_token_does_not_authorize_control_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, manager = _build_client(Path(tmp))
            assignment, run_token, _ = manager._run_service.create_run("D01")
            # Use the run_token (qtb_) against a control endpoint — must fail.
            with patch.dict(os.environ, {"QTB_AUTH_MODE": "github"}):
                r = client.get(
                    f"/ui/runs/{assignment.run_id}",
                    headers={"Authorization": f"Bearer {run_token}"},
                )
            self.assertEqual(r.status_code, 401)

    def test_live_observer_lists_active_run_from_public_endpoint(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, manager = _build_client(Path(tmp))
            assignment, _, _ = manager._run_service.create_and_claim("D01")
            manager._run_service.bind_session(assignment.run_id, "session-1")
            conversation = [
                {"role": "user", "content": f"message {index}"}
                for index in range(60)
            ]
            manager._sessions["session-1"] = SimpleNamespace(
                phase=SimpleNamespace(value="in_session"),
                session=SimpleNamespace(
                    conversation=conversation,
                    turn=1,
                ),
                proxy=SimpleNamespace(
                    get_logs=lambda: [
                        {
                            "name": "send_message",
                            "args": {},
                            "result": "{}",
                            "success": True,
                            "duration_ms": 5.0,
                        },
                        {
                            "name": "run_lean_backtest",
                            "args": {},
                            "result": "{}",
                            "success": True,
                            "duration_ms": 12.0,
                        }
                    ]
                ),
            )

            r = client.get("/ui/runs/live")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["runs"][0]["run_id"], assignment.run_id)
            self.assertTrue(body["runs"][0]["is_live"])
            self.assertEqual(body["runs"][0]["observer_status"], "active")
            self.assertEqual(body["runs"][0]["session_phase"], "in_session")
            self.assertEqual(body["runs"][0]["turn"], 1)
            self.assertEqual(len(body["runs"][0]["conversation"]), 60)
            self.assertEqual(body["runs"][0]["conversation"][0]["content"], "message 0")
            self.assertEqual(
                body["runs"][0]["recent_tool_logs"][0]["name"],
                "run_lean_backtest",
            )
            self.assertEqual(len(body["runs"][0]["recent_tool_logs"]), 1)

    def test_local_dev_can_open_live_run_detail_without_control_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, manager = _build_client(Path(tmp))
            assignment, _, _ = manager._run_service.create_and_claim("D01")
            manager._run_service.bind_session(assignment.run_id, "session-1")
            manager._sessions["session-1"] = SimpleNamespace(
                phase=SimpleNamespace(value="in_session"),
                session=SimpleNamespace(
                    conversation=[{"role": "user", "content": "opening"}],
                    turn=1,
                ),
                proxy=SimpleNamespace(
                    get_logs=lambda: [
                        {
                            "name": "send_message",
                            "args": {"text": "answer"},
                            "result": "{}",
                            "success": True,
                            "duration_ms": 5.0,
                        }
                    ]
                ),
            )

            status = client.get(f"/ui/runs/{assignment.run_id}")
            live = client.get(f"/ui/runs/{assignment.run_id}/live")

            self.assertEqual(status.status_code, 200)
            self.assertEqual(live.status_code, 200)
            self.assertEqual(status.json()["run_id"], assignment.run_id)
            self.assertEqual(live.json()["conversation"][0]["content"], "opening")
            self.assertEqual(live.json()["recent_tool_logs"][0]["name"], "send_message")

    def test_live_observer_marks_missing_active_session_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, manager = _build_client(Path(tmp))
            assignment, _, _ = manager._run_service.create_and_claim("D01")
            manager._run_service.bind_session(assignment.run_id, "missing-session")

            r = client.get("/ui/runs/live")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertFalse(body["runs"][0]["is_live"])
            self.assertEqual(body["runs"][0]["observer_status"], "stale")

    def test_cancel_requires_control_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_task(Path(tmp))
            client, manager = _build_client(Path(tmp))
            assignment, _, control_token = manager._run_service.create_run("D01")
            with patch.dict(os.environ, {"QTB_AUTH_MODE": "github"}):
                r = client.post(f"/ui/runs/{assignment.run_id}/cancel")
            self.assertEqual(r.status_code, 401)
            with patch.dict(os.environ, {"QTB_AUTH_MODE": "github"}):
                r = client.post(
                    f"/ui/runs/{assignment.run_id}/cancel",
                    headers={"Authorization": f"Bearer {control_token}"},
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
