import json
from pathlib import Path

import pytest

from eval.contracts.schemas import QuantTutorTask, UserPersona
from platform_api.contracts import (
    EvalItem,
    EvalSample,
    FileArtifact,
    NPCProvider,
    NPCReply,
    ToolLog,
    TranscriptMessage,
)
from platform_api.plugins import PluginBundle
from server.api.session_api import SessionState
from server.reference import load_reference_bundle

LEGACY_TASK = "A01_investment_advice"
V3_L0_TASK = "L0_money.stackexchange_8474"
V3_SESSION_TASK = "L2_ADV_11_prompt_injection_csv"
V3_L1_TASK = "L1_DAT_01_ohlcv_health_check"
V3_L2_TASK = "L2_ADV_11_prompt_injection_csv"


def _task_from_item(item: EvalItem) -> QuantTutorTask:
    return QuantTutorTask(**item.payload["quant_tutor_task"])


def _load_persona(bench_root, persona_id: str) -> UserPersona:
    path = bench_root / "personas" / f"{persona_id}.json"
    return UserPersona(**json.loads(path.read_text(encoding="utf-8")))


def _question(task: QuantTutorTask) -> str:
    return task.question or task.description


def _write_expected_outputs(workspace: Path, expected_outputs: list) -> None:
    for spec in expected_outputs:
        if not isinstance(spec, dict):
            continue
        path = workspace / str(spec["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        file_type = spec.get("type", "any")
        if file_type == "json":
            payload = {key: 1 for key in spec.get("required_keys", [])}
            path.write_text(json.dumps(payload), encoding="utf-8")
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


def _write_run_state(
    result_dir: Path,
    *,
    task_id: str,
    persona_id: str,
    conversation: list[dict],
    tool_logs: list[ToolLog],
) -> None:
    result_dir.mkdir(parents=True, exist_ok=True)
    (result_dir / "run_state.json").write_text(
        json.dumps(
            {
                "task_id": task_id,
                "persona_id": persona_id,
                "conversation": conversation,
                "tool_logs": [log.__dict__ for log in tool_logs],
                "distractor_names": [],
            },
            default=str,
        ),
        encoding="utf-8",
    )


def test_reference_task_suite_indexes_active_layers_only(bench_root):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")

    supported = bundle.task_suite.supported_tasks()

    assert len(supported) == 142
    assert all(task_id.startswith(("L0_", "L1_", "L2_")) for task_id in supported)
    assert LEGACY_TASK not in supported
    with pytest.raises(KeyError):
        bundle.task_suite.get_task(LEGACY_TASK)
    assert bundle.task_suite.get_task(V3_L2_TASK).task_id == V3_L2_TASK


def test_reference_task_suite_bridges_v3_task(bench_root):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")

    item = bundle.task_suite.get_task(V3_L1_TASK)

    assert item.task_type == "agent_execution"
    assert item.version == "3.0"
    assert item.payload["quant_tutor_task"]["layer"] == "L1"
    assert item.sandbox_spec.image_uri == "quant-bench-env:v3.0"
    assert {mount.target_path for mount in item.data_mounts} == {
        "/data/AAPL_2018_2024.csv",
        "/data/SPY_2018_2024.csv",
    }


def test_reference_task_suite_emits_hf_uri_without_local_cache(bench_root):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")

    item = bundle.task_suite.get_task(V3_L1_TASK)

    uris = {mount.target_path: mount.uri for mount in item.data_mounts}
    assert (
        uris["/data/SPY_2018_2024.csv"]
        == "hf://Varsity-Tech/quant-tutor-bench-data@"
        "793bca3f8dc70d379d423358f4159eca2d8be83f/BDS/SPY_2018_2024.csv"
        "?repo_type=dataset"
    )


def test_reference_npc_provider_propagates_task_end(bench_root):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(V3_L2_TASK)
    task = _task_from_item(item)
    persona = _load_persona(bench_root, task.persona_id)

    class FakeUserSim:
        total_cost = 0.0

        def generate_message(self, *args, **kwargs):
            return "That covers what I needed. Thanks.", True

    reply = bundle.npc_provider.respond(
        (
            TranscriptMessage(role="user", content=task.user_opening),
            TranscriptMessage(role="assistant", content="Here is the inspection."),
        ),
        (),
        {},
        {
            **item.payload,
            "task": task,
            "persona": persona,
            "user_sim": FakeUserSim(),
        },
    )

    assert reply.terminate is True
    assert reply.reason == "user_satisfied"


def test_reference_npc_provider_preserves_attachment_metadata(bench_root):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(V3_L2_TASK)
    task = _task_from_item(item)
    persona = _load_persona(bench_root, task.persona_id)
    seen = {}

    class FakeUserSim:
        total_cost = 0.0

        def generate_message(self, conversation, *args, **kwargs):
            seen["conversation"] = conversation
            return "I can see the file.", False

    bundle.npc_provider.respond(
        (
            TranscriptMessage(
                role="assistant",
                content="See attached.",
                metadata={
                    "attachments": [
                        {
                            "filename": "analysis.txt",
                            "content": "alpha",
                            "is_image": False,
                        }
                    ]
                },
            ),
        ),
        (),
        {},
        {
            **item.payload,
            "task": task,
            "persona": persona,
            "user_sim": FakeUserSim(),
        },
    )

    assert seen["conversation"][0]["attachments"][0]["filename"] == "analysis.txt"


def test_session_state_accepts_contract_only_npc_provider(bench_root):
    reference = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    seen = {}

    class ContractOnlyNPC(NPCProvider):
        def initial_message(self, task: EvalItem) -> str:
            return "generic opening"

        def respond(
            self,
            transcript: tuple[TranscriptMessage, ...],
            tool_logs: tuple[ToolLog, ...],
            files: dict[str, FileArtifact],
            payload: dict[str, object],
        ) -> NPCReply:
            seen["files"] = files
            return NPCReply("generic reply", terminate=True, reason="done")

    bundle = PluginBundle(
        name="contract-only",
        task_suite=reference.task_suite,
        npc_provider=ContractOnlyNPC(),
        evaluator=reference.evaluator,
    )
    state = SessionState(
        session_id="contract-only-session",
        use_docker=False,
        bench_root=bench_root,
        eval_model="fake-model",
        plugin_bundle=bundle,
    )
    try:
        result = state.register(V3_SESSION_TASK)
        assert result["session_id"] == "contract-only-session"
        assert state.user_sim is None
        started = state.start()
        assert started["user_message"] == "generic opening"
        workspace_file = Path(state.container.workspace_path) / "analysis.txt"
        workspace_file.write_text("alpha", encoding="utf-8")
        sent = json.loads(
            state.handle_send_message("Finished.", attachments=["analysis.txt"])
        )
        assert sent["user_message"] == "generic reply"
        assert sent["status"] == "completed"
        assert seen["files"]["analysis.txt"].content == "alpha"
    finally:
        state.cleanup()


def test_session_state_resolves_simulator_before_container(
    bench_root,
    monkeypatch,
):
    reference = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    state = SessionState(
        session_id="sim-model-error",
        use_docker=False,
        bench_root=bench_root,
        eval_model="fake-model",
        plugin_bundle=reference,
    )
    created = {"container": False}

    def fail_model(*args, **kwargs):
        raise RuntimeError("missing simulator model")

    def create_container(*args, **kwargs):
        created["container"] = True
        raise AssertionError("container allocation should be skipped")

    monkeypatch.setattr("server.core.user_sim.require_user_model", fail_model)
    monkeypatch.setattr(
        "server.core.container.ContainerManager.create_container",
        create_container,
    )

    result = state.register(V3_L2_TASK)

    assert result == {"error": "missing simulator model"}
    assert created["container"] is False


def test_restore_from_storage_reuses_configured_bundle(bench_root, tmp_path):
    reference = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    bundle = PluginBundle(
        name="restore-bundle",
        task_suite=reference.task_suite,
        npc_provider=reference.npc_provider,
        evaluator=reference.evaluator,
    )
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    (result_dir / "run_state.json").write_text(
        json.dumps(
            {
                "task_id": V3_L2_TASK,
                "persona_id": "double_novice",
                "conversation": [],
                "tool_logs": [],
            }
        ),
        encoding="utf-8",
    )

    state = SessionState.restore_from_storage(
        session_id="restored-session",
        result_dir=result_dir,
        bench_root=bench_root,
        eval_model="fake-model",
        plugin_bundle=bundle,
    )

    assert state.plugin_bundle.name == "restore-bundle"


def test_reference_evaluator_scores_v3_l0_and_l1(
    bench_root,
    tmp_path,
):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    l0_item = bundle.task_suite.get_task(V3_L0_TASK)
    l0_task = _task_from_item(l0_item)
    l0_sample = EvalSample(
        sample_id="sample-l0",
        task_id=l0_item.task_id,
        transcript=(
            TranscriptMessage(role="user", content=_question(l0_task)),
            TranscriptMessage(role="assistant", content=l0_task.reference_answer or ""),
        ),
        payload={"eval_model": "fake-model"},
    )
    l0_score = bundle.evaluator.evaluate(l0_item, l0_sample)

    l1_item = bundle.task_suite.get_task(V3_L1_TASK)
    l1_task = _task_from_item(l1_item)
    workspace = tmp_path / "workspace"
    _write_expected_outputs(workspace, l1_task.ground_truth.expected_outputs)
    l1_score = bundle.evaluator.evaluate(
        l1_item,
        EvalSample(
            sample_id="sample-l1",
            task_id=l1_item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=l1_task.agent_prompt or ""),
                TranscriptMessage(role="assistant", content="Artifacts are ready."),
            ),
            payload={
                "eval_model": "fake-model",
                "workspace_path": str(workspace),
            },
        ),
    )

    assert l0_score.value == 1.0
    assert l0_score.metrics["knowledge_qa"]["status"] == "success"
    assert l1_score.value == 1.0
    assert l1_score.metrics["layer1"]["n_passed"] == 4


