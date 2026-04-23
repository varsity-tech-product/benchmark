import asyncio
import json
import sys
import types
import unittest
import warnings
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

sys.modules.setdefault(
    "dotenv",
    types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None),
)

from config.model_resolver import resolve_deepeval_model
from config.pricing import estimate_cost
from config.prompt_config import build_scenario, build_tutor_context
from orchestrator.agent_adapters.claude_code_adapter import ClaudeCodeAdapter
from orchestrator.agent_adapters.openai_adapter import OpenAIAgentAdapter
from orchestrator.deepeval_models import ClaudeCodeModel
from orchestrator.schemas import (
    Difficulty,
    EnvironmentConfig,
    GroundTruth,
    QuantTutorTask,
    StudentPersona,
    TaskCategory,
)
from pydantic import BaseModel
from run_benchmark import _collect_remote_endpoints


def _make_persona() -> StudentPersona:
    return StudentPersona(
        persona_id="developer_crossover",
        knowledge_level="proficient_code",
        description="Comfortable with Python and basic quant workflows.",
    )


def _make_task(*, requires_code: bool) -> QuantTutorTask:
    return QuantTutorTask(
        task_id="I10_parameter_optimization",
        difficulty=Difficulty.HARD,
        category=TaskCategory.IMPLEMENTATION,
        description="Implement and verify a parameter optimization workflow.",
        persona_ids=["developer_crossover"],
        student_openings={
            "developer_crossover": "How should I implement the optimization workflow?"
        },
        environment=EnvironmentConfig(
            data_files=["universe.json"],
            core_mcp_tools=[
                "shell_exec",
                "file_write",
                "file_read",
                "get_environment_info",
            ],
            docs_available=["algorithm_framework_guide.md"],
        ),
        ground_truth=GroundTruth(
            termination_criteria="Produce code, artifacts, and ranked optimization results.",
            required_capabilities=[
                "Use GetParameter() to parameterize algorithm configuration",
                "Collect and rank optimization results",
            ],
            expected_mcp_tools=["shell_exec", "get_environment_info"],
        ),
        requires_code=requires_code,
    )


