"""Tests for run_state.json -> bundle.json v1 backfill."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from eval.backfill.run_state_to_bundle import backfill, main
from eval.contracts import bundle_io


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
    """Build a fake bench root with one task JSON the backfill can find."""
    bench = tmp_path / "bench"
    task_dir = bench / "tasks" / "layer2" / "strategy"
    task_dir.mkdir(parents=True)
    (task_dir / f"{task_id}.json").write_text(
        json.dumps({"task_id": task_id, "version": "2.2", "category": "strategy"}),
        encoding="utf-8",
    )
    return bench


def test_backfill_populates_top_level_fields(tmp_path):
    state = _minimal_run_state()
    run_state = _write_run_state(tmp_path / "result", state)
    bench_root = _bench_root_with_task(tmp_path, state["task_id"])

    out = backfill(run_state, bench_root=bench_root)
    bundle = bundle_io.read(out)

    assert bundle.schema_version == "1.0"
    assert bundle.task_id == state["task_id"]
    assert bundle.task_version == "2.2"
    assert bundle.task_spec_hash  # non-empty sha256
    assert bundle.persona_id == state["persona_id"]
    assert bundle.session.session_id == state["session_id"]
    assert bundle.session.termination_reason == "tc_pass"


def test_backfill_writes_bundle_next_to_run_state_by_default(tmp_path):
    run_state = _write_run_state(tmp_path / "result", _minimal_run_state())
    out = backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    assert out == run_state.parent / "bundle.json"
    assert out.exists()


def test_conversation_pairs_alternating_user_assistant(tmp_path):
    state = _minimal_run_state(
        conversation=_conv_pair("u0", "a0", ts_start=100)
        + _conv_pair("u1", "a1", ts_start=200),
    )
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    assert [t.turn for t in bundle.conversation] == [1, 2]
    assert bundle.conversation[0].user.text == "u0"
    assert bundle.conversation[0].agent.text == "a0"
    assert bundle.conversation[1].user.text == "u1"
    assert bundle.conversation[1].agent.text == "a1"
    assert bundle.session.turn_count == 2


def test_trailing_lone_user_becomes_turn_with_empty_agent(tmp_path):
    """Repro of X01-shaped legacy bundle: 5 conv entries (u,a,u,a,u) +
    3 send_message logs where the third is the synthetic 'agent_stuck'
    auto-completion. Bundle should have 3 user-led turns; the third
    has empty agent text — not duplicated text from the synthetic log."""
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
    assert bundle.session.turn_count == 3
    assert [t.user.text for t in bundle.conversation] == ["u0", "u1", "u2-no-reply"]
    assert [t.agent.text for t in bundle.conversation] == ["a0", "a1", ""]
    # The synthetic send_message log content must not bleed into the third agent message.
    assert "Repeated message for completion" not in bundle.conversation[2].agent.text


def test_domain_tool_only_logs_yield_full_conversation(tmp_path):
    """Repro of run-single shape: conversation is fully populated, tool_logs
    are domain tools only (no send_message). Bundle should mirror the
    persisted conversation and attach tool_logs by turn_index."""
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
    assert bundle.session.turn_count == 3
    assert [t.tool_calls[0].tool for t in bundle.conversation] == [
        "file_read",
        "shell_exec",
        "run_lean_backtest",
    ]
    assert bundle.conversation[1].tool_calls[0].args == {"command": "ls"}


def test_send_message_logs_excluded_from_tool_calls(tmp_path):
    """The synthetic send_message NPC tool is the conversation conduit, not a
    domain tool call — must not appear in any turn's tool_calls list."""
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
    tools = bundle.conversation[0].tool_calls
    assert len(tools) == 1
    assert tools[0].tool == "file_read"


def test_send_message_attachments_carry_into_agent_message(tmp_path):
    """attachments live on the synthetic send_message log's args, not on the
    conversation entry — backfill must pull them forward to agent.attachments."""
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
    assert bundle.conversation[0].agent.attachments == attachments


def test_truncates_long_tool_results(tmp_path):
    long_blob = "x" * 10000
    state = _minimal_run_state(
        tool_logs=[_tool_log("shell_exec", turn_index=0, result=long_blob)],
    )
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    tc = bundle.conversation[0].tool_calls[0]
    assert tc.result_truncated is True
    assert tc.result_preview.endswith("...")
    assert len(tc.result_preview) <= 4096 + 3


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

    paths = [w.path for w in bundle.workspace_manifest]
    assert paths == sorted(paths)
    assert "Algorithm.cs" in paths
    assert "subdir/data.csv" in paths

    cs_entry = next(w for w in bundle.workspace_manifest if w.path == "Algorithm.cs")
    assert cs_entry.size == len("class A {}")
    assert cs_entry.sha256 == hashlib.sha256(b"class A {}").hexdigest()


def test_handles_missing_task_json(tmp_path):
    state = _minimal_run_state(task_id="UNKNOWN_TASK_99")
    run_state = _write_run_state(tmp_path / "result", state)
    bench_root = tmp_path / "bench"
    (bench_root / "tasks" / "layer2").mkdir(parents=True)

    bundle = bundle_io.read(backfill(run_state, bench_root=bench_root))
    assert bundle.task_version == ""
    assert bundle.task_spec_hash == ""


def test_handles_empty_conversation(tmp_path):
    state = _minimal_run_state(conversation=[], tool_logs=[])
    run_state = _write_run_state(tmp_path / "result", state)
    bundle = bundle_io.read(
        backfill(run_state, bench_root=_bench_root_with_task(tmp_path, "S01_ma_crossover"))
    )
    assert bundle.conversation == []
    assert bundle.session.turn_count == 0


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
    """Backfill any existing run_state.json in bench/results/server/ and
    verify the result deserializes round-trip clean."""
    bench_root = Path(__file__).resolve().parents[2]
    samples = list((bench_root / "results" / "server").rglob("run_state.json"))
    if not samples:
        pytest.skip("No real run_state.json samples available")
    sample = samples[0]

    out = tmp_path / "bundle.json"
    backfill(sample, bench_root=bench_root, output=out)

    bundle = bundle_io.read(out)
    assert bundle.schema_version == "1.0"
    assert bundle.task_id
    assert bundle.persona_id
    assert bundle.session.session_id


def test_real_run_single_sample_smokes(tmp_path):
    """Same smoke for the older bench/results/run-single/ shape (domain tools
    only, no send_message logs)."""
    bench_root = Path(__file__).resolve().parents[2]
    samples = list((bench_root / "results" / "run-single").rglob("run_state.json"))
    if not samples:
        pytest.skip("No run-single samples available")
    sample = samples[0]

    out = tmp_path / "bundle.json"
    backfill(sample, bench_root=bench_root, output=out)
    bundle = bundle_io.read(out)
    assert bundle.schema_version == "1.0"
