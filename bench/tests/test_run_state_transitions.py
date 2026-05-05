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


TASK_ID = "L2_DAT_01_demo"


def _make_service(tmp: Path) -> RunService:
    (tmp / "tasks" / "L2" / "data").mkdir(parents=True, exist_ok=True)
    (tmp / "tasks" / "L2" / "data" / f"{TASK_ID}.json").write_text(
        json.dumps(
            {
                "task_id": TASK_ID,
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
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess")
            svc.cancel_run(a.run_id)
            with self.assertRaises(ValueError):
                svc.mark_completed(a.run_id, "/tmp/results")

    def test_rejects_failed_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess")
            svc.mark_failed(a.run_id, "boom")
            with self.assertRaises(ValueError):
                svc.mark_completed(a.run_id, "/tmp/r")

    def test_rejects_non_active_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)  # CLAIMED, inactive
            with self.assertRaises(ValueError):
                svc.mark_completed(a.run_id, "/tmp/r")

    def test_idempotent_same_result_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess")
            svc.mark_completed(a.run_id, "/tmp/r")
            again = svc.mark_completed(a.run_id, "/tmp/r")
            self.assertEqual(again.status, RunStatus.COMPLETED)

    def test_idempotent_rejects_different_result_dir(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess")
            svc.mark_completed(a.run_id, "/tmp/r1")
            with self.assertRaises(ValueError):
                svc.mark_completed(a.run_id, "/tmp/r2")


class CancelGuardTests(unittest.TestCase):
    def test_cancel_rejects_completed(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess")
            svc.mark_completed(a.run_id, "/tmp/r")
            with self.assertRaises(ValueError):
                svc.cancel_run(a.run_id)

    def test_cancel_rejects_failed(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess")
            svc.mark_failed(a.run_id, "x")
            with self.assertRaises(ValueError):
                svc.cancel_run(a.run_id)


class ResetForRetryTests(unittest.TestCase):
    def test_reset_completed_clears_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess")
            svc.mark_completed(a.run_id, "/tmp/r")
            after = svc.reset_for_retry(a.run_id)
            self.assertEqual(after.status, RunStatus.CLAIMED)
            self.assertIsNone(after.session_id)
            self.assertIsNone(after.result_dir)
            self.assertIsNone(after.error)
            self.assertIsNone(after.completed_at)
            self.assertEqual(after.eval_status, "pending")

    def test_reset_active_run_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess")  # ACTIVE
            with self.assertRaises(ValueError):
                svc.reset_for_retry(a.run_id)

    def test_reset_unknown_run_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            with self.assertRaises(ValueError):
                svc.reset_for_retry("run_doesnotexist")

    def test_reset_then_rebind_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess1")
            svc.mark_completed(a.run_id, "/tmp/r1")
            svc.reset_for_retry(a.run_id)
            after = svc.bind_session(a.run_id, "sess2")
            self.assertEqual(after.status, RunStatus.ACTIVE)
            self.assertEqual(after.session_id, "sess2")

    def test_reset_refreshes_claimed_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess")
            svc.mark_completed(a.run_id, "/tmp/r")
            stale = svc.get_run(a.run_id).claimed_at
            after = svc.reset_for_retry(a.run_id)
            # claimed_at must move forward so the idle-claim sweeper
            # does not retire the run mid-retry.
            self.assertNotEqual(after.claimed_at, stale)
            self.assertGreater(after.claimed_at, stale)


class RestoreAfterFailedRetryTests(unittest.TestCase):
    def test_restore_reattaches_completed_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess1")
            svc.mark_completed(a.run_id, "/tmp/r1")
            snapshot = svc.get_run(a.run_id)
            snap_dict = {
                "session_id": snapshot.session_id,
                "result_dir": snapshot.result_dir,
                "completed_at": snapshot.completed_at,
                "eval_status": snapshot.eval_status,
                "error": snapshot.error,
            }
            svc.reset_for_retry(a.run_id)  # now CLAIMED with cleared fields
            restored = svc.restore_after_failed_retry(a.run_id, **snap_dict)
            self.assertEqual(restored.status, RunStatus.COMPLETED)
            self.assertEqual(restored.session_id, "sess1")
            self.assertEqual(restored.result_dir, "/tmp/r1")
            self.assertEqual(restored.completed_at, snapshot.completed_at)

    def test_restore_then_retry_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess1")
            svc.mark_completed(a.run_id, "/tmp/r1")
            snap = svc.get_run(a.run_id)
            snap_dict = {
                "session_id": snap.session_id,
                "result_dir": snap.result_dir,
                "completed_at": snap.completed_at,
                "eval_status": snap.eval_status,
                "error": snap.error,
            }
            svc.reset_for_retry(a.run_id)
            svc.restore_after_failed_retry(a.run_id, **snap_dict)
            # A second retry attempt must still succeed since the run is
            # back in COMPLETED with the failure receipt attached.
            again = svc.reset_for_retry(a.run_id)
            self.assertEqual(again.status, RunStatus.CLAIMED)
            self.assertIsNone(again.session_id)

    def test_restore_rejects_non_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)
            svc.bind_session(a.run_id, "sess1")  # ACTIVE
            with self.assertRaises(ValueError):
                svc.restore_after_failed_retry(
                    a.run_id,
                    session_id="sess1",
                    result_dir="/tmp/r1",
                    completed_at="2026-01-01T00:00:00+00:00",
                    eval_status="pending",
                    error=None,
                )


class BindSessionRollbackTests(unittest.TestCase):
    def test_rollback_on_store_error_leaves_run_claimed(self):
        with tempfile.TemporaryDirectory() as tmp:
            svc = _make_service(Path(tmp))
            a, _, _ = svc.create_and_claim(TASK_ID)

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