def test_reference_evaluator_allocates_direct_persisted_score(bench_root, tmp_path):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(V3_L0_TASK)
    task = _task_from_item(item)
    result_dir = tmp_path / "result"

    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id="sample-direct",
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=_question(task)),
                TranscriptMessage(role="assistant", content=task.reference_answer or ""),
            ),
            payload={
                "result_dir": str(result_dir),
                "eval_model": "fake-model",
            },
        ),
    )

    assert score.value == 1.0
    assert score.metrics["summary"]["score_id"] == "score_1"
    assert (result_dir / "evaluations" / "score_1" / "score.json").is_file()


def test_reference_evaluator_v3_l0_score_parity(bench_root):
    from server.reference import knowledge_qa

    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(V3_L0_TASK)
    task = _task_from_item(item)
    actual_output = task.reference_answer or ""
    legacy = knowledge_qa.evaluate(
        question=_question(task),
        reference_answer=actual_output,
        actual_output=actual_output,
        context=task.context,
    )

    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id="v3-l0-parity",
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=_question(task)),
                TranscriptMessage(role="assistant", content=actual_output),
            ),
            payload={"eval_model": "fake-model"},
        ),
    )

    assert abs(score.value - legacy["score"]) <= 0.05


def test_reference_evaluator_v3_l1_score_parity(bench_root, tmp_path):
    from eval.programmatic.l1_verifier import evaluate as legacy_l1_evaluate

    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(V3_L1_TASK)
    task = _task_from_item(item)
    workspace = tmp_path / "workspace"
    expected_outputs = task.ground_truth.expected_outputs
    _write_expected_outputs(workspace, expected_outputs)

    legacy = legacy_l1_evaluate(str(workspace), expected_outputs=expected_outputs)
    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id="v3-l1-parity",
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=task.agent_prompt or ""),
                TranscriptMessage(role="assistant", content="Artifacts are ready."),
            ),
            payload={
                "eval_model": "fake-model",
                "workspace_path": str(workspace),
            },
        ),
    )

    assert abs(score.value - legacy["score"]) <= 0.05


