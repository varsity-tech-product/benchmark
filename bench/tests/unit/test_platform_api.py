import json
import subprocess
from types import SimpleNamespace

import pytest

from platform_api import (
    DockerSandboxRuntime,
    EvalItem,
    EvalSample,
    Evaluator,
    EvaluatorMetadata,
    InMemoryTelemetryHook,
    LocalSandboxRuntime,
    NPCProvider,
    NPCReply,
    PluginBundle,
    PluginLoadError,
    PluginLoader,
    SandboxCreateRequest,
    SandboxMount,
    Score,
    TaskSuite,
    TelemetryRecord,
    TelemetryTimer,
    ToolRequest,
    ToolRouter,
    TranscriptMessage,
)


class StubTaskSuite(TaskSuite):
    def supported_tasks(self) -> set[str]:
        return {"T1"}

    def get_task(self, task_id: str) -> EvalItem:
        return EvalItem(task_id=task_id, payload={"student_opening": "hello"})

    def required_bundle_fields(self) -> set[str]:
        return {"conversation", "tool_logs"}


class StubNPCProvider(NPCProvider):
    def initial_message(self, task: EvalItem) -> str:
        return str(task.payload["student_opening"])

    def respond(self, transcript, tool_logs, files, payload) -> NPCReply:
        return NPCReply(message=f"turns={len(transcript)}", terminate=True)


class StubEvaluator(Evaluator):
    def evaluate(self, item: EvalItem, sample: EvalSample) -> Score:
        return Score(value=1.0, metrics={"sample_id": sample.sample_id})

    def metadata(self) -> EvaluatorMetadata:
        return EvaluatorMetadata(
            evaluator_id="stub",
            version="0",
            supported_tasks=frozenset({"T1"}),
            required_bundle_fields=frozenset({"conversation"}),
        )


def test_contract_abcs_and_models_round_trip():
    suite = StubTaskSuite()
    npc = StubNPCProvider()
    evaluator = StubEvaluator()

    item = suite.get_task("T1")
    sample = EvalSample(
        sample_id="S1",
        task_id="T1",
        transcript=(TranscriptMessage(role="user", content="hello"),),
    )

    assert suite.supported_tasks() == {"T1"}
    assert npc.initial_message(item) == "hello"
    assert npc.respond(sample.transcript, (), {}, {}).terminate is True
    assert evaluator.evaluate(item, sample).value == 1.0
    assert evaluator.metadata().required_bundle_fields == frozenset({"conversation"})


