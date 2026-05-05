import json

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
from platform_api.runtime import DataMountResolver
from server.api.session_api import SessionState
from server.reference import load_reference_bundle


def _task_from_item(item: EvalItem) -> QuantTutorTask:
    return QuantTutorTask(**item.payload["quant_tutor_task"])


def _load_persona(bench_root, persona_id: str) -> UserPersona:
    path = bench_root / "personas" / f"{persona_id}.json"
    return UserPersona(**json.loads(path.read_text(encoding="utf-8")))


def test_reference_task_suite_bridges_quant_tutor_task(bench_root):
    data_dir = bench_root / "data" / "hf_cache" / "normal" / "BDEX"
    data_dir.mkdir(parents=True)
    (data_dir / "AAPL_2018_2024.csv").write_text("Date,Close\n", encoding="utf-8")
    (data_dir / "SPY_2018_2024.csv").write_text("Date,Close\n", encoding="utf-8")
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")

    item = bundle.task_suite.get_task("D01_load_inspect_ohlcv")

    assert item.task_type == "multi_turn"
    assert item.version == "2.2"
    assert item.payload["quant_tutor_task"]["task_id"] == "D01_load_inspect_ohlcv"
    assert {mount.target_path for mount in item.data_mounts} == {
        "/data/AAPL_2018_2024.csv",
        "/data/SPY_2018_2024.csv",
    }
    resolved = DataMountResolver().resolve_all(item.data_mounts)
    assert {mount.container_path for mount in resolved} == {
        "/data/AAPL_2018_2024.csv",
        "/data/SPY_2018_2024.csv",
    }
    assert item.sandbox_spec.image_uri == "quant-tutor-env:v2.2"


def test_reference_task_suite_emits_hf_uri_without_local_cache(bench_root):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")

    item = bundle.task_suite.get_task("D01_load_inspect_ohlcv")

    uris = {mount.target_path: mount.uri for mount in item.data_mounts}
    assert (
        uris["/data/SPY_2018_2024.csv"]
        == "hf://Varsity-Tech/quant-tutor-bench-data@"
        "793bca3f8dc70d379d423358f4159eca2d8be83f/BDS/SPY_2018_2024.csv"
        "?repo_type=dataset"
    )


def test_reference_npc_provider_propagates_task_end(bench_root):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task("D01_load_inspect_ohlcv")
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
    item = bundle.task_suite.get_task("D01_load_inspect_ohlcv")
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
        result = state.register("D01_load_inspect_ohlcv")
        assert result["session_id"] == "contract-only-session"
        assert state.user_sim is None
        started = state.start()
        assert started["user_message"] == "generic opening"
        sent = json.loads(state.handle_send_message("Finished."))
        assert sent["user_message"] == "generic reply"
        assert sent["status"] == "completed"
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

    result = state.register("D01_load_inspect_ohlcv")

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
                "task_id": "D01_load_inspect_ohlcv",
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


def test_reference_evaluator_scores_l0_and_l1(bench_root):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task("money.stackexchange_2")
    task = _task_from_item(item)
    sample = EvalSample(
        sample_id="sample-l1",
        task_id=item.task_id,
        transcript=(
            TranscriptMessage(role="user", content=task.description),
            TranscriptMessage(role="assistant", content=task.reference_answer or ""),
        ),
        payload={"eval_model": "fake-model"},
    )

    l1_score = bundle.evaluator.evaluate(item, sample)
    l0_score = bundle.evaluator.evaluate(
        EvalItem(
            task_id=item.task_id,
            task_type="knowledge_qa",
            payload=item.payload,
        ),
        sample,
    )

    assert l1_score.value == 1.0
    assert l1_score.metrics["layer1"]["status"] == "success"
    assert l0_score.value == 1.0
    assert l0_score.metrics["knowledge_qa"]["status"] == "success"


def test_reference_evaluator_allocates_direct_persisted_score(bench_root, tmp_path):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task("money.stackexchange_2")
    task = _task_from_item(item)
    result_dir = tmp_path / "result"

    score = bundle.evaluator.evaluate(
        item,
        EvalSample(
            sample_id="sample-direct",
            task_id=item.task_id,
            transcript=(
                TranscriptMessage(role="user", content=task.description),
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


def test_reference_evaluator_layer2_uses_coordinator(
    bench_root,
    tmp_path,
    monkeypatch,
):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task("D01_load_inspect_ohlcv")
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
    assert seen["task_id"] == "D01_load_inspect_ohlcv"
    assert score.value == 0.75
    assert score.metrics["summary"]["overall_score"] == 0.75


def test_reference_evaluator_layer2_direct_computes_full_overall(
    bench_root,
    monkeypatch,
):
    bundle = load_reference_bundle(bench_root=bench_root, eval_model="fake-model")
    item = bundle.task_suite.get_task("D01_load_inspect_ohlcv")
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
