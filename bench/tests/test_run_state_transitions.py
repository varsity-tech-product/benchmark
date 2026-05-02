"""Tests for RunService state-transition guards (Step 3 of v6.0 plan)."""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.run.catalog import TaskCatalog
from server.run.models import RunStatus
from server.run.service import RunService
from server.run.store import RunStore


def _make_service(tmp: Path) -> RunService:
    (tmp / "tasks" / "layer2" / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "tasks" / "layer2" / "data" / "D01_demo.json").write_text(
        json.dumps(
            {
                "task_id": "D01_demo",
                "category": "data",
                "difficulty": "easy",
                "description": "d",
                "persona_id": "p",
                "max_turns": 4,
            }
        ),
        encoding="utf-8",
    )
    return RunService(TaskCatalog(tmp), RunStore(tmp / "runs"))


class MarkCompletedGuardTests(unittest.TestCase):
    def test_rejects_cancelled_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim("D01")
            svc.bind_session(a.run_id, "sess")
            svc.cancel_run(a.run_id)
            with self.assertRaises(ValueError):
                svc.mark_completed(a.run_id, "/tmp/results")

    def test_rejects_failed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim("D01")
            svc.bind_session(a.run_id, "sess")
            svc.mark_failed(a.run_id, "boom")
            with self.assertRaises(ValueError):
                svc.mark_completed(a.run_id, "/tmp/r")

    def test_rejects_non_active_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim("D01")  # CLAIMED, not ACTIVE
            with self.assertRaises(ValueError):
                svc.mark_completed(a.run_id, "/tmp/r")

    def test_idempotent_same_result_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim("D01")
            svc.bind_session(a.run_id, "sess")
            svc.mark_completed(a.run_id, "/tmp/r")
            again = svc.mark_completed(a.run_id, "/tmp/r")
            self.assertEqual(again.status, RunStatus.COMPLETED)

    def test_idempotent_rejects_different_result_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim("D01")
            svc.bind_session(a.run_id, "sess")
            svc.mark_completed(a.run_id, "/tmp/r1")
            with self.assertRaises(ValueError):
                svc.mark_completed(a.run_id, "/tmp/r2")


class CancelGuardTests(unittest.TestCase):
    def test_cancel_rejects_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim("D01")
            svc.bind_session(a.run_id, "sess")
            svc.mark_completed(a.run_id, "/tmp/r")
            with self.assertRaises(ValueError):
                svc.cancel_run(a.run_id)

    def test_cancel_rejects_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim("D01")
            svc.bind_session(a.run_id, "sess")
            svc.mark_failed(a.run_id, "x")
            with self.assertRaises(ValueError):
                svc.cancel_run(a.run_id)


class BindSessionRollbackTests(unittest.TestCase):
    def test_rollback_on_store_error_leaves_run_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim("D01")

            original_save = svc._store.save
            calls = {"n": 0}

            def flaky_save(x):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise OSError("disk full")
                return original_save(x)

            svc._store.save = flaky_save  # type: ignore[assignment]
            with self.assertRaises(OSError):
                svc.bind_session(a.run_id, "sess")

            # Rollback write happened (call 2 was the rollback save)
            self.assertGreaterEqual(calls["n"], 2)
            svc._store.save = original_save  # type: ignore[assignment]
            fresh = svc.get_run(a.run_id)
            self.assertEqual(fresh.status, RunStatus.CLAIMED)
            self.assertIsNone(fresh.session_id)


if __name__ == "__main__":
    unittest.main()
