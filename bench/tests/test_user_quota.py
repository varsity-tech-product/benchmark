import json
import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mcp.types import TextContent

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.quota import QuotaExceeded, QuotaManager
from server.api.http_app import BenchSessionManager
from server.run.catalog import TaskCatalog
from server.run.service import RunService
from server.run.store import RunStore


TASK_ID = "L2_DAT_01_demo"


def _write_task(root: Path) -> None:
    path = root / "tasks" / "L2" / "data" / f"{TASK_ID}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "task_id": TASK_ID,
                "category": "data",
                "difficulty": "easy",
                "description": "demo",
            }
        ),
        encoding="utf-8",
    )


class UserQuotaTests(unittest.TestCase):
    def test_active_run_limit_is_per_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task(root)
            service = RunService(TaskCatalog(root), RunStore(root / "runs"))
            with patch.dict(
                "os.environ",
                {"QTB_MAX_ACTIVE_RUNS_PER_USER": "1"},
                clear=False,
            ):
                service.create_run(TASK_ID, owner_user_id="github:alice")
                with self.assertRaises(QuotaExceeded):
                    service.create_run(TASK_ID, owner_user_id="github:alice")
                service.create_run(TASK_ID, owner_user_id="github:bob")

    def test_heavy_job_running_limit_releases_on_finish(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = QuotaManager(Path(tmp))
            with patch.dict(
                "os.environ",
                {
                    "QTB_MAX_RUNNING_JOBS_PER_USER": "1",
                    "QTB_DAILY_BACKTEST_LIMIT_PER_USER": "10",
                },
                clear=False,
            ):
                reservation = manager.reserve_heavy_job(
                    owner_user_id="github:alice",
                    run_id="run-a",
                    session_id="sess-a",
                    tool_name="run_backtest",
                )
                with self.assertRaises(QuotaExceeded):
                    manager.reserve_heavy_job(
                        owner_user_id="github:alice",
                        run_id="run-a",
                        session_id="sess-a",
                        tool_name="run_backtest",
                    )
                manager.release_heavy_job(
                    owner_user_id="github:alice", reservation_id=reservation
                )
                manager.reserve_heavy_job(
                    owner_user_id="github:alice",
                    run_id="run-a",
                    session_id="sess-a",
                    tool_name="run_backtest",
                )

    def test_daily_backtest_limit_counts_accepted_jobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            manager = QuotaManager(Path(tmp))
            with patch.dict(
                "os.environ",
                {
                    "QTB_MAX_RUNNING_JOBS_PER_USER": "2",
                    "QTB_DAILY_BACKTEST_LIMIT_PER_USER": "1",
                },
                clear=False,
            ):
                reservation = manager.reserve_heavy_job(
                    owner_user_id="github:alice",
                    run_id="run-a",
                    session_id="sess-a",
                    tool_name="run_backtest",
                )
                manager.release_heavy_job(
                    owner_user_id="github:alice", reservation_id=reservation
                )
                with self.assertRaises(QuotaExceeded):
                    manager.reserve_heavy_job(
                        owner_user_id="github:alice",
                        run_id="run-a",
                        session_id="sess-a",
                        tool_name="run_backtest",
                    )

    def test_mcp_heavy_tool_uses_user_quota(self):
        class _State:
            session_id = "sess-a"
            run_id = "run-a"
            task_id = TASK_ID
            owner_user_id = "github:alice"
            owner_github_login = "alice"
            owner_email = "alice@example.com"
            phase = type("Phase", (), {"value": "in_session"})()
            calls = 0

            async def handle_tool_call(self, name, arguments):
                self.calls += 1
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": True, "tool": name}),
                    )
                ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task(root)
            manager = BenchSessionManager(use_docker=False, bench_root=root)
            state = _State()
            with patch.dict(
                "os.environ",
                {
                    "QTB_MAX_RUNNING_JOBS_PER_USER": "1",
                    "QTB_DAILY_BACKTEST_LIMIT_PER_USER": "10",
                },
                clear=False,
            ):
                manager._quota_manager.reserve_heavy_job(
                    owner_user_id="github:alice",
                    run_id="run-existing",
                    session_id="sess-existing",
                    tool_name="run_backtest",
                    job_id="existing",
                )
                response = asyncio.run(
                    manager._handle_mcp_tool_call(state, "run_backtest", {})
                )

            self.assertEqual(state.calls, 0)
            payload = json.loads(response[0].text)
            self.assertFalse(payload["success"])

    def test_mcp_heavy_tool_releases_quota_after_call(self):
        class _State:
            session_id = "sess-a"
            run_id = "run-a"
            task_id = TASK_ID
            owner_user_id = "github:alice"
            owner_github_login = "alice"
            owner_email = "alice@example.com"
            phase = type("Phase", (), {"value": "in_session"})()

            async def handle_tool_call(self, name, arguments):
                return [
                    TextContent(
                        type="text",
                        text=json.dumps({"success": True, "tool": name}),
                    )
                ]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _write_task(root)
            manager = BenchSessionManager(use_docker=False, bench_root=root)
            with patch.dict(
                "os.environ",
                {
                    "QTB_MAX_RUNNING_JOBS_PER_USER": "1",
                    "QTB_DAILY_BACKTEST_LIMIT_PER_USER": "2",
                },
                clear=False,
            ):
                asyncio.run(manager._handle_mcp_tool_call(_State(), "run_backtest", {}))
                reservation = manager._quota_manager.reserve_heavy_job(
                    owner_user_id="github:alice",
                    run_id="run-b",
                    session_id="sess-b",
                    tool_name="run_backtest",
                )
                self.assertTrue(reservation)


if __name__ == "__main__":
    unittest.main()