class PricingTests(unittest.TestCase):
    def test_gpt_4o_mini_aliases_share_pricing(self):
        bare = estimate_cost("gpt-4o-mini", 1_000_000, 1_000_000)
        prefixed = estimate_cost("openai/gpt-4o-mini", 1_000_000, 1_000_000)
        self.assertGreater(bare, 0.0)
        self.assertEqual(bare, prefixed)

    def test_unknown_model_still_warns(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            cost = estimate_cost("unknown-model", 10, 10)
        self.assertEqual(cost, 0.0)
        self.assertTrue(
            any("No pricing for model" in str(item.message) for item in caught)
        )


class NetworkPreflightTests(unittest.TestCase):
    def test_collect_remote_endpoints_uses_openai_when_no_openrouter_key(self):
        args = SimpleNamespace(
            command="run-single",
            agent="openai",
            eval_model="gpt-4o-mini",
            simulator_model="gpt-4o-mini",
        )
        with patch.dict("os.environ", {}, clear=False):
            endpoints = _collect_remote_endpoints(args)
        hosts = {host for _, host in endpoints}
        self.assertIn("api.openai.com", hosts)
        self.assertNotIn("openrouter.ai", hosts)

    def test_collect_remote_endpoints_uses_openrouter_when_key_present(self):
        args = SimpleNamespace(
            command="run-single",
            agent="openai",
            eval_model="gpt-4o-mini",
            simulator_model="gpt-4o-mini",
        )
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test"}, clear=False):
            endpoints = _collect_remote_endpoints(args)
        hosts = {host for _, host in endpoints}
        self.assertIn("openrouter.ai", hosts)


class ClaudeCodeModelTests(unittest.TestCase):
    def test_resolve_deepeval_model_uses_claude_code_wrapper_explicitly(self):
        with patch.dict("os.environ", {"OPENROUTER_API_KEY": "test"}, clear=False):
            model = resolve_deepeval_model("claude-code/sonnet")

        self.assertIsInstance(model, ClaudeCodeModel)
        self.assertEqual(model.cli_model, "sonnet")
        self.assertEqual(model.get_model_name(), "claude-code/sonnet")

    def test_claude_code_model_parses_json_output_and_schema(self):
        class ScoreSchema(BaseModel):
            score: int
            reason: str

        payload = json.dumps(
            {
                "is_error": False,
                "result": json.dumps({"score": 9, "reason": "solid"}),
                "total_cost_usd": 0.125,
            }
        )

        model = ClaudeCodeModel(model="claude-code/sonnet")
        result, cost = model._parse_output(payload)
        parsed = model._coerce_schema_result(result, ScoreSchema)

        self.assertEqual(parsed.score, 9)
        self.assertEqual(parsed.reason, "solid")
        self.assertEqual(cost, 0.125)

    def test_claude_code_model_uses_structured_output_payload_when_present(self):
        payload = json.dumps(
            {
                "is_error": False,
                "result": "",
                "structured_output": {"answer": "OK"},
                "total_cost_usd": 0.5,
            }
        )

        model = ClaudeCodeModel(model="claude-code/sonnet")
        result, cost = model._parse_output(payload)

        self.assertEqual(result, {"answer": "OK"})
        self.assertEqual(cost, 0.5)

    def test_claude_code_model_async_schema_returns_schema_instance(self):
        class ScoreSchema(BaseModel):
            score: int
            reason: str

        model = ClaudeCodeModel(model="claude-code/sonnet")
        expected = ScoreSchema(score=7, reason="kept as object")

        with patch.object(
            model,
            "_run_async",
            AsyncMock(return_value=(expected, 0.25)),
        ):
            result = asyncio.run(model.a_generate("prompt", schema=ScoreSchema))

        self.assertIsInstance(result, ScoreSchema)
        self.assertEqual(result.score, 7)


class PromptRegressionTests(unittest.TestCase):
    def test_code_tasks_require_artifacts_in_tutor_context(self):
        context = build_tutor_context(_make_task(requires_code=True), _make_persona())
        self.assertIn("CODE TASK EXECUTION REQUIREMENT", context)
        self.assertIn("usable workspace artifacts", context)

    def test_non_code_tasks_do_not_get_code_task_execution_directive(self):
        context = build_tutor_context(_make_task(requires_code=False), _make_persona())
        self.assertNotIn("CODE TASK EXECUTION REQUIREMENT", context)
        self.assertNotIn("usable workspace artifacts", context)

    def test_code_tasks_push_student_back_to_implementation(self):
        scenario = build_scenario(_make_task(requires_code=True), "developer_crossover")
        self.assertIn("IMPLEMENTATION TRACKING", scenario)
        self.assertIn("WHEN THE TUTOR STAYS ABSTRACT", scenario)


class ClaudeCodeAdapterTests(unittest.TestCase):
    def test_build_command_uses_resume_for_existing_persistent_session(self):
        adapter = ClaudeCodeAdapter(model="haiku")
        adapter._session_id = "session-123"

        cmd = adapter._build_command(
            effective_prompt="system",
            mcp_config_path=None,
            use_persistent_session=True,
        )

        self.assertIn("--resume", cmd)
        self.assertNotIn("--no-session-persistence", cmd)
        self.assertNotIn("--system-prompt", cmd)

    def test_build_command_keeps_system_prompt_for_first_persistent_turn(self):
        adapter = ClaudeCodeAdapter(model="haiku")

        cmd = adapter._build_command(
            effective_prompt="system",
            mcp_config_path=None,
            use_persistent_session=True,
        )

        self.assertNotIn("--resume", cmd)
        self.assertIn("--system-prompt", cmd)
        self.assertNotIn("--no-session-persistence", cmd)

    def test_build_command_uses_stateless_mode_when_persistence_disabled(self):
        adapter = ClaudeCodeAdapter(model="haiku")

        cmd = adapter._build_command(
            effective_prompt="system",
            mcp_config_path=None,
            use_persistent_session=False,
        )

        self.assertIn("--no-session-persistence", cmd)
        self.assertIn("--system-prompt", cmd)

    def test_build_prompt_uses_only_latest_message_for_incremental_turns(self):
        adapter = ClaudeCodeAdapter(model="haiku")
        prompt = adapter._build_prompt(
            [
                {"role": "user", "content": "first"},
                {"role": "assistant", "content": "reply"},
                {"role": "user", "content": "latest"},
            ],
            incremental=True,
        )
        self.assertEqual(prompt, "latest")

    def test_build_env_uses_oauth_override_only_for_stateless_mode(self):
        adapter = ClaudeCodeAdapter(model="haiku")
        with patch.object(adapter, "_ensure_oauth_creds", return_value="/tmp/creds"):
            with patch.dict(
                "os.environ",
                {
                    "CLAUDE_CODE_OAUTH_TOKEN": "token",
                    "CLAUDE_CODE_OAUTH_REFRESH_TOKEN": "refresh",
                },
                clear=False,
            ):
                stateless_env = adapter._build_env(False)
                persistent_env = adapter._build_env(True)

        self.assertEqual(stateless_env["CLAUDE_CONFIG_DIR"], "/tmp/creds")
        self.assertNotIn("CLAUDE_CONFIG_DIR", persistent_env)

    def test_parse_output_records_session_id(self):
        adapter = ClaudeCodeAdapter(model="haiku")
        response = adapter._parse_output(
            json.dumps(
                {
                    "is_error": False,
                    "result": "OK",
                    "session_id": "session-abc",
                    "modelUsage": {},
                }
            )
        )
        self.assertEqual(response, "OK")
        self.assertEqual(adapter._session_id, "session-abc")

    def test_detect_local_auth_reads_claude_auth_status_json(self):
        adapter = ClaudeCodeAdapter(model="haiku")
        with patch(
            "orchestrator.agent_adapters.claude_code_adapter.subprocess.run"
        ) as mock_run:
            mock_run.return_value = SimpleNamespace(
                returncode=0,
                stdout=json.dumps({"loggedIn": True}),
            )
            self.assertTrue(adapter._detect_local_auth())


class OpenAIAdapterCleanupTests(unittest.TestCase):
    def test_close_is_safe_without_a_client(self):
        adapter = OpenAIAgentAdapter(model="gpt-4o-mini", api_key="test-key")
        adapter.close()

    def test_close_awaits_owned_async_client(self):
        adapter = OpenAIAgentAdapter(model="gpt-4o-mini", api_key="test-key")

        class FakeAsyncClient:
            def __init__(self):
                self.closed = False

            async def close(self):
                self.closed = True

        fake_client = FakeAsyncClient()
        adapter._async_client = fake_client
        adapter.close()

        self.assertTrue(fake_client.closed)
        self.assertIsNone(adapter._async_client)

    def test_generate_response_disables_sdk_tracing_for_custom_provider(self):
        adapter = OpenAIAgentAdapter(
            model="gpt-4o-mini",
            api_key="test-key",
            base_url="https://openrouter.ai/api/v1",
        )
        adapter._agent = object()

        fake_result = types.SimpleNamespace(
            final_output="ok",
            raw_responses=[],
            to_input_list=lambda: [{"role": "assistant", "content": "ok"}],
        )
        fake_runner = types.SimpleNamespace(
            run_sync=MagicMock(return_value=fake_result)
        )

        class FakeRunConfig:
            def __init__(
                self, tracing_disabled=False, trace_include_sensitive_data=True
            ):
                self.tracing_disabled = tracing_disabled
                self.trace_include_sensitive_data = trace_include_sensitive_data

        with (
            patch(
                "orchestrator.agent_adapters.openai_adapter.OPENAI_AGENTS_AVAILABLE",
                True,
            ),
            patch(
                "orchestrator.agent_adapters.openai_adapter.Runner",
                fake_runner,
                create=True,
            ),
            patch(
                "orchestrator.agent_adapters.openai_adapter.RunConfig",
                FakeRunConfig,
                create=True,
            ),
            patch(
                "orchestrator.agent_adapters.openai_adapter.MaxTurnsExceeded",
                RuntimeError,
                create=True,
            ),
        ):
            response = adapter.generate_response(
                messages=[{"role": "user", "content": "hello"}],
                available_tools=[],
                tool_callback=None,
            )

        self.assertEqual(response, "ok")
        run_config = fake_runner.run_sync.call_args.kwargs["run_config"]
        self.assertTrue(run_config.tracing_disabled)
        self.assertFalse(run_config.trace_include_sensitive_data)

    def test_generate_response_keeps_tracing_for_native_openai(self):
        adapter = OpenAIAgentAdapter(model="gpt-4o-mini", api_key="test-key")
        adapter._agent = object()

        fake_result = types.SimpleNamespace(
            final_output="ok",
            raw_responses=[],
            to_input_list=lambda: [{"role": "assistant", "content": "ok"}],
        )
        fake_runner = types.SimpleNamespace(
            run_sync=MagicMock(return_value=fake_result)
        )

        class FakeRunConfig:
            def __init__(
                self, tracing_disabled=False, trace_include_sensitive_data=True
            ):
                self.tracing_disabled = tracing_disabled
                self.trace_include_sensitive_data = trace_include_sensitive_data

        with (
            patch(
                "orchestrator.agent_adapters.openai_adapter.OPENAI_AGENTS_AVAILABLE",
                True,
            ),
            patch(
                "orchestrator.agent_adapters.openai_adapter.Runner",
                fake_runner,
                create=True,
            ),
            patch(
                "orchestrator.agent_adapters.openai_adapter.RunConfig",
                FakeRunConfig,
                create=True,
            ),
            patch(
                "orchestrator.agent_adapters.openai_adapter.MaxTurnsExceeded",
                RuntimeError,
                create=True,
            ),
        ):
            response = adapter.generate_response(
                messages=[{"role": "user", "content": "hello"}],
                available_tools=[],
                tool_callback=None,
            )

        self.assertEqual(response, "ok")
        run_config = fake_runner.run_sync.call_args.kwargs["run_config"]
        self.assertFalse(run_config.tracing_disabled)
        self.assertFalse(run_config.trace_include_sensitive_data)


if __name__ == "__main__":
    unittest.main()
