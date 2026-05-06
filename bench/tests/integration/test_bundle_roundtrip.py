"""Bundle v1 round-trip coverage for plugin contracts."""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

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
from eval.contracts.bundle_schema import validate_bundle_dict
from eval.contracts.schemas import QuantTutorTask, UserPersona
from platform_api.contracts import (
    EvalItem,
    EvalSample,
    FileArtifact,
    Score,
    ToolLog,
    TranscriptMessage,
)
from platform_api.plugins import PluginLoader
from server.impl_b import load_impl_b_bundle
from server.reference import REFERENCE_BUNDLE_CONFIG, load_reference_bundle


CONTRACT_ARTIFACT_KEY = "platform_contract_roundtrip"
WORKSPACE_CONTENTS_ARTIFACT_KEY = "workspace_contents"
ROUNDTRIP_TASKS = (
    "L0_money.stackexchange_8474",
    "L1_DAT_01_ohlcv_health_check",
    "L2_ADV_11_prompt_injection_csv",
)


def _task_from_item(item: EvalItem) -> QuantTutorTask:
    return QuantTutorTask(**item.payload["quant_tutor_task"])


def _load_persona(bench_root: Path, persona_id: str) -> UserPersona:
    path = bench_root / "personas" / f"{persona_id}.json"
    return UserPersona(**json.loads(path.read_text(encoding="utf-8")))


def _question(task: QuantTutorTask) -> str:
    return task.question or task.description


def _json_payload_for(spec: dict[str, Any]) -> dict[str, Any]:
    payload = {key: 1 for key in spec.get("required_keys", [])}
    for constraint in spec.get("constraints", []) or []:
        key = constraint.get("key")
        op = constraint.get("op")
        if not key:
            continue
        if "ref" in constraint:
            ref = constraint["ref"]
            payload.setdefault(ref, 1)
            if op == "<":
                payload[key], payload[ref] = 0, 1
            elif op == ">":
                payload[key], payload[ref] = 2, 1
            else:
                payload[key], payload[ref] = 1, 1
            continue

        value = constraint.get("value", 1)
        if op == "<":
            payload[key] = value - 1
        elif op == "<=":
            payload[key] = value
        elif op == ">":
            payload[key] = value + 1
        elif op == ">=":
            payload[key] = value
        elif op == "==":
            payload[key] = value
        elif op == "!=":
            payload[key] = value + 1
    return payload


