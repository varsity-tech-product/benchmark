"""Tests for run_state.json -> Bundle v1 alpha backfill."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eval.backfill.run_state_to_bundle import backfill, main
from eval.contracts import bundle_io
from eval.contracts.bundle import REFERENCE_ARTIFACT_KEY, SCHEMA_VERSION
from eval.contracts.bundle_schema import validate_bundle_path


def _write_run_state(result_dir: Path, payload: dict) -> Path:
    result_dir.mkdir(parents=True, exist_ok=True)
    p = result_dir / "run_state.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


def _conv_pair(user: str, agent: str, *, ts_start: float = 0.0) -> list[dict]:
    return [
        {"role": "user", "content": user, "ts": ts_start},
        {"role": "assistant", "content": agent, "ts": ts_start + 1.0},
    ]


def _tool_log(name: str, *, turn_index: int, args=None, result="", ms=1.0) -> dict:
    return {
        "name": name,
        "args": args or {},
        "call_id": f"{name}_{turn_index}",
        "result": result,
        "timestamp": 1776835000.0 + turn_index,
        "duration_ms": ms,
        "success": True,
        "turn_index": turn_index,
    }


def _minimal_run_state(**overrides) -> dict:
    base = {
        "task_id": "S01_ma_crossover",
        "persona_id": "double_novice",
        "session_id": "sess-abc",
        "timestamp": "2026-05-02T10:14:32",
        "termination_reason": "tc_pass",
        "conversation": _conv_pair("hi", "hello"),
        "tool_logs": [],
    }
    base.update(overrides)
    return base


def _bench_root_with_task(tmp_path: Path, task_id: str) -> Path:
    bench = tmp_path / "bench"
    task_dir = bench / "tasks" / "layer2" / "strategy"
    task_dir.mkdir(parents=True)
    (task_dir / f"{task_id}.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "version": "2.2",
                "category": "strategy",
                "environment": {"sandbox_image": "quant-tutor-env:v2.2"},
            }
        ),
        encoding="utf-8",
    )
    return bench


def _qtb(bundle):
    return bundle.artifacts[REFERENCE_ARTIFACT_KEY]


def test_backfill_populates_top_level_fields(tmp_path):
    state = _minimal_run_state()
    run_state = _write_run_state(tmp_path / "result", state)
    bench_root = _bench_root_with_task(tmp_path, state["task_id"])

    out = backfill(run_state, bench_root=bench_root)
    bundle = bundle_io.read(out)

    assert bundle.schema_version == SCHEMA_VERSION
    assert bundle.bundle_id == state["session_id"]
    assert bundle.task_id == state["task_id"]
    assert bundle.agent_id == "ref_harness"
    assert bundle.sandbox_digest["sandbox_image"] == "quant-tutor-env:v2.2"
    assert _qtb(bundle)["task_version"] == "2.2"
    assert _qtb(bundle)["task_spec_hash"]
    assert bundle.persona_id == state["persona_id"]
    assert bundle.session_id == state["session_id"]
    assert bundle.termination_reason == "tc_pass"
    validate_bundle_path(out)


def test_backfill_writes_bundle_next_to_run_state_by_default(tmp_path):
    run_state = _write_run_state(tmp_path / "result", _minimal_run_state())
    out = backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    assert out == run_state.parent / "bundle.json"
    assert out.exists()


def test_backfill_legacy_bundle_id_is_unique_without_session_or_run_id(tmp_path):
    bench_root = _bench_root_with_task(tmp_path, "B01_interpret_metrics")
    state = _minimal_run_state(
        task_id="B01_interpret_metrics",
        conversation=_conv_pair("u", "a"),
    )
    del state["session_id"]
    left = _write_run_state(tmp_path / "results" / "agent_a" / "result", state)
    right = _write_run_state(tmp_path / "results" / "agent_b" / "result", state)

    left_bundle = bundle_io.read(backfill(left, bench_root=bench_root))
    right_bundle = bundle_io.read(backfill(right, bench_root=bench_root))

    assert left_bundle.bundle_id.startswith("legacy-B01_interpret_metrics-")
    assert right_bundle.bundle_id.startswith("legacy-B01_interpret_metrics-")
    assert left_bundle.bundle_id != right_bundle.bundle_id


def test_conversation_messages_preserve_order_and_turn_index(tmp_path):
    state = _minimal_run_state(
        conversation=_conv_pair("u0", "a0", ts_start=100)
        + _conv_pair("u1", "a1", ts_start=200),
    )
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    assert [m.role for m in bundle.messages] == ["user", "assistant", "user", "assistant"]
    assert [m.content for m in bundle.messages] == ["u0", "a0", "u1", "a1"]
    assert [m.turn_index for m in bundle.messages] == [0, 0, 1, 1]
    assert bundle.telemetry["message_count"] == 4


def test_trailing_lone_user_is_preserved_without_synthetic_agent_text(tmp_path):
    state = _minimal_run_state(
        conversation=_conv_pair("u0", "a0", ts_start=100)
        + _conv_pair("u1", "a1", ts_start=200)
        + [{"role": "user", "content": "u2-no-reply", "ts": 300}],
        tool_logs=[
            _tool_log("send_message", turn_index=0, args={"text": "a0"}),
            _tool_log("send_message", turn_index=1, args={"text": "a1"}),
            _tool_log(
                "send_message",
                turn_index=2,
                args={"text": "Repeated message for completion"},
                result='{"user_message": "", "status": "completed", "reason": "agent_stuck"}',
            ),
        ],
    )
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    assert [m.content for m in bundle.messages if m.role == "user"] == [
        "u0",
        "u1",
        "u2-no-reply",
    ]
    assert [m.content for m in bundle.messages if m.role == "assistant"] == ["a0", "a1"]
    assert len([tc for tc in bundle.tool_calls if tc.tool_name == "send_message"]) == 3


def test_domain_tool_logs_are_flat_generic_tool_calls(tmp_path):
    state = _minimal_run_state(
        conversation=_conv_pair("u0", "a0", ts_start=100)
        + _conv_pair("u1", "a1", ts_start=200)
        + _conv_pair("u2", "a2", ts_start=300),
        tool_logs=[
            _tool_log("file_read", turn_index=0, args={"path": "x"}, result="ok"),
            _tool_log("shell_exec", turn_index=1, args={"command": "ls"}, result="a\nb"),
            _tool_log("run_lean_backtest", turn_index=2, args={}, result="done"),
        ],
    )
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    assert [tc.tool_name for tc in bundle.tool_calls] == [
        "file_read",
        "shell_exec",
        "run_lean_backtest",
    ]
    assert bundle.tool_calls[1].args == {"command": "ls"}
    assert bundle.tool_calls[1].result == "a\nb"


def test_send_message_logs_are_marked_as_conversation_transport(tmp_path):
    state = _minimal_run_state(
        tool_logs=[
            _tool_log("send_message", turn_index=0, args={"text": "hello"}),
            _tool_log("file_read", turn_index=0, args={"path": "x"}),
        ],
    )
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    assert [tc.tool_name for tc in bundle.tool_calls] == ["send_message", "file_read"]
    assert bundle.tool_calls[0].metadata["conversation_transport"] is True


def test_send_message_attachments_carry_into_assistant_message(tmp_path):
    attachments = [{"path": "report.csv", "kind": "csv"}]
    state = _minimal_run_state(
        tool_logs=[
            _tool_log("send_message", turn_index=0, args={"text": "hi", "attachments": attachments}),
        ],
    )
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    assistant = next(m for m in bundle.messages if m.role == "assistant")
    assert assistant.attachments == attachments


def test_consecutive_assistant_attachments_match_send_message_turn(tmp_path):
    state = _minimal_run_state(
        conversation=[
            {"role": "assistant", "content": "a0"},
            {"role": "assistant", "content": "a1"},
        ],
        tool_logs=[
            _tool_log(
                "send_message",
                turn_index=0,
                args={"text": "a0", "attachments": [{"path": "a0.csv"}]},
            ),
            _tool_log(
                "send_message",
                turn_index=1,
                args={"text": "a1", "attachments": [{"path": "a1.csv"}]},
            ),
        ],
    )
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )

    assert [message.turn_index for message in bundle.messages] == [0, 1]
    assert [message.attachments for message in bundle.messages] == [
        [{"path": "a0.csv"}],
        [{"path": "a1.csv"}],
    ]


def test_preserves_long_tool_results_as_artifact_json(tmp_path):
    long_blob = "x" * 10000
    state = _minimal_run_state(
        tool_logs=[_tool_log("shell_exec", turn_index=0, result=long_blob)],
    )
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    assert bundle.tool_calls[0].result == long_blob


def test_workspace_manifest_hashes_files(tmp_path):
    result_dir = tmp_path / "result"
    run_state = _write_run_state(result_dir, _minimal_run_state())
    workspace = result_dir / "agent_files"
    workspace.mkdir()
    (workspace / "Algorithm.cs").write_text("class A {}", encoding="utf-8")
    (workspace / "subdir").mkdir()
    (workspace / "subdir" / "data.csv").write_text("a,b\n1,2", encoding="utf-8")

    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )

    paths = [w.path for w in bundle.workspace.files]
    assert paths == sorted(paths)
    assert "Algorithm.cs" in paths
    assert "subdir/data.csv" in paths

    cs_entry = next(w for w in bundle.workspace.files if w.path == "Algorithm.cs")
    assert cs_entry.size_bytes == len("class A {}")
    assert cs_entry.size == len("class A {}")
    assert cs_entry.sha256 == hashlib.sha256(b"class A {}").hexdigest()


def test_handles_missing_task_json(tmp_path):
    state = _minimal_run_state(task_id="UNKNOWN_TASK_99")
    run_state = _write_run_state(tmp_path / "result", state)
    bench_root = tmp_path / "bench"
    (bench_root / "tasks" / "layer2").mkdir(parents=True)

    bundle = bundle_io.read(backfill(run_state, bench_root=bench_root))
    assert _qtb(bundle)["task_version"] == ""
    assert _qtb(bundle)["task_spec_hash"] == ""


def test_handles_empty_conversation(tmp_path):
    state = _minimal_run_state(conversation=[], tool_logs=[])
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    assert bundle.messages == []
    assert bundle.telemetry["message_count"] == 0


def test_main_recursive_walks_results_tree(tmp_path):
    bench_root = _bench_root_with_task(tmp_path, "S01_ma_crossover")
    results = tmp_path / "results"
    for sid in ("a", "b", "c"):
        _write_run_state(results / "S01" / "p" / f"20260502_{sid}", _minimal_run_state())

    rc = main(["--recursive", "--bench-root", str(bench_root), str(results)])
    assert rc == 0

    bundles = sorted(results.rglob("bundle.json"))
    assert len(bundles) == 3


def test_main_skips_existing_bundle_unless_force(tmp_path):
    bench_root = _bench_root_with_task(tmp_path, "S01_ma_crossover")
    result_dir = tmp_path / "result"
    run_state = _write_run_state(result_dir, _minimal_run_state())

    rc1 = main(["--bench-root", str(bench_root), str(run_state)])
    assert rc1 == 0
    first_mtime = (result_dir / "bundle.json").stat().st_mtime_ns

    rc2 = main(["--bench-root", str(bench_root), str(run_state)])
    assert rc2 == 0
    assert (result_dir / "bundle.json").stat().st_mtime_ns == first_mtime

    rc3 = main(["--force", "--bench-root", str(bench_root), str(run_state)])
    assert rc3 == 0
    assert (result_dir / "bundle.json").stat().st_mtime_ns >= first_mtime


def test_main_rejects_path_with_no_run_state(tmp_path):
    with pytest.raises(FileNotFoundError):
        main([str(tmp_path)])


def test_real_sample_smokes(tmp_path):
    bench_root = Path(__file__).resolve().parents[2]
    samples = list((bench_root / "results" / "server").rglob("run_state.json"))
    if not samples:
        pytest.skip("No real run_state.json samples available")
    sample = samples[0]

    out = tmp_path / "bundle.json"
    backfill(sample, bench_root=bench_root, output=out)

    bundle = bundle_io.read(out)
    assert bundle.schema_version == SCHEMA_VERSION
    assert bundle.task_id
    assert bundle.persona_id
    assert bundle.session_id
    validate_bundle_path(out)


def test_real_run_single_sample_smokes(tmp_path):
    bench_root = Path(__file__).resolve().parents[2]
    samples = list((bench_root / "results" / "run-single").rglob("run_state.json"))
    if not samples:
        pytest.skip("No run-single samples available")
    sample = samples[0]

    out = tmp_path / "bundle.json"
    backfill(sample, bench_root=bench_root, output=out)
    bundle = bundle_io.read(out)
    assert bundle.schema_version == SCHEMA_VERSION
    validate_bundle_path(out)
