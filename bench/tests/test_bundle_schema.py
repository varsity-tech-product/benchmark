"""Bundle schema contract tests (issue #46 slice 1).

Producer (``save_run_state``) writes a self-contained bundle; consumer
(``load_bundle``) reconstructs the input set ``evaluate_task`` reads. These
tests pin that contract so future migration steps can split the producer
and consumer into separate processes without quietly losing fields.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from server.storage.bundle import (
    AGENT_FILES_DIRNAME,
    BUNDLE_SCHEMA_VERSION,
    MANIFEST_FILENAME,
    RUN_STATE_FILENAME,
    load_bundle,
    write_manifest,
)
from server.storage.result_writer import save_run_state


def _make_fixture_bundle(bundle_dir: Path) -> Path:
    workspace = bundle_dir.parent / "_ws"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "solution.py").write_text(
        "print('hello')\n", encoding="utf-8"
    )
    (workspace / "notes.md").write_text("scratch\n", encoding="utf-8")

    conversation = [
        {"role": "user", "content": "Help me with the SMA bug."},
        {"role": "assistant", "content": "Sure — let's look at the window."},
        {"role": "user", "content": "Got it, thanks."},
    ]
    tool_logs = [
        {
            "name": "execute_python",
            "args": {"code": "print(1)"},
            "success": True,
            "duration_ms": 12.0,
            "turn_index": 1,
            "result": "1\n",
        },
        {
            "name": "search_web",
            "args": {"query": "pandas rolling"},
            "success": True,
            "duration_ms": 80.0,
            "turn_index": 2,
            "result": "...",
        },
    ]
    distractor_names = ["search_web"]

    save_run_state(
        result_dir=bundle_dir,
        conversation=conversation,
        tool_logs=tool_logs,
        workspace_path=str(workspace),
        simulator_cost=0.0,
        tc_checker_cost=0.0,
        duration_seconds=4.2,
        distractor_names=distractor_names,
        task_id="X01",
        session_id="abcd1234efgh5678abcd1234efgh5678",
        persona_id="fullstack_practitioner",
        session_status="completed",
        termination_reason="tc_complete",
        run_id="run_test_X01",
        public_task_label="X01",
    )
    return bundle_dir


def test_save_run_state_writes_manifest(tmp_path):
    bundle_dir = tmp_path / "bundle"
    _make_fixture_bundle(bundle_dir)

    manifest_path = bundle_dir / MANIFEST_FILENAME
    assert manifest_path.is_file(), "save_run_state must emit manifest.json"

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["bundle_schema_version"] == BUNDLE_SCHEMA_VERSION
    assert manifest["task_id"] == "X01"
    assert manifest["persona_id"] == "fullstack_practitioner"
    assert manifest["session_id"].startswith("abcd1234")
    assert manifest["run_id"] == "run_test_X01"
    assert manifest["session_status"] == "completed"
    assert manifest["termination_reason"] == "tc_complete"

    artifacts = manifest["artifacts"]
    assert artifacts["run_state"] == RUN_STATE_FILENAME
    assert artifacts["agent_files"].rstrip("/") == AGENT_FILES_DIRNAME


def test_load_bundle_reconstitutes_evaluator_inputs(tmp_path):
    bundle_dir = tmp_path / "bundle"
    _make_fixture_bundle(bundle_dir)

    bundle = load_bundle(bundle_dir)

    assert bundle.task_id == "X01"
    assert bundle.persona_id == "fullstack_practitioner"
    assert bundle.session_id.startswith("abcd1234")

    assert [m["role"] for m in bundle.conversation] == [
        "user",
        "assistant",
        "user",
    ]
    assert len(bundle.tool_logs) == 2
    # Rehydrated to ToolCallLog dataclass instances so downstream
    # evaluators that use attribute access keep working.
    assert bundle.tool_logs[0].name == "execute_python"
    assert bundle.tool_logs[0].args == {"code": "print(1)"}
    assert bundle.tool_logs[0].success is True
    assert bundle.tool_logs[0].duration_ms == 12.0
    assert bundle.distractor_names == ["search_web"]

    workspace = Path(bundle.workspace_path)
    assert workspace.is_dir()
    assert (workspace / "solution.py").read_text(encoding="utf-8") == "print('hello')\n"


def test_load_bundle_rejects_missing_manifest(tmp_path):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / RUN_STATE_FILENAME).write_text("{}", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        load_bundle(bundle_dir)


def test_load_bundle_rejects_unsupported_major(tmp_path):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / RUN_STATE_FILENAME).write_text(
        json.dumps({"task_id": "X01"}), encoding="utf-8"
    )
    (bundle_dir / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "bundle_schema_version": "99.0.0",
                "artifacts": {"run_state": RUN_STATE_FILENAME},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unsupported bundle schema"):
        load_bundle(bundle_dir)


def test_write_manifest_omits_missing_optional_artifacts(tmp_path):
    bundle_dir = tmp_path / "bundle"
    bundle_dir.mkdir()
    (bundle_dir / RUN_STATE_FILENAME).write_text(
        json.dumps({"task_id": "X02", "session_id": "s", "persona_id": "p"}),
        encoding="utf-8",
    )

    write_manifest(
        bundle_dir,
        {"task_id": "X02", "session_id": "s", "persona_id": "p"},
    )

    manifest = json.loads(
        (bundle_dir / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert "agent_files" not in manifest["artifacts"]
    assert "run_state_md" not in manifest["artifacts"]
    assert manifest["artifacts"]["run_state"] == RUN_STATE_FILENAME


def test_load_bundle_rejects_missing_declared_artifact(tmp_path):
    """If the manifest lists agent_files/ but it was lost in transit,
    fail at load — silently scoring against an empty workspace would
    hide the corruption."""
    bundle_dir = tmp_path / "bundle"
    _make_fixture_bundle(bundle_dir)

    # Simulate post-write corruption: the agent_files/ directory disappears.
    import shutil

    shutil.rmtree(bundle_dir / AGENT_FILES_DIRNAME)

    with pytest.raises(FileNotFoundError, match="agent_files"):
        load_bundle(bundle_dir)


def test_evaluator_inputs_match_pipeline_signature(tmp_path):
    """LoadedBundle must expose every per-bundle input ``evaluate_task`` reads.

    Pins the contract: if a future change adds a new bundle-side input to
    ``pipeline.evaluate_task``, the producer/loader must learn to carry it.
    """
    import inspect

    from server.eval.pipeline import evaluate_task

    bundle_inputs = {
        "workspace_path",
        "conversation",
        "tool_logs",
        "distractor_names",
    }
    sig_params = set(inspect.signature(evaluate_task).parameters)
    missing = bundle_inputs - sig_params
    assert not missing, (
        "evaluate_task no longer accepts these bundle-side inputs; "
        f"bundle schema is out of sync: {missing}"
    )

    bundle_dir = tmp_path / "bundle"
    _make_fixture_bundle(bundle_dir)
    bundle = load_bundle(bundle_dir)

    for name in bundle_inputs:
        assert hasattr(bundle, name), (
            f"LoadedBundle missing '{name}' — evaluate_task cannot be fed "
            f"from this bundle"
        )