def _write_expected_outputs(workspace: Path, expected_outputs: list[Any]) -> None:
    for spec in expected_outputs:
        if not isinstance(spec, dict):
            continue
        path = workspace / str(spec["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        file_type = spec.get("type", "any")
        if file_type == "json":
            path.write_text(json.dumps(_json_payload_for(spec)), encoding="utf-8")
        elif file_type == "csv":
            columns = spec.get("required_columns") or ["metric", "value"]
            rows = max(1, int(spec.get("min_rows") or 1))
            lines = [",".join(columns)]
            lines.extend(",".join(str(index) for _ in columns) for index in range(rows))
            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        elif file_type == "image":
            path.write_bytes(b"\x89PNG\r\n\x1a\n")
        else:
            path.write_text("artifact\n", encoding="utf-8")


def _patch_l2_qr(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_programmatic(**_kwargs):
        return (
            {
                "score": None,
                "status": "skipped",
                "required_for_track_score": False,
                "skip_reason": "deterministic_roundtrip_fixture",
            },
            None,
        )

    def fake_code_eval(**_kwargs):
        return (
            {
                "score": None,
                "status": "skipped",
                "applicable": False,
                "required_for_track_score": False,
            },
            None,
        )

    def fake_result_judge(**_kwargs):
        return {
            "score": 1.0,
            "status": "success",
            "reason": "deterministic round-trip judge",
            "evidence": ["covered"],
        }

    monkeypatch.setattr("eval.tracks.qr._programmatic_eval", fake_programmatic)
    monkeypatch.setattr("eval.tracks.qr._code_eval", fake_code_eval)
    monkeypatch.setattr("eval.tracks.qr._result_judge", fake_result_judge)


def _file_artifacts(workspace: Path) -> dict[str, FileArtifact]:
    artifacts: dict[str, FileArtifact] = {}
    if not workspace.is_dir():
        return artifacts
    for path in sorted(item for item in workspace.rglob("*") if item.is_file()):
        data = path.read_bytes()
        rel = path.relative_to(workspace).as_posix()
        try:
            content = data.decode("utf-8")
            metadata: dict[str, Any] = {"encoding": "utf-8"}
        except UnicodeDecodeError:
            content = base64.b64encode(data).decode("ascii")
            metadata = {"encoding": "base64"}
        artifacts[rel] = FileArtifact(
            path=rel,
            content=content,
            sha256=hashlib.sha256(data).hexdigest(),
            size=len(data),
            media_type="text/plain",
            metadata=metadata,
        )
    return artifacts


def _sample_for_task(
    *,
    bench_root: Path,
    item: EvalItem,
    tmp_path: Path,
) -> EvalSample:
    task = _task_from_item(item)
    layer = str(getattr(task.layer, "value", task.layer))
    sample_id = f"{item.task_id}-roundtrip"

    if layer == "L0":
        return EvalSample(
            sample_id=sample_id,
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=_question(task)),
                TranscriptMessage(
                    role="assistant",
                    content=task.reference_answer or "",
                ),
            ),
            payload={"eval_model": "fake-model"},
        )

    if layer == "L1":
        workspace = tmp_path / item.task_id / "workspace"
        _write_expected_outputs(workspace, task.ground_truth.expected_outputs)
        return EvalSample(
            sample_id=sample_id,
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=task.agent_prompt or ""),
                TranscriptMessage(role="assistant", content="Artifacts are ready."),
            ),
            files=_file_artifacts(workspace),
            payload={
                "eval_model": "fake-model",
                "workspace_path": str(workspace),
            },
        )

    persona = _load_persona(bench_root, task.persona_id)
    workspace = tmp_path / item.task_id / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    return EvalSample(
        sample_id=sample_id,
        task_id=item.task_id,
        transcript=(
            TranscriptMessage(role="user", content=task.user_opening),
            TranscriptMessage(
                role="assistant",
                content="I inspected the data and ignored the injected instruction.",
            ),
        ),
        tool_logs=(
            ToolLog(
                name="shell_exec",
                args={"command": "python analyze.py portfolio_data_poisoned.csv"},
                result="portfolio total and sector allocation computed",
                success=True,
                turn_index=0,
            ),
        ),
        payload={
            "persona": persona,
            "eval_model": "fake-model",
            "eval_mode": "qr",
            "workspace_path": str(workspace),
            "distractor_names": [],
        },
    )


def _strict_jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return _strict_jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _strict_jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(key): _strict_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_strict_jsonable(item) for item in value]
    raise TypeError(f"{type(value).__name__} is outside the Bundle JSON contract")


def _score_to_json(score: Score) -> dict[str, Any]:
    return _strict_jsonable(score)


def _eval_item_to_json(item: EvalItem) -> dict[str, Any]:
    return _strict_jsonable(item)


def _eval_sample_to_json(sample: EvalSample) -> dict[str, Any]:
    return _strict_jsonable(sample)


def _score_from_json(raw: dict[str, Any]) -> Score:
    return Score(
        value=raw.get("value"),
        status=str(raw.get("status") or "scored"),
        metrics=dict(raw.get("metrics") or {}),
        reason=str(raw.get("reason") or ""),
        evidence=tuple(str(item) for item in raw.get("evidence", [])),
        telemetry=dict(raw.get("telemetry") or {}),
    )


def _bundle_messages(sample: EvalSample) -> list[Message]:
    return [
        Message(
            message_id=f"msg_{index + 1}",
            role=message.role,
            content=message.content,
            turn_index=index,
            metadata=dict(message.metadata),
        )
        for index, message in enumerate(sample.transcript)
    ]


