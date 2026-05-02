"""Tests for Bundle v1 dataclass + JSON serializer."""

from __future__ import annotations

import json

import pytest

from eval.contracts import bundle_io
from eval.contracts.bundle import (
    SCHEMA_VERSION,
    AgentMessage,
    AgentMetadata,
    Bundle,
    ConversationTurn,
    RuntimeInfo,
    SessionInfo,
    StudentMessage,
    ToolCall,
    WorkspaceFile,
)


def _make_bundle() -> Bundle:
    return Bundle(
        schema_version=SCHEMA_VERSION,
        task_id="S01_ma_crossover",
        task_version="2.2",
        task_spec_hash="abc123",
        persona_id="double_novice",
        session=SessionInfo(
            session_id="sess-1",
            start_ts="2026-05-02T10:00:00Z",
            end_ts="2026-05-02T10:14:32Z",
            termination_reason="tc_pass",
            turn_count=2,
        ),
        runtime=RuntimeInfo(
            sandbox_image="quant-tutor-env:v2.2",
            npc_model="gpt-5.2-2026-01-15",
            bench_eval_version="1.0.0",
            seed=42,
        ),
        agent_metadata=AgentMetadata(
            self_reported_name="claude-opus-4-7",
            self_reported_version="1.0",
            harness="ref_harness",
        ),
        conversation=[
            ConversationTurn(
                turn=1,
                agent=AgentMessage(text="hello", reasoning=None, attachments=[]),
                student=StudentMessage(text="hi back"),
                tool_calls=[
                    ToolCall(
                        call_id="tc_001",
                        tool="fetch_market_data",
                        args={"symbol": "AAPL"},
                        result_preview="...",
                        result_truncated=True,
                        ts="2026-05-02T10:01:00Z",
                        duration_ms=1234.0,
                        success=True,
                    ),
                ],
            ),
            ConversationTurn(
                turn=2,
                agent=AgentMessage(text="ok", reasoning="thinking"),
                student=None,
            ),
        ],
        workspace_manifest=[
            WorkspaceFile(path="AAPL_with_sma.csv", sha256="deadbeef", size=102400),
        ],
    )


def test_bundle_roundtrip_preserves_all_fields():
    b = _make_bundle()
    s = bundle_io.to_json(b)
    b2 = bundle_io.from_json(s)
    assert b2 == b


def test_bundle_write_and_read(tmp_path):
    b = _make_bundle()
    path = tmp_path / "bundle.json"
    bundle_io.write(b, path)
    b2 = bundle_io.read(path)
    assert b2 == b


def test_to_json_is_indented_and_unicode_safe():
    b = _make_bundle()
    s = bundle_io.to_json(b)
    # indent=2 means there's at least one newline + spaces in the output
    assert "\n  " in s
    # ensure_ascii=False keeps non-ASCII intact (regression guard)
    b2 = Bundle(
        schema_version=SCHEMA_VERSION,
        task_id="S01",
        task_version="1",
        task_spec_hash="h",
        persona_id="p",
        session=SessionInfo(session_id="s"),
        runtime=RuntimeInfo(),
        agent_metadata=AgentMetadata(),
        conversation=[
            ConversationTurn(turn=1, agent=AgentMessage(text="日本語チェック"))
        ],
    )
    out = bundle_io.to_json(b2)
    assert "日本語チェック" in out


def test_reader_ignores_unknown_top_level_fields():
    b = _make_bundle()
    raw = json.loads(bundle_io.to_json(b))
    raw["future_field"] = {"some": "future expansion"}
    raw["another_unknown"] = 42
    b2 = bundle_io.from_dict(raw)
    assert b2 == b


def test_reader_ignores_unknown_nested_fields():
    b = _make_bundle()
    raw = json.loads(bundle_io.to_json(b))
    raw["session"]["future_subfield"] = "x"
    raw["runtime"]["future_subfield"] = "y"
    raw["agent_metadata"]["future_subfield"] = "z"
    raw["conversation"][0]["future_subfield"] = "w"
    raw["conversation"][0]["agent"]["future_subfield"] = "v"
    raw["conversation"][0]["tool_calls"][0]["future_subfield"] = "u"
    raw["workspace_manifest"][0]["future_subfield"] = "t"
    b2 = bundle_io.from_dict(raw)
    assert b2 == b


def test_reader_fills_missing_optional_fields_with_defaults():
    minimal = {
        "schema_version": SCHEMA_VERSION,
        "task_id": "S01",
        "task_version": "1",
        "task_spec_hash": "h",
        "persona_id": "p",
        "session": {"session_id": "s"},
        "runtime": {},
        "agent_metadata": {},
        "conversation": [],
        "workspace_manifest": [],
    }
    b = bundle_io.from_dict(minimal)
    assert b.session.session_id == "s"
    assert b.session.start_ts == ""
    assert b.session.turn_count == 0
    assert b.runtime.seed is None
    # Missing optional fields fall back to dataclass defaults (see schema_evolution.md).
    assert b.runtime.bench_eval_version == "1.0.0"
    assert b.agent_metadata.harness == ""
    assert b.conversation == []
    assert b.workspace_manifest == []


def test_reader_rejects_missing_schema_version():
    raw = json.loads(bundle_io.to_json(_make_bundle()))
    del raw["schema_version"]
    with pytest.raises(bundle_io.BundleError, match="schema_version"):
        bundle_io.from_dict(raw)


def test_reader_rejects_incompatible_major_version():
    raw = json.loads(bundle_io.to_json(_make_bundle()))
    raw["schema_version"] = "2.0"
    with pytest.raises(bundle_io.BundleError, match="v1 only"):
        bundle_io.from_dict(raw)


def test_reader_accepts_minor_bump_within_v1():
    raw = json.loads(bundle_io.to_json(_make_bundle()))
    raw["schema_version"] = "1.5"
    raw["unrecognized_v1_5_field"] = "ok"
    b = bundle_io.from_dict(raw)
    assert b.schema_version == "1.5"


def test_reader_rejects_non_dict_payload():
    with pytest.raises(bundle_io.BundleError, match="JSON object"):
        bundle_io.from_dict([1, 2, 3])  # type: ignore[arg-type]


def test_turn_with_none_student_roundtrips():
    b = Bundle(
        schema_version=SCHEMA_VERSION,
        task_id="S01",
        task_version="1",
        task_spec_hash="h",
        persona_id="p",
        session=SessionInfo(session_id="s"),
        runtime=RuntimeInfo(),
        agent_metadata=AgentMetadata(),
        conversation=[
            ConversationTurn(turn=1, agent=AgentMessage(text="hi"), student=None),
        ],
    )
    b2 = bundle_io.from_json(bundle_io.to_json(b))
    assert b2.conversation[0].student is None
