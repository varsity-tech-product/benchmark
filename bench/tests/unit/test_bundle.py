"""Tests for Bundle v1 alpha dataclasses, JSON IO, and JSON Schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.contracts import bundle_io
from eval.contracts.bundle import (
    REFERENCE_ARTIFACT_KEY,
    SCHEMA_VERSION,
    Bundle,
    BundleTimestamps,
    Message,
    ToolCall,
    WorkspaceFile,
    WorkspaceSnapshot,
)
from eval.contracts.bundle_schema import BundleValidationError, validate_bundle_dict


def _make_bundle() -> Bundle:
    return Bundle(
        bundle_id="sess-1",
        schema_version=SCHEMA_VERSION,
        task_id="S01_ma_crossover",
        timestamps=BundleTimestamps(
            created_at="2026-05-02T10:00:00Z",
            started_at="2026-05-02T10:00:00Z",
            completed_at="2026-05-02T10:14:32Z",
            duration_seconds=872.0,
        ),
        agent_id="claude-opus-4-7",
        sandbox_digest={"sandbox_image": "quant-tutor-env:v2.2", "digest": ""},
        telemetry={"bench_eval_version": "1.0.0", "seed": 42},
        messages=[
            Message(
                message_id="msg_1",
                role="user",
                content="hi back",
                created_at="2026-05-02T10:00:00Z",
                turn_index=0,
            ),
            Message(
                message_id="msg_2",
                role="assistant",
                content="hello",
                created_at="2026-05-02T10:01:00Z",
                turn_index=0,
                attachments=[{"path": "AAPL_with_sma.csv", "kind": "csv"}],
            ),
        ],
        tool_calls=[
            ToolCall(
                tool_call_id="tc_001",
                tool_name="fetch_market_data",
                args={"symbol": "AAPL"},
                result={"rows": 10},
                created_at="2026-05-02T10:01:00Z",
                duration_ms=1234.0,
                success=True,
                turn_index=0,
            )
        ],
        artifacts={
            REFERENCE_ARTIFACT_KEY: {
                "persona_id": "double_novice",
                "session_id": "sess-1",
                "termination_reason": "tc_pass",
                "task_version": "2.2",
                "task_spec_hash": "abc123",
            }
        },
        workspace=WorkspaceSnapshot(
            root="agent_files",
            files=[
                WorkspaceFile(
                    path="AAPL_with_sma.csv",
                    sha256="0" * 64,
                    size_bytes=102400,
                )
            ],
        ),
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
    assert "\n  " in s
    b.messages[0].content = "日本語チェック"
    out = bundle_io.to_json(b)
    assert "日本語チェック" in out


def test_json_schema_accepts_current_bundle():
    raw = json.loads(bundle_io.to_json(_make_bundle()))
    validate_bundle_dict(raw)


def test_json_schema_rejects_missing_envelope_field():
    raw = json.loads(bundle_io.to_json(_make_bundle()))
    del raw["bundle_id"]
    with pytest.raises(BundleValidationError, match="bundle_id"):
        validate_bundle_dict(raw)


def test_reader_ignores_unknown_fields():
    b = _make_bundle()
    raw = json.loads(bundle_io.to_json(b))
    raw["future_field"] = {"some": "future expansion"}
    raw["messages"][0]["future_subfield"] = "v"
    raw["tool_calls"][0]["future_subfield"] = "u"
    raw["workspace"]["files"][0]["future_subfield"] = "t"
    b2 = bundle_io.from_dict(raw)
    assert b2 == b


def test_reader_fills_missing_optional_fields_with_defaults():
    minimal = {
        "bundle_id": "b",
        "schema_version": SCHEMA_VERSION,
        "task_id": "S01",
        "timestamps": {"created_at": "2026-05-02T10:00:00Z"},
        "agent_id": "agent",
        "sandbox_digest": {},
        "telemetry": {},
        "messages": [],
        "tool_calls": [],
        "artifacts": {},
        "workspace": {"files": []},
    }
    b = bundle_io.from_dict(minimal)
    assert b.bundle_id == "b"
    assert b.timestamps.completed_at == ""
    assert b.messages == []
    assert b.workspace.files == []


def test_reference_properties_read_quanttutor_artifact():
    b = _make_bundle()
    assert b.session_id == "sess-1"
    assert b.persona_id == "double_novice"
    assert b.termination_reason == "tc_pass"
    assert b.workspace_manifest == b.workspace.files


def test_reader_rejects_missing_schema_version():
    raw = json.loads(bundle_io.to_json(_make_bundle()))
    del raw["schema_version"]
    with pytest.raises(bundle_io.BundleError, match="schema_version"):
        bundle_io.from_dict(raw)


def test_reader_rejects_incompatible_major_version():
    raw = json.loads(bundle_io.to_json(_make_bundle()))
    raw["schema_version"] = "2.0.0"
    with pytest.raises(bundle_io.BundleError, match="v1 family"):
        bundle_io.from_dict(raw)


def test_reader_accepts_minor_bump_within_v1():
    raw = json.loads(bundle_io.to_json(_make_bundle()))
    raw["schema_version"] = "1.5.0"
    raw["unrecognized_v1_5_field"] = "ok"
    b = bundle_io.from_dict(raw)
    assert b.schema_version == "1.5.0"


def test_reader_rejects_non_dict_payload():
    with pytest.raises(bundle_io.BundleError, match="JSON object"):
        bundle_io.from_dict([1, 2, 3])  # type: ignore[arg-type]


def test_fixture_bundles_validate_against_json_schema():
    fixture_dir = (
        Path(__file__).resolve().parents[2]
        / "eval"
        / "contracts"
        / "fixtures"
        / "bundle_v1_alpha"
    )
    fixtures = sorted(fixture_dir.glob("*.json"))
    assert len(fixtures) >= 3
    for fixture in fixtures:
        raw = json.loads(fixture.read_text(encoding="utf-8"))
        validate_bundle_dict(raw)