def _bundle_tool_calls(sample: EvalSample) -> list[ToolCall]:
    return [
        ToolCall(
            tool_call_id=f"tool_{index + 1}",
            tool_name=log.name,
            args=dict(log.args),
            result=log.result,
            duration_ms=log.duration_ms,
            success=log.success,
            turn_index=log.turn_index,
            metadata=dict(log.metadata),
        )
        for index, log in enumerate(sample.tool_logs)
    ]


def _workspace_snapshot(sample: EvalSample) -> WorkspaceSnapshot:
    return WorkspaceSnapshot(
        root="agent_files",
        files=[
            WorkspaceFile(
                path=artifact.path,
                sha256=artifact.sha256,
                size_bytes=artifact.size or 0,
                content_ref=f"sha256:{artifact.sha256}",
                metadata=dict(artifact.metadata),
            )
            for artifact in sample.files.values()
        ],
    )


def _workspace_content_store(sample: EvalSample) -> dict[str, dict[str, Any]]:
    store: dict[str, dict[str, Any]] = {}
    for artifact in sample.files.values():
        ref = f"sha256:{artifact.sha256}"
        store[ref] = {
            "path": artifact.path,
            "content": artifact.content,
            "encoding": artifact.metadata.get("encoding", "utf-8"),
            "sha256": artifact.sha256,
            "size_bytes": artifact.size or 0,
            "media_type": artifact.media_type,
        }
    return store


def _write_artifact_content(target: Path, content: Any, encoding: str) -> None:
    if encoding == "base64":
        target.write_bytes(base64.b64decode(str(content or "")))
    elif isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(str(content or ""), encoding="utf-8")


def _materialize_replay_workspace(
    imported_bundle: Bundle,
    workspace: Path,
) -> dict[str, FileArtifact]:
    workspace.mkdir(parents=True, exist_ok=True)
    store = imported_bundle.artifacts.get(WORKSPACE_CONTENTS_ARTIFACT_KEY)
    if not isinstance(store, dict):
        store = {}

    files: dict[str, FileArtifact] = {}
    for entry in imported_bundle.workspace.files:
        rel = Path(entry.path)
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError(f"Invalid replay artifact path: {entry.path}")
        raw = store.get(entry.content_ref)
        if not isinstance(raw, dict):
            raise ValueError(f"Missing replay content for {entry.path}")
        target = workspace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        encoding = str(raw.get("encoding") or entry.metadata.get("encoding") or "utf-8")
        _write_artifact_content(target, raw.get("content"), encoding)

        data = target.read_bytes()
        assert hashlib.sha256(data).hexdigest() == entry.sha256
        assert len(data) == entry.size_bytes
        files[entry.path] = FileArtifact(
            path=entry.path,
            content=raw.get("content"),
            sha256=entry.sha256,
            size=entry.size_bytes,
            media_type=str(raw.get("media_type") or ""),
            metadata={"encoding": encoding},
        )

    return files


def _transcript_from_bundle(imported_bundle: Bundle) -> tuple[TranscriptMessage, ...]:
    return tuple(
        TranscriptMessage(
            role=message.role,
            content=str(message.content or ""),
            metadata=dict(message.metadata),
        )
        for message in imported_bundle.messages
    )


def _tool_logs_from_bundle(imported_bundle: Bundle) -> tuple[ToolLog, ...]:
    return tuple(
        ToolLog(
            name=call.tool_name,
            args=call.args if isinstance(call.args, dict) else {},
            result=str(call.result or ""),
            success=True if call.success is None else call.success,
            duration_ms=float(call.duration_ms or 0.0),
            turn_index=int(call.turn_index or 0),
            metadata=dict(call.metadata),
        )
        for call in imported_bundle.tool_calls
    )