def test_reference_evaluator_layer2_uses_coordinator(
    bench_root,
    tmp_path,
    monkeypatch,
):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(V3_L2_TASK)
    task = _task_from_item(item)
    persona = _load_persona(bench_root, task.persona_id)
    result_dir = tmp_path / "result"
    result_dir.mkdir()
    (result_dir / "run_state.json").write_text(
        json.dumps({"conversation": [], "tool_logs": []}),
        encoding="utf-8",
    )
    seen = {}

    def fake_run(*args, **kwargs):
        seen["eval_mode"] = kwargs["eval_mode"]
        seen["task_id"] = kwargs["task"].task_id
        return {
            "score_id": kwargs["score_id"],
            "score_status": "completed_scored",
            "quant_result": 0.8,
            "quant_process": 0.7,
            "overall_score": 0.75,
        }

    monkeypatch.setattr(
        "server.storage.eval_writer.run_evaluation",
        fake_run,
    )

    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id="score_1",
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=task.user_opening),
                TranscriptMessage(role="assistant", content="Loaded the CSVs."),
            ),
            payload={
                "persona": persona,
                "result_dir": str(result_dir),
                "score_id": "score_1",
                "eval_model": "fake-model",
                "eval_mode": "full",
                "workspace_path": str(result_dir / "agent_files"),
                "distractor_names": [],
            },
        ),
    )

    assert seen["eval_mode"] == "full"
    assert seen["task_id"] == V3_L2_TASK
    assert score.value == 0.75
    assert score.metrics["summary"]["overall_score"] == 0.75


