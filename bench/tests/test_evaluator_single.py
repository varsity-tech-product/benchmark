"""Tests for the offline evaluator (issue #46 slice 2).

Pins the producer/consumer wiring: a bundle written by the producer can
be scored by the new evaluator, the output lands in the sibling
``evaluations/server/...`` tree, and ``find_latest_eval_dir`` finds it
ahead of any legacy in-bundle directory.

These tests stub ``evaluate_task`` because the real pipeline needs LLM
judges + reference fixtures; the *driver*, paths, and persistence are
what slice 2 introduces and what we want to lock in.
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from server.evaluator import paths, score_bundle
from server.storage.bundle import load_bundle
from server.storage.result_writer import save_run_state

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _stub_task(category: str = "data_analysis", requires_code: bool = True):
    return types.SimpleNamespace(
        task_id="X01",
        category=types.SimpleNamespace(value=category),
        difficulty=types.SimpleNamespace(value="medium"),
        requires_code=requires_code,
        environment=types.SimpleNamespace(data_files=[]),
        ground_truth=None,
        description="stub task",
    )


def _stub_persona():
    return types.SimpleNamespace(
        persona_id="fullstack_practitioner",
        knowledge_level="proficient_code",
        description="stub persona",
    )


def _make_bundle(bundle_dir: Path) -> Path:
    workspace = bundle_dir.parent / "_ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "solution.py").write_text("print(1)\n", encoding="utf-8")
    save_run_state(
        result_dir=bundle_dir,
        conversation=[
            {"role": "user", "content": "help"},
            {"role": "assistant", "content": "sure"},
        ],
        tool_logs=[
            {
                "name": "execute_python",
                "args": {"code": "print(1)"},
                "success": True,
                "duration_ms": 5.0,
                "turn_index": 1,
                "result": "1\n",
            }
        ],
        workspace_path=str(workspace),
        duration_seconds=1.0,
        distractor_names=[],
        task_id="X01",
        session_id="abcd1234efgh5678abcd1234efgh5678",
        persona_id="fullstack_practitioner",
        session_status="completed",
        termination_reason="tc_complete",
        run_id="run_test_X01",
        public_task_label="X01",
    )
    return bundle_dir


def _fake_evaluate_task(**_kwargs):
    return {
        "quant_result": 0.71,
        "quant_process": 0.66,
        "tutor_scores": {"D1": 4, "D2": 3, "D3": 4, "D4": 3, "D5": 4, "D6": 3, "D7": 4},
    }


def _fake_compute_task_score(**_kwargs):
    return {"overall_score": 0.7}


@pytest.fixture
def stubbed_pipeline():
    with (
        patch(
            "server.evaluator.single.evaluate_task",
            side_effect=lambda **kw: _fake_evaluate_task(**kw),
            create=True,
        ),
        patch(
            "server.evaluator.single.compute_task_score",
            side_effect=lambda **kw: _fake_compute_task_score(**kw),
            create=True,
        ),
        patch("server.eval.pipeline.evaluate_task", _fake_evaluate_task),
        patch("server.eval.scoring.compute_task_score", _fake_compute_task_score),
    ):
        yield


def test_score_bundle_writes_to_sibling_tree(tmp_path, stubbed_pipeline):
    bench_root = tmp_path / "bench"
    bundle_dir = (
        bench_root
        / "results"
        / "server"
        / "X01"
        / "fullstack_practitioner"
        / "20260421_120000_abcd1234"
    )
    _make_bundle(bundle_dir)

    results = score_bundle(
        bundle_dir=bundle_dir,
        task=_stub_task(),
        persona=_stub_persona(),
        bench_root=bench_root,
        eval_model="fake-model",
    )

    assert results["quant_result"] == 0.71
    out_dir = results["_eval_run_dir"]
    assert out_dir.is_dir()
    assert out_dir.parent.parent.parent == bench_root / "evaluations" / "server" / "X01"
    assert out_dir.parent.parent.name == "fullstack_practitioner"
    assert out_dir.parent.name == "abcd1234"
    assert paths.EVAL_RUN_ID_RE.match(out_dir.name)

    meta = json.loads((out_dir / "eval_meta.json").read_text(encoding="utf-8"))
    assert meta["overall_score"] == 0.7
    assert meta["eval_run_id"] == out_dir.name
    assert meta["bundle_dir"] == str(bundle_dir)


def test_score_bundle_does_not_touch_legacy_in_bundle_dir(tmp_path, stubbed_pipeline):
    bench_root = tmp_path / "bench"
    bundle_dir = (
        bench_root
        / "results"
        / "server"
        / "X01"
        / "fullstack_practitioner"
        / "20260421_120000_abcd1234"
    )
    _make_bundle(bundle_dir)

    score_bundle(
        bundle_dir=bundle_dir,
        task=_stub_task(),
        persona=_stub_persona(),
        bench_root=bench_root,
        eval_model="fake-model",
    )

    # Bundle directory must remain free of evaluator-produced files.
    assert not (bundle_dir / "evaluations").exists()


def test_score_bundle_marks_run_state_completed(tmp_path, stubbed_pipeline):
    bench_root = tmp_path / "bench"
    bundle_dir = (
        bench_root
        / "results"
        / "server"
        / "X01"
        / "fullstack_practitioner"
        / "20260421_120000_abcd1234"
    )
    _make_bundle(bundle_dir)

    score_bundle(
        bundle_dir=bundle_dir,
        task=_stub_task(),
        persona=_stub_persona(),
        bench_root=bench_root,
        eval_model="fake-model",
    )

    run_state = json.loads(
        (bundle_dir / "run_state.json").read_text(encoding="utf-8")
    )
    assert run_state["evaluation_status"] == "completed"


def test_find_latest_eval_dir_returns_newest_run(tmp_path):
    bench_root = tmp_path / "bench"
    older = paths.eval_run_dir(
        bench_root,
        task_id="X01",
        persona_id="fullstack_practitioner",
        session_id="abcd1234efgh5678",
        eval_run_id="eval_20260101_000000",
    )
    older.mkdir(parents=True)
    (older / "eval_meta.json").write_text("{}", encoding="utf-8")

    newer = paths.eval_run_dir(
        bench_root,
        task_id="X01",
        persona_id="fullstack_practitioner",
        session_id="abcd1234efgh5678",
        eval_run_id="eval_20260421_010101",
    )
    newer.mkdir(parents=True)
    (newer / "eval_meta.json").write_text("{}", encoding="utf-8")

    latest = paths.find_latest_eval_dir(
        bench_root=bench_root,
        task_id="X01",
        persona_id="fullstack_practitioner",
        session_id="abcd1234efgh5678",
    )
    assert latest == newer


def test_find_latest_eval_dir_returns_none_when_empty(tmp_path):
    """No sibling tree → ``None``. The legacy in-bundle fallback is
    gone (issue #46 slice 5)."""
    latest = paths.find_latest_eval_dir(
        bench_root=tmp_path / "bench",
        task_id="X01",
        persona_id="fullstack_practitioner",
        session_id="abcd1234efgh5678",
    )
    assert latest is None


def test_list_eval_history_returns_runs_newest_first(tmp_path):
    bench_root = tmp_path / "bench"
    for run_id in ("eval_20260101_000000", "eval_20260421_010101"):
        run = paths.eval_run_dir(
            bench_root,
            task_id="X01",
            persona_id="fullstack_practitioner",
            session_id="abcd1234efgh5678",
            eval_run_id=run_id,
        )
        run.mkdir(parents=True)
        (run / "eval_meta.json").write_text("{}", encoding="utf-8")

    history = paths.list_eval_history(
        bench_root=bench_root,
        task_id="X01",
        persona_id="fullstack_practitioner",
        session_id="abcd1234efgh5678",
    )
    names = [p.name for p in history]
    assert names == ["eval_20260421_010101", "eval_20260101_000000"]


def test_score_bundle_requires_manifest(tmp_path, stubbed_pipeline):
    """Slice 5 dropped the manifest backfill — bundles must carry a
    ``manifest.json`` (any bundle written since slice 1 does)."""
    bench_root = tmp_path / "bench"
    bundle_dir = (
        bench_root
        / "results"
        / "server"
        / "X01"
        / "fullstack_practitioner"
        / "20260101_010101_abcd1234"
    )
    _make_bundle(bundle_dir)
    # Simulate a pre-slice-1 bundle: delete the manifest.
    (bundle_dir / "manifest.json").unlink()

    with pytest.raises(FileNotFoundError, match="manifest.json"):
        score_bundle(
            bundle_dir=bundle_dir,
            task=_stub_task(),
            persona=_stub_persona(),
            bench_root=bench_root,
            eval_model="fake-model",
        )


def test_score_bundle_marks_failed_on_pipeline_exception(tmp_path):
    bench_root = tmp_path / "bench"
    bundle_dir = (
        bench_root
        / "results"
        / "server"
        / "X01"
        / "fullstack_practitioner"
        / "20260421_120000_abcd1234"
    )
    _make_bundle(bundle_dir)

    def _boom(**_kw):
        raise RuntimeError("judge crashed")

    with patch("server.eval.pipeline.evaluate_task", _boom):
        with pytest.raises(RuntimeError, match="judge crashed"):
            score_bundle(
                bundle_dir=bundle_dir,
                task=_stub_task(),
                persona=_stub_persona(),
                bench_root=bench_root,
                eval_model="fake-model",
            )

    run_state = json.loads(
        (bundle_dir / "run_state.json").read_text(encoding="utf-8")
    )
    assert run_state["evaluation_status"] == "failed"


def test_score_bundle_rejects_invalid_eval_run_id(tmp_path, stubbed_pipeline):
    bench_root = tmp_path / "bench"
    bundle_dir = (
        bench_root
        / "results"
        / "server"
        / "X01"
        / "fullstack_practitioner"
        / "20260421_120000_abcd1234"
    )
    _make_bundle(bundle_dir)

    with pytest.raises(ValueError, match="must match"):
        score_bundle(
            bundle_dir=bundle_dir,
            task=_stub_task(),
            persona=_stub_persona(),
            bench_root=bench_root,
            eval_model="fake-model",
            eval_run_id="not-a-valid-id",
        )


def test_cli_scores_bundle_end_to_end(tmp_path, stubbed_pipeline):
    """Round-trip via ``python -m server.evaluator``.

    Stubs the task / persona loaders and the schema constructors so the
    test does not depend on real task JSON existing under bench_root.
    """
    bench_root = tmp_path / "bench"
    bundle_dir = (
        bench_root
        / "results"
        / "server"
        / "X01"
        / "fullstack_practitioner"
        / "20260421_120000_abcd1234"
    )
    _make_bundle(bundle_dir)

    from server.evaluator import __main__ as cli

    with (
        patch.object(cli, "_load_task", return_value=_stub_task()),
        patch.object(cli, "_load_persona", return_value=_stub_persona()),
    ):
        rc = cli.main(
            [
                "--bundle",
                str(bundle_dir),
                "--bench-root",
                str(bench_root),
                "--eval-model",
                "fake-model",
            ]
        )
    assert rc == 0

    bundle = load_bundle(bundle_dir)
    latest = paths.find_latest_eval_dir(
        bench_root=bench_root,
        task_id=bundle.task_id,
        persona_id=bundle.persona_id,
        session_id=bundle.session_id,
    )
    assert latest is not None
    assert (latest / "eval_meta.json").is_file()
