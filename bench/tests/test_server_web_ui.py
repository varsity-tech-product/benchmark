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


class _Manager:
    def __init__(self, bench_root: Path):
        self.bench_root = bench_root


class ResultIndexerTests(unittest.TestCase):
    def test_indexer_uses_newest_eval_dir_without_latest_symlink(self):
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

            session_id = "session-001"
            _write_json(
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "run_state.json",
                {
                    "session_id": session_id,
                    "task_id": "D01_demo",
                    "persona_id": "beginner_persona",
                    "duration_seconds": 123.4,
                    "conversation": [
                        {"role": "user", "content": "Question"},
                        {"role": "assistant", "content": "Answer 1"},
                        {"role": "assistant", "content": "Answer 2"},
                    ],
                    "tool_logs": [
                        {"name": "tool_a", "args": {}, "result": "ok", "turn_index": 0},
                        {
                            "name": "send_message",
                            "args": {"text": "Rendered answer"},
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
                    "evaluation_status": "pending",
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
                        ]
                    },
                },
            )

            old_eval = (
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "evaluations"
                / "eval_20260101_010101"
            )
            new_eval = (
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "evaluations"
                / "eval_20260102_010101"
            )
            _write_json(
                old_eval / "eval_meta.json",
                {"overall_score": 0.11, "timestamp": "2026-01-01T01:01:01Z"},
            )
            _write_json(
                new_eval / "eval_meta.json",
                {"overall_score": 0.82, "timestamp": "2026-01-02T01:01:01Z"},
            )
            (old_eval / "scores.md").write_text("# old", encoding="utf-8")
            (new_eval / "scores.md").write_text("# newest", encoding="utf-8")
            (new_eval / "cost.md").write_text("cost body", encoding="utf-8")
            (new_eval / "trace.md").write_text("trace body", encoding="utf-8")
            (
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "agent_files"
                / "reports"
            ).mkdir(parents=True, exist_ok=True)
            (
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "agent_files"
                / "reports"
                / "chart.png"
            ).write_bytes(b"png")
            (
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "agent_files"
                / "notes.md"
            ).write_text("hello", encoding="utf-8")

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

            self.assertEqual(detail["scores_md"], "# newest")
            self.assertEqual(detail["cost_md"], "cost body")
            self.assertEqual(detail["trace_md"], "trace body")
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
                detail["conversation"][1]["content_blocks"][0]["type"], "thinking"
            )
            self.assertEqual(
                detail["conversation"][1]["content_blocks"][1]["type"], "tool_use"
            )

    def test_resolve_agent_file_supports_nested_paths_and_blocks_traversal(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            session_id = "session-002"
            _write_json(
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "run_state.json",
                {
                    "session_id": session_id,
                    "task_id": "D01_demo",
                    "persona_id": "beginner_persona",
                    "conversation": [],
                    "tool_logs": [],
                },
            )
            nested = (
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "agent_files"
                / "plots"
                / "chart.png"
            )
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
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "run_state.json",
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
            file_path = (
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "agent_files"
                / "artifacts"
                / "report.md"
            )
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text("report", encoding="utf-8")
            image_path = (
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "agent_files"
                / "plots"
                / "chart.png"
            )
            image_path.parent.mkdir(parents=True, exist_ok=True)
            image_path.write_bytes(b"png")
            csv_path = (
                root
                / "results"
                / "server"
                / "D01_demo"
                / session_id
                / "agent_files"
                / "data"
                / "sample.csv"
            )
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
        self.assertIn("/static/js/app.js?v=20260414l", index_response.text)
        self.assertIn("Isolated UI", index_response.text)


if __name__ == "__main__":
    unittest.main()
