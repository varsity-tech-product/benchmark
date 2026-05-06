from __future__ import annotations

import json
from pathlib import Path

import pytest

from client.adapters.anthropic_adapter import _openrouter_anthropic_model
from scripts import baseline_run


def _write_task(
    root: Path,
    layer: str,
    task_id: str,
    *,
    category: str = "data",
    difficulty: str = "medium",
    persona_id: str = "",
) -> None:
    path = root / "tasks" / layer / category / f"{task_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "task_id": task_id,
                "layer": layer,
                "category": category,
                "difficulty": difficulty,
                "persona_id": persona_id,
            }
        ),
        encoding="utf-8",
    )


def test_discover_tasks_and_matrix_mark_http_runnable_slice(tmp_path):
    _write_task(tmp_path, "L0", "L0_demo_qa")
    _write_task(tmp_path, "L1", "L1_DAT_01_demo")
    _write_task(tmp_path, "L2", "L2_ADV_01_demo", persona_id="fullstack")

    tasks = baseline_run.discover_tasks(tmp_path)
    matrix = baseline_run.build_matrix(
        tasks,
        agent_ids=("claude_haiku_4_5",),
        condition_ids=("agent",),
    )

    assert [task.task_id for task in tasks] == [
        "L0_demo_qa",
        "L1_DAT_01_demo",
        "L2_ADV_01_demo",
    ]
    assert len(matrix) == 3
    assert [cell.task.task_id for cell in matrix if cell.http_runnable] == [
        "L2_ADV_01_demo"
    ]


def test_matrix_from_args_filters_tasks_in_requested_order(tmp_path):
    _write_task(tmp_path, "L2", "L2_ADV_01_demo", persona_id="fullstack")
    _write_task(tmp_path, "L2", "L2_ADV_02_demo", persona_id="fullstack")
    parser = baseline_run.build_parser()
    args = parser.parse_args(
        [
            "--bench-root",
            str(tmp_path),
            "plan",
            "--layers",
            "L2",
            "--tasks",
            "L2_ADV_02_demo,L2_ADV_01_demo,L2_ADV_02_demo",
            "--agents",
            "claude_haiku_4_5",
            "--conditions",
            "agent",
        ]
    )

    matrix = baseline_run.matrix_from_args(args)

    assert [cell.task.task_id for cell in matrix] == [
        "L2_ADV_02_demo",
        "L2_ADV_01_demo",
    ]


def test_select_tasks_rejects_unknown_id(tmp_path):
    _write_task(tmp_path, "L2", "L2_ADV_01_demo", persona_id="fullstack")
    tasks = baseline_run.discover_tasks(tmp_path, layers=("L2",))

    with pytest.raises(ValueError, match="unknown task id"):
        baseline_run.select_tasks(tasks, ("L2_ADV_missing",))


def test_summary_aggregates_completed_scored_cells():
    task = baseline_run.TaskSpec(
        task_id="L2_ADV_01_demo",
        layer="L2",
        category="adversarial",
        difficulty="hard",
        persona_id="fullstack",
        source_path="tasks/L2/adversarial/L2_ADV_01_demo.json",
    )
    matrix = baseline_run.build_matrix(
        [task],
        agent_ids=("claude_haiku_4_5",),
        condition_ids=("agent", "direct_answer_baseline"),
    )
    records = [
        matrix[0].to_record()
        | {
            "status": "completed",
            "score": {"task_score": 0.75, "task_pass": True},
        },
        matrix[1].to_record()
        | {
            "status": "completed",
            "score": {"task_score": 0.25, "task_pass": False},
        },
    ]

    summary = baseline_run.summarize_records(records, matrix)

    assert summary["planned_cells"] == 2
    assert summary["http_runnable_cells"] == 2
    assert summary["completed_cells"] == 2
    assert summary["by_layer"] == [
        {"layer": "L2", "n": 2, "pass_rate": 0.5, "mean": 0.5, "median": 0.5}
    ]
    assert summary["by_agent"][0]["agent_id"] == "claude_haiku_4_5"


def test_summary_keeps_unknown_pass_rate_null():
    task = baseline_run.TaskSpec(
        task_id="L2_ADV_01_demo",
        layer="L2",
        category="adversarial",
        difficulty="hard",
        persona_id="fullstack",
        source_path="tasks/L2/adversarial/L2_ADV_01_demo.json",
    )
    matrix = baseline_run.build_matrix(
        [task],
        agent_ids=("claude_haiku_4_5",),
        condition_ids=("agent",),
    )
    records = [
        matrix[0].to_record()
        | {
            "status": "completed",
            "score": {"overall_score": 0.75, "score_status": "completed_scored"},
        }
    ]

    summary = baseline_run.summarize_records(records, matrix)

    assert summary["by_layer"] == [
        {"layer": "L2", "n": 1, "pass_rate": None, "mean": 0.75, "median": 0.75}
    ]


def test_cell_status_requires_completed_score_value():
    assert (
        baseline_run.cell_status(
            "session",
            {},
            {"score_status": "completed_scored", "overall_score": 0.8},
        )
        == "completed"
    )
    assert baseline_run.cell_status("session", {}, {"status": "timeout"}) == (
        "score_timeout"
    )
    assert baseline_run.cell_status("session", {}, {"status": "failed"}) == (
        "score_failed"
    )
    assert baseline_run.cell_status("session", {}, {"score_status": "running"}) == (
        "score_pending"
    )


def test_find_result_dir_uses_session_marker_then_run_state(tmp_path):
    first = tmp_path / "results" / "server" / "task" / "persona" / "run-a"
    first.mkdir(parents=True)
    (first / ".session_id").write_text("session-a", encoding="utf-8")

    second = tmp_path / "results" / "server" / "task" / "persona" / "run-b"
    second.mkdir(parents=True)
    (second / "run_state.json").write_text(
        json.dumps({"session_id": "session-b"}),
        encoding="utf-8",
    )

    root = tmp_path / "results" / "server"

    assert baseline_run.find_result_dir(root, "session-a") == first
    assert baseline_run.find_result_dir(root, "session-b") == second
    assert baseline_run.find_result_dir(root, "missing") is None


def test_run_parser_rejects_zero_workers():
    parser = baseline_run.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--workers", "0"])


def test_openrouter_anthropic_model_preserves_matrix_model_choice():
    assert _openrouter_anthropic_model("claude-sonnet-4-6") == (
        "anthropic/claude-sonnet-4-6"
    )
    assert _openrouter_anthropic_model("anthropic/claude-haiku-4-5") == (
        "anthropic/claude-haiku-4-5"
    )
