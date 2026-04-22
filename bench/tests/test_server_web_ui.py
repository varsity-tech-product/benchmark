import json
import sys
import tempfile
import unittest
from pathlib import Path

from starlette.applications import Starlette
from starlette.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from server.api.http_app import create_app
from server.web.ui_app import ui_routes
from server.web.ui_indexer import ResultIndexer


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_server_result_dir(
    root: Path,
    task_id: str,
    persona_id: str,
    session_id: str,
    timestamp: str = "20260422_120000",
) -> Path:
    result_dir = (
        root
        / "results"
        / "server"
        / task_id
        / persona_id
        / f"{timestamp}_{session_id[:12]}"
    )
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / ".session_id").write_text(session_id, encoding="utf-8")
    return result_dir


class _Manager:
    def __init__(self, bench_root: Path):
        self.bench_root = bench_root


class ResultIndexerTests(unittest.TestCase):
    def test_indexer_uses_latest_score_run(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            _write_json(
                root / "tasks" / "layer2" / "data_analysis" / "D01_demo.json",
                {
                    "task_id": "D01_demo",
                    "category": "data_analysis",
                    "difficulty": "medium",
                    "description": "Demo task",
                    "persona_ids": ["beginner_persona"],
                    "max_turns": 8,
                    "requires_code": True,
                },
            )
            _write_json(
                root / "personas" / "beginner_persona.json",
                {
                    "persona_id": "beginner_persona",
                    "knowledge_level": "beginner",
                    "description": "New to quant workflows",
                },
            )

            session_id = "session-001-abcdef"
            result_dir = _make_server_result_dir(
                root, "D01_demo", "beginner_persona", session_id
            )
            _write_json(
                result_dir / "run_state.json",
                {
                    "session_id": session_id,
                    "task_id": "D01_demo",
                    "persona_id": "beginner_persona",
                    "duration_seconds": 123.4,
                    "conversation": [
                        {"role": "user", "content": "Question"},
                        {
                            "role": "assistant",
                            "content": "Answer 1",
                            "attachments": [
                                {
                                    "filename": "notes.md",
                                    "content": "hello",
                                    "truncated": False,
                                    "is_image": False,
                                }
                            ],
                        },
                        {"role": "assistant", "content": "Answer 2"},
                    ],
                    "tool_logs": [
                        {"name": "tool_a", "args": {}, "result": "ok", "turn_index": 0},
                        {
                            "name": "send_message",
                            "args": {
                                "text": "Rendered answer",
                                "attachments": ["notes.md"],
                            },
                            "result": json.dumps(
                                {
                                    "student_message": "Thanks, that helps.",
                                    "status": "active",
                                }
                            ),
                            "turn_index": 0,
                            "duration_ms": 42.0,
                        },
                    ],
                    "workspace_files": ["reports/chart.png", "notes.md"],
                    "distractor_names": ["noise_tool"],
                    "step_count": 5,
                    "simulator_cost": 0.02,
                },
            )

            _write_json(
                root / "results" / "client" / session_id / "client_trace.json",
                {
                    "timestamp": "2026-04-13T12:34:56Z",
                    "agent_cost": {
                        "model": "anthropic/claude-haiku-4-5",
                        "input_tokens": 100,
                        "output_tokens": 50,
                        "cost_usd": 0.12,
                        "api_calls": 3,
                    },
                    "content_blocks": {
                        "0": [
                            {"type": "thinking", "text": "reasoning"},
                            {"type": "tool_use", "name": "tool_a", "input": {}},
                            {"type": "tool_result", "content": "ok", "is_error": False},
                            {"type": "text", "text": "Rendered answer"},
                        ],
                        "1": [
                            {"type": "text", "text": "Answer 2"},
                        ],
                    },
                },
            )

            _write_json(
                result_dir / "evaluations" / "index.json",
                {
                    "version": "2.0",
                    "next_score_number": 3,
                    "latest_completed_score_id": "score_2",
                    "scores": [
                        {
                            "score_id": "score_1",
                            "status": "completed_scored",
                            "eval_mode": "full",
                            "eval_model": "judge-a",
                            "tutor_dims": None,
                            "created_at": "2026-01-01T01:01:01Z",
                            "completed_at": "2026-01-01T01:01:02Z",
                            "overall_score": 0.11,
                            "score_path": "score_1/score.json",
                            "cost_path": "score_1/cost.json",
                        },
                        {
                            "score_id": "score_2",
                            "status": "completed_scored",
                            "eval_mode": "full",
                            "eval_model": "judge-a",
                            "tutor_dims": None,
                            "created_at": "2026-01-02T01:01:01Z",
                            "completed_at": "2026-01-02T01:01:02Z",
                            "overall_score": 0.82,
                            "score_path": "score_2/score.json",
                            "cost_path": "score_2/cost.json",
                        },
                    ],
                },
            )
            _write_json(
                result_dir / "evaluations" / "score_1" / "score.json",
                {
                    "version": "2.0",
                    "score_id": "score_1",
                    "score_status": "completed_scored",
                    "overall_score": 0.11,
                },
            )
            _write_json(
                result_dir / "evaluations" / "score_2" / "score.json",
                {
                    "version": "2.0",
                    "score_id": "score_2",
                    "score_status": "completed_scored",
                    "eval_mode": "full",
                    "overall_score": 0.82,
                    "completed_at": "2026-01-02T01:01:02Z",
                    "qr": {"track": "qr", "score": 0.8, "status": "success"},
                    "qp": {"track": "qp", "score": 0.84, "status": "success"},
                    "tutor": {"track": "tutor", "score": 0.82, "status": "success"},
                },
            )
            _write_json(
                result_dir / "evaluations" / "score_2" / "cost.json",
                {
                    "version": "2.0",
                    "score_id": "score_2",
                    "eval_cost_usd": 0.03,
                    "eval_cost_by_track": {"qr": 0.01, "qp": 0.01, "tutor": 0.01},
                    "eval_cost_by_model": {"judge-a": 0.03},
                },
            )
            (result_dir / "agent_files" / "reports").mkdir(parents=True, exist_ok=True)
            (result_dir / "agent_files" / "reports" / "chart.png").write_bytes(b"png")
            (result_dir / "agent_files" / "notes.md").write_text(
                "hello", encoding="utf-8"
            )

            indexer = ResultIndexer(root)
            summary = indexer.list_results()[0]
            detail = indexer.get_detail(session_id)

            self.assertEqual(summary["evaluation_status"], "completed")
            self.assertAlmostEqual(summary["overall_score"], 0.82)
            self.assertEqual(summary["model"], "anthropic/claude-haiku-4-5")
            self.assertEqual(summary["agent_name"], "anthropic")
            self.assertEqual(summary["timestamp"], "2026-04-13T12:34:56Z")
            self.assertTrue(summary["has_content_blocks"])
            self.assertEqual(summary["turn_count"], 2)
            self.assertEqual(summary["tool_count"], 1)
            self.assertEqual(summary["send_message_count"], 1)
            self.assertEqual(summary["step_count"], 5)

            self.assertEqual(detail["score_json"]["score_id"], "score_2")
            self.assertEqual(detail["cost_json"]["eval_cost_usd"], 0.03)
            self.assertEqual(detail["eval_history"][0]["score_id"], "score_2")
            self.assertAlmostEqual(detail["evaluation_cost"], 0.03)
            self.assertAlmostEqual(detail["total_cost"], 0.05)
            self.assertEqual(
                detail["workspace_files"], ["reports/chart.png", "notes.md"]
            )
            self.assertEqual(len(detail["tool_logs"]), 1)
            self.assertEqual(detail["tool_logs"][0]["name"], "tool_a")
            self.assertEqual(detail["send_message_count"], 1)
            self.assertEqual(
                detail["send_message_events"][0]["request_text"], "Rendered answer"
            )
            self.assertEqual(
                detail["send_message_events"][0]["student_message"],
                "Thanks, that helps.",
            )
            self.assertEqual(detail["send_message_events"][0]["status"], "active")
            self.assertEqual(
                detail["send_message_events"][0]["attachments"][0]["filename"],
                "notes.md",
            )
            self.assertEqual(
                detail["send_message_events"][0]["attachments"][0]["content"],
                "hello",
            )
            self.assertEqual(
                detail["conversation"][1]["content_blocks"][0]["type"], "thinking"
            )
            self.assertEqual(
                detail["conversation"][1]["content_blocks"][1]["type"], "tool_use"
            )

    def test_client_trace_loads_with_original_id_when_server_id_has_task_suffix(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base_session_id = "48ad001952494420bf83cc3f5be94c3f"
            stored_session_id = f"{base_session_id}_D01"
            run_dir = _make_server_result_dir(
                root,
                "D01_demo",
                "beginner_persona",
                stored_session_id,
                timestamp="20260416_120000",
            )
            _write_json(
                run_dir / "run_state.json",
                {
                    "session_id": stored_session_id,
                    "task_id": "D01_demo",
                    "persona_id": "beginner_persona",
                    "conversation": [
                        {"role": "user", "content": "Question"},
                        {"role": "assistant", "content": "Answer"},
                    ],
                    "tool_logs": [],
                },
            )
            _write_json(
                root / "results" / "client" / base_session_id / "client_trace.json",
                {
                    "timestamp": "2026-04-16T12:00:00Z",
                    "agent_cost": {
                        "model": "anthropic/claude-sonnet-4-6",
                        "input_tokens": 10,
                        "output_tokens": 20,
                        "cost_usd": 0.01,
                        "api_calls": 1,
                    },
                    "content_blocks": {
                        "0": [
                            {"type": "thinking", "text": "mapped"},
                            {"type": "text", "text": "Answer"},
                        ],
                    },
                },
            )

            indexer = ResultIndexer(root)
            summary = indexer.list_results()[0]
            detail = indexer.get_detail(stored_session_id)

            self.assertEqual(summary["session_id"], stored_session_id)
            self.assertTrue(summary["has_client_trace"])
            self.assertEqual(summary["model"], "anthropic/claude-sonnet-4-6")
            self.assertEqual(detail["session_id"], stored_session_id)
            self.assertTrue(detail["has_content_blocks"])
            self.assertEqual(
                detail["conversation"][1]["content_blocks"][0]["text"],
                "mapped",
            )

    def test_resolve_agent_file_supports_nested_paths_and_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            session_id = "session-002"
            result_dir = _make_server_result_dir(
                root, "D01_demo", "beginner_persona", session_id
            )
            _write_json(
                result_dir / "run_state.json",
                {
                    "session_id": session_id,
                    "task_id": "D01_demo",
                    "persona_id": "beginner_persona",
                    "conversation": [],
                    "tool_logs": [],
                },
            )
            nested = result_dir / "agent_files" / "plots" / "chart.png"
            nested.parent.mkdir(parents=True, exist_ok=True)
            nested.write_bytes(b"png")

            indexer = ResultIndexer(root)
            resolved = indexer.resolve_agent_file(session_id, "plots/chart.png")

            self.assertEqual(resolved, nested.resolve())
            with self.assertRaises(ValueError):
                indexer.resolve_agent_file(session_id, "../secret.txt")


class UiRoutesTests(unittest.TestCase):
    def test_ui_routes_serve_results_detail_and_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            session_id = "session-003"
            result_dir = _make_server_result_dir(
                root, "D01_demo", "beginner_persona", session_id
            )
            _write_json(
                root / "tasks" / "layer2" / "data_analysis" / "D01_demo.json",
                {
                    "task_id": "D01_demo",
                    "category": "data_analysis",
                    "difficulty": "easy",
                    "description": "Demo task",
                    "persona_ids": ["beginner_persona"],
                },
            )
            _write_json(
                root / "personas" / "beginner_persona.json",
                {
                    "persona_id": "beginner_persona",
                    "knowledge_level": "beginner",
                    "description": "Demo persona",
                },
            )
            _write_json(
                result_dir / "run_state.json",
                {
                    "session_id": session_id,
                    "task_id": "D01_demo",
                    "persona_id": "beginner_persona",
                    "conversation": [{"role": "assistant", "content": "Answer"}],
                    "tool_logs": [
                        {
                            "name": "send_message",
                            "args": {"text": "Answer"},
                            "result": json.dumps(
                                {
                                    "student_message": "Got it",
                                    "status": "completed",
                                    "reason": "objectives_met",
                                }
                            ),
                            "turn_index": 0,
                        }
                    ],
                    "workspace_files": [
                        "artifacts/report.md",
                        "plots/chart.png",
                        "data/sample.csv",
                    ],
                },
            )
            file_path = result_dir / "agent_files" / "artifacts" / "report.md"
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("report", encoding="utf-8")
            image_path = result_dir / "agent_files" / "plots" / "chart.png"
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"png")
            csv_path = result_dir / "agent_files" / "data" / "sample.csv"
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            csv_path.write_text("col_a,col_b\n1,2\n3,4\n", encoding="utf-8")

            app = Starlette(routes=ui_routes(_Manager(root)))
            client = TestClient(app)

            tasks_response = client.get("/ui/tasks")
            results_response = client.get("/ui/results")
            detail_response = client.get(f"/ui/results/{session_id}")
            workspace_response = client.get(f"/ui/results/{session_id}/workspace")
            workspace_preview_response = client.get(
                f"/ui/results/{session_id}/workspace/preview/artifacts/report.md"
            )
            csv_preview_response = client.get(
                f"/ui/results/{session_id}/workspace/preview/data/sample.csv"
            )
            image_preview_response = client.get(
                f"/ui/results/{session_id}/workspace/preview/plots/chart.png"
            )
            file_response = client.get(
                f"/ui/results/{session_id}/files/artifacts/report.md"
            )
            bad_path_response = client.get(
                f"/ui/results/{session_id}/files/%2E%2E/secret.txt"
            )
            bad_preview_response = client.get(
                f"/ui/results/{session_id}/workspace/preview/%2E%2E/secret.txt"
            )

            self.assertEqual(tasks_response.status_code, 200)
            self.assertEqual(results_response.status_code, 200)
            self.assertEqual(detail_response.status_code, 200)
            self.assertEqual(workspace_response.status_code, 200)
            self.assertEqual(workspace_preview_response.status_code, 200)
            self.assertEqual(csv_preview_response.status_code, 200)
            self.assertEqual(image_preview_response.status_code, 200)
            self.assertEqual(file_response.status_code, 200)
            self.assertEqual(file_response.text, "report")
            self.assertEqual(bad_path_response.status_code, 400)
            self.assertEqual(bad_preview_response.status_code, 400)

            payload = detail_response.json()
            self.assertEqual(payload["session_id"], session_id)
            self.assertEqual(
                payload["workspace_files"],
                ["artifacts/report.md", "plots/chart.png", "data/sample.csv"],
            )
            self.assertEqual(payload["tool_logs"], [])
            self.assertEqual(payload["send_message_count"], 1)
            self.assertEqual(payload["send_message_events"][0]["status"], "completed")
            self.assertEqual(
                payload["send_message_events"][0]["reason"], "objectives_met"
            )

            workspace_payload = workspace_response.json()
            self.assertEqual(workspace_payload["file_count"], 3)
            self.assertEqual(workspace_payload["files"][0]["kind"], "markdown")
            self.assertEqual(workspace_payload["files"][1]["kind"], "csv")
            self.assertEqual(workspace_payload["files"][2]["kind"], "image")

            preview_payload = workspace_preview_response.json()
            self.assertEqual(preview_payload["kind"], "markdown")
            self.assertEqual(preview_payload["content_text"], "report")

            csv_payload = csv_preview_response.json()
            self.assertEqual(csv_payload["kind"], "csv")
            self.assertEqual(csv_payload["columns"], ["col_a", "col_b"])
            self.assertEqual(csv_payload["rows"], [["1", "2"], ["3", "4"]])

            image_payload = image_preview_response.json()
            self.assertEqual(image_payload["kind"], "image")
            self.assertIn("/ui/results/", image_payload["raw_url"])


class HttpAppSmokeTests(unittest.TestCase):
    def test_create_app_serves_isolated_shell_and_static_assets(self):
        bench_root = Path(__file__).resolve().parents[1]
        app = create_app(use_docker=False, bench_root=bench_root)
        client = TestClient(app)

        index_response = client.get("/")
        script_response = client.get("/static/js/app.js")
        render_response = client.get("/static/js/render.js")

        self.assertEqual(index_response.status_code, 200)
        self.assertEqual(script_response.status_code, 200)
        self.assertEqual(render_response.status_code, 200)
        self.assertIn("/static/js/chat.js", index_response.text)
        self.assertIn("/static/js/tools.js", index_response.text)
        self.assertIn("/static/js/app.js", index_response.text)
        self.assertIn("/static/js/run-agent.js", index_response.text)
        self.assertIn('href="#/run"', index_response.text)
        self.assertIn('data-route="run"', index_response.text)
        self.assertIn("Isolated UI", index_response.text)


if __name__ == "__main__":
    unittest.main()