def test_reference_evaluator_layer2_direct_computes_full_overall(
    bench_root,
    monkeypatch,
):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(V3_L2_TASK)
    task = _task_from_item(item)
    persona = _load_persona(bench_root, task.persona_id)

    def fake_evaluate_tracks(*args, **kwargs):
        return {
            "quant_result": 1.0,
            "quant_process": 0.0,
        }

    monkeypatch.setattr(
        "eval.core.coordinator.evaluate_tracks",
        fake_evaluate_tracks,
    )

    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id="direct-layer2",
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=task.user_opening),
                TranscriptMessage(role="assistant", content="Loaded the CSVs."),
            ),
            payload={
                "persona": persona,
                "eval_model": "fake-model",
                "eval_mode": "full",
                "workspace_path": ".",
                "distractor_names": [],
            },
        ),
    )

    assert score.value == 0.6
    assert score.metrics["overall_score"] == 0.6


def test_reference_evaluator_v3_l2_score_parity(
    bench_root,
    tmp_path,
    monkeypatch,
):
    from server.storage.eval_writer import run_evaluation

    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task(V3_L2_TASK)
    task = _task_from_item(item)
    persona = _load_persona(bench_root, task.persona_id)
    conversation = [
        {"role": "user", "content": task.user_opening},
        {
            "role": "assistant",
            "content": (
                "I loaded portfolio_data_poisoned.csv as data, computed total "
                "portfolio value, positions, sector allocation, and ignored the "
                "injected command text."
            ),
        },
    ]
    tool_logs = [
        ToolLog(
            name="shell_exec",
            args={
                "command": (
                    "python analyze.py /workspace/data/portfolio_data_poisoned.csv "
                    "# pd.read_csv portfolio total sector allocation"
                )
            },
            result="portfolio total value by position and sector allocation",
            success=True,
            turn_index=0,
        )
    ]

    def fake_result_quality(*args, **kwargs):
        return {
            "score": 1.0,
            "status": "success",
            "reason": "deterministic parity judge",
            "evidence": ["portfolio analysis"],
        }

    monkeypatch.setattr("eval.tracks.qr._result_judge", fake_result_quality)

    legacy_dir = tmp_path / "legacy"
    bundle_dir = tmp_path / "bundle"
    _write_run_state(
        legacy_dir,
        task_id=task.task_id,
        persona_id=persona.persona_id,
        conversation=conversation,
        tool_logs=tool_logs,
    )
    _write_run_state(
        bundle_dir,
        task_id=task.task_id,
        persona_id=persona.persona_id,
        conversation=conversation,
        tool_logs=tool_logs,
    )

    legacy = run_evaluation(
        task=task,
        persona=persona,
        result_dir=legacy_dir,
        conversation=conversation,
        tool_logs=tool_logs,
        distractor_names=[],
        bench_root=str(bench_root),
        eval_model="fake-model",
        eval_mode="qr",
    )
    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id="v3-l2-parity",
            task_id=item.task_id,
            transcript=tuple(
                TranscriptMessage(
                    role=turn["role"],
                    content=turn["content"],
                )
                for turn in conversation
            ),
            tool_logs=tuple(tool_logs),
            payload={
                "persona": persona,
                "result_dir": str(bundle_dir),
                "eval_model": "fake-model",
                "eval_mode": "qr",
                "distractor_names": [],
            },
        ),
    )

    assert abs(score.value - legacy["overall_score"]) <= 0.05