def test_plugin_loader_loads_json_config(tmp_path, monkeypatch):
    module_path = tmp_path / "stub_plugin.py"
    module_path.write_text(
        """
from platform_api.contracts import (
    EvalItem, EvalSample, Evaluator, EvaluatorMetadata, NPCProvider,
    NPCReply, Score, TaskSuite,
)

class Suite(TaskSuite):
    def supported_tasks(self):
        return {"T1"}
    def get_task(self, task_id):
        return EvalItem(task_id=task_id)
    def required_bundle_fields(self):
        return {"conversation"}

class NPC(NPCProvider):
    def initial_message(self, task):
        return "hello"
    def respond(self, transcript, tool_logs, files, payload):
        return NPCReply(message="done", terminate=True)

class Eval(Evaluator):
    def evaluate(self, item, sample):
        return Score(value=0.5)
    def metadata(self):
        return EvaluatorMetadata(evaluator_id="eval", version="1")
""",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    config_path = tmp_path / "plugins.json"
    config_path.write_text(
        json.dumps(
            {
                "plugins": [
                    {
                        "name": "stub",
                        "task_suite": "stub_plugin:Suite",
                        "npc_provider": "stub_plugin:NPC",
                        "evaluator": "stub_plugin:Eval",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    bundles = PluginLoader().load_config(config_path)

    assert len(bundles) == 1
    assert bundles[0].name == "stub"
    assert bundles[0].task_suite.supported_tasks() == {"T1"}
    assert bundles[0].npc_provider.initial_message(EvalItem("T1")) == "hello"


def test_plugin_loader_loads_entry_point_bundle(monkeypatch):
    bundle = PluginBundle(
        name="entry",
        task_suite=StubTaskSuite(),
        npc_provider=StubNPCProvider(),
        evaluator=StubEvaluator(),
    )

    class EntryPoints(list):
        def select(self, group):
            return self if group == "quantagentbench.plugins" else []

    fake_entry_point = SimpleNamespace(name="entry", load=lambda: (lambda: bundle))
    monkeypatch.setattr(
        "platform_api.plugins.loader.importlib.metadata.entry_points",
        lambda: EntryPoints([fake_entry_point]),
    )

    bundles = PluginLoader().load_entry_points()

    assert bundles == [bundle]


def test_plugin_loader_empty_entry_point_names_loads_none(monkeypatch):
    class EntryPoints(list):
        def select(self, group):
            return self if group == "quantagentbench.plugins" else []

    def fail_load():
        pytest.fail("empty entry-point names should skip loading")

    fake_entry_point = SimpleNamespace(name="entry", load=fail_load)
    monkeypatch.setattr(
        "platform_api.plugins.loader.importlib.metadata.entry_points",
        lambda: EntryPoints([fake_entry_point]),
    )

    assert PluginLoader().load_entry_points(names=set()) == []


def test_plugin_loader_rejects_incomplete_spec():
    with pytest.raises(PluginLoadError):
        PluginLoader().load_spec(
            {
                "name": "bad",
                "task_suite": object(),
                "npc_provider": StubNPCProvider(),
                "evaluator": StubEvaluator(),
            }
        )


def test_local_sandbox_routes_tools_and_emits_telemetry():
    telemetry = InMemoryTelemetryHook()
    router = ToolRouter()
    router.register("echo", lambda handle, args: {"success": True, "output": args["x"]})
    runtime = LocalSandboxRuntime(telemetry=telemetry, router=router)

    handle = runtime.create(SandboxCreateRequest(image_uri="local:test"))
    exec_result = runtime.exec(handle, "printf ok")
    tool_result = runtime.call_tool(handle, ToolRequest(name="echo", args={"x": "hi"}))
    runtime.destroy(handle)

    assert exec_result.stdout == "ok"
    assert tool_result.output == "hi"
    assert [record.event for record in telemetry.records] == [
        "create",
        "exec",
        "tool",
        "destroy",
    ]
    assert telemetry.totals()["errors"] == 0


def test_local_sandbox_timeout_output_is_text():
    runtime = LocalSandboxRuntime()
    handle = runtime.create(SandboxCreateRequest(image_uri="local:test"))

    result = runtime.exec(handle, "printf hi; sleep 1", timeout=0.05)
    runtime.destroy(handle)

    assert result.timed_out is True
    assert result.stdout == "hi"
    assert isinstance(result.stdout, str)
    assert isinstance(result.stderr, str)


def test_local_sandbox_exec_uses_request_env_and_counts_failed_records():
    telemetry = InMemoryTelemetryHook()
    runtime = LocalSandboxRuntime(telemetry=telemetry)
    handle = runtime.create(
        SandboxCreateRequest(
            image_uri="local:test",
            env={"QAB_PLATFORM_TEST_VALUE": "available"},
        )
    )

    env_result = runtime.exec(handle, "printf $QAB_PLATFORM_TEST_VALUE")
    failed_result = runtime.exec(handle, ["sh", "-c", "exit 7"])
    runtime.destroy(handle)

    assert env_result.stdout == "available"
    assert failed_result.exit_code == 7
    assert telemetry.records[-2].success is False
    assert telemetry.totals()["errors"] == 1


def test_docker_runtime_builds_isolated_run_command():
    calls = []

    def runner(args, timeout=None):
        calls.append(list(args))
        if args[:3] == ["docker", "run", "-d"]:
            return subprocess.CompletedProcess(
                args,
                0,
                stdout="container123\n",
                stderr="",
            )
        return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

    runtime = DockerSandboxRuntime(runner=runner)
    handle = runtime.create(
        SandboxCreateRequest(
            image_uri="example/image:v1",
            sandbox_id="case1",
            mounts=(
                SandboxMount(
                    host_path="/tmp/data",
                    container_path="/data",
                    read_only=True,
                ),
            ),
            env={"A": "B"},
            network_enabled=False,
            cpus="2",
            memory="1g",
        )
    )
    runtime.destroy(handle)

    run_args = calls[0]
    assert run_args[:4] == ["docker", "run", "-d", "--name"]
    assert "--network" in run_args
    assert run_args[run_args.index("--network") + 1] == "none"
    assert run_args[run_args.index("--cpus") + 1] == "2"
    assert run_args[run_args.index("--memory") + 1] == "1g"
    assert "/tmp/data:/data:ro" in run_args
    assert "A=B" in run_args
    assert handle.container_id == "container123"
    assert calls[-1] == ["docker", "rm", "-f", "container123"]


def test_telemetry_timer_records_errors_and_token_counts():
    hook = InMemoryTelemetryHook()
    hook.emit(
        TelemetryRecord(
            namespace="llm",
            event="call",
            input_tokens=3,
            output_tokens=5,
            cost_usd=0.01,
        )
    )

    with pytest.raises(ValueError):
        with TelemetryTimer(hook, "sandbox", "exec"):
            raise ValueError("boom")

    totals = hook.totals()
    assert totals["input_tokens"] == 3
    assert totals["output_tokens"] == 5
    assert totals["cost_usd"] == 0.01
    assert totals["errors"] == 1
    assert hook.records[-1].success is False