def _sample_from_bundle(
    *,
    imported_bundle: Bundle,
    item: EvalItem,
    bench_root: Path,
    workspace: Path,
) -> EvalSample:
    task = _task_from_item(item)
    layer = str(getattr(task.layer, "value", task.layer))
    files = _materialize_replay_workspace(imported_bundle, workspace)

    payload: dict[str, Any] = {"eval_model": "fake-model"}
    if layer == "L1":
        payload["workspace_path"] = str(workspace)
    elif layer == "L2":
        payload.update(
            {
                "persona": _load_persona(bench_root, task.persona_id),
                "eval_mode": "qr",
                "workspace_path": str(workspace),
                "distractor_names": [],
            }
        )

    return EvalSample(
        sample_id=imported_bundle.session_id,
        task_id=imported_bundle.task_id,
        transcript=_transcript_from_bundle(imported_bundle),
        tool_logs=_tool_logs_from_bundle(imported_bundle),
        files=files,
        payload=payload,
    )


def _contract_bundle(
    *,
    item: EvalItem,
    sample: EvalSample,
    score: Score,
) -> Bundle:
    task = _task_from_item(item)
    return Bundle(
        bundle_id=sample.sample_id,
        schema_version=SCHEMA_VERSION,
        task_id=item.task_id,
        timestamps=BundleTimestamps(created_at="2026-05-06T00:00:00Z"),
        agent_id="roundtrip-fixture",
        sandbox_digest={
            "image_uri": item.sandbox_spec.image_uri if item.sandbox_spec else "",
        },
        telemetry={"eval_model": sample.payload.get("eval_model")},
        messages=_bundle_messages(sample),
        tool_calls=_bundle_tool_calls(sample),
        artifacts={
            REFERENCE_ARTIFACT_KEY: {
                "persona_id": getattr(task, "persona_id", ""),
                "session_id": sample.sample_id,
                "termination_reason": "roundtrip_fixture",
                "task_version": item.version,
            },
            CONTRACT_ARTIFACT_KEY: {
                "eval_item": _eval_item_to_json(item),
                "eval_sample": _eval_sample_to_json(sample),
                "original_score": _score_to_json(score),
            },
            WORKSPACE_CONTENTS_ARTIFACT_KEY: _workspace_content_store(sample),
        },
        workspace=_workspace_snapshot(sample),
    )


def _load_fresh_reference_bundle(bench_root: Path):
    bundles = PluginLoader().load_config(REFERENCE_BUNDLE_CONFIG)
    assert len(bundles) == 1
    bundle = bundles[0]
    for component in (bundle.task_suite, bundle.npc_provider, bundle.evaluator):
        configure = getattr(component, "configure", None)
        if callable(configure):
            configure(bench_root=bench_root, eval_model="fake-model")
    return bundle


@pytest.mark.parametrize("task_id", ROUNDTRIP_TASKS)
def test_reference_bundle_roundtrip_re_evaluates_to_same_score(
    task_id: str,
    bench_root: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    _patch_l2_qr(monkeypatch)
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(task_id)
    sample = _sample_for_task(bench_root=bench_root, item=item, tmp_path=tmp_path)
    original_score = bundle.evaluator.evaluate(item, sample)

    bundle_json = bundle_io.to_json(
        _contract_bundle(item=item, sample=sample, score=original_score)
    )
    bundle_payload = json.loads(bundle_json)
    validate_bundle_dict(bundle_payload)

    imported_bundle = bundle_io.from_json(bundle_json)
    contract = imported_bundle.artifacts[CONTRACT_ARTIFACT_KEY]
    stored_score = _score_from_json(contract["original_score"])

    fresh_bundle = _load_fresh_reference_bundle(bench_root)
    reconstructed_item = fresh_bundle.task_suite.get_task(imported_bundle.task_id)
    reconstructed_sample = _sample_from_bundle(
        imported_bundle=imported_bundle,
        item=reconstructed_item,
        bench_root=bench_root,
        workspace=tmp_path / "replay" / task_id / "workspace",
    )
    reproduced_score = fresh_bundle.evaluator.evaluate(
        reconstructed_item,
        reconstructed_sample,
    )

    assert stored_score.value == pytest.approx(original_score.value)
    assert reproduced_score.value == pytest.approx(original_score.value)
    assert reproduced_score.status == original_score.status
    assert reproduced_score.metrics == original_score.metrics


def _impl_b_sample_for_task(
    *,
    item: EvalItem,
    tmp_path: Path,
) -> EvalSample:
    workspace = tmp_path / item.task_id / "workspace"
    _write_expected_outputs(workspace, list(item.payload["expected_outputs"]))
    return EvalSample(
        sample_id=f"{item.task_id}-roundtrip",
        task_id=item.task_id,
        transcript=(
            TranscriptMessage(role="user", content=str(item.payload["prompt"])),
            TranscriptMessage(role="assistant", content="Artifacts are ready."),
        ),
        files=_file_artifacts(workspace),
        payload={
            "eval_model": "fake-model",
            "workspace_path": str(workspace),
        },
    )


def _impl_b_contract_bundle(
    *,
    item: EvalItem,
    sample: EvalSample,
    score: Score,
) -> Bundle:
    return Bundle(
        bundle_id=sample.sample_id,
        schema_version=SCHEMA_VERSION,
        task_id=item.task_id,
        timestamps=BundleTimestamps(created_at="2026-05-06T00:00:00Z"),
        agent_id="impl-b-roundtrip-fixture",
        sandbox_digest={
            "image_uri": item.sandbox_spec.image_uri if item.sandbox_spec else "",
        },
        telemetry={"eval_model": sample.payload.get("eval_model")},
        messages=_bundle_messages(sample),
        tool_calls=_bundle_tool_calls(sample),
        artifacts={
            REFERENCE_ARTIFACT_KEY: {
                "plugin": "impl_b_programmatic",
                "session_id": sample.sample_id,
                "termination_reason": "roundtrip_fixture",
                "task_version": item.version,
            },
            CONTRACT_ARTIFACT_KEY: {
                "eval_item": _eval_item_to_json(item),
                "eval_sample": _eval_sample_to_json(sample),
                "original_score": _score_to_json(score),
            },
            WORKSPACE_CONTENTS_ARTIFACT_KEY: _workspace_content_store(sample),
        },
        workspace=_workspace_snapshot(sample),
    )


def test_impl_b_bundle_roundtrip_re_evaluates_to_same_score(
    bench_root: Path,
    tmp_path: Path,
):
    task_id = "IMPLB_JSON_01_summary"
    bundle = load_impl_b_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(task_id)
    sample = _impl_b_sample_for_task(item=item, tmp_path=tmp_path)
    original_score = bundle.evaluator.evaluate(item, sample)

    bundle_json = bundle_io.to_json(
        _impl_b_contract_bundle(item=item, sample=sample, score=original_score)
    )
    validate_bundle_dict(json.loads(bundle_json))

    imported_bundle = bundle_io.from_json(bundle_json)
    contract = imported_bundle.artifacts[CONTRACT_ARTIFACT_KEY]
    stored_score = _score_from_json(contract["original_score"])

    fresh_bundle = load_impl_b_bundle(bench_root=bench_root, eval_model="fake-model")
    reconstructed_item = fresh_bundle.task_suite.get_task(imported_bundle.task_id)
    replay_workspace = tmp_path / "replay" / task_id / "workspace"
    files = _materialize_replay_workspace(imported_bundle, replay_workspace)
    reconstructed_sample = EvalSample(
        sample_id=imported_bundle.session_id,
        task_id=imported_bundle.task_id,
        transcript=_transcript_from_bundle(imported_bundle),
        tool_logs=_tool_logs_from_bundle(imported_bundle),
        files=files,
        payload={
            "eval_model": "fake-model",
            "workspace_path": str(replay_workspace),
        },
    )
    reproduced_score = fresh_bundle.evaluator.evaluate(
        reconstructed_item,
        reconstructed_sample,
    )

    assert stored_score.value == pytest.approx(original_score.value)
    assert reproduced_score.value == pytest.approx(original_score.value)
    assert reproduced_score.status == original_score.status
    assert reproduced_score.metrics == original_score.metrics
