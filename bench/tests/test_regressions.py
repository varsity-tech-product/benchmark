import sys
import types
import warnings
import unittest

sys.modules.setdefault(
    "dotenv",
    types.SimpleNamespace(load_dotenv=lambda *args, **kwargs: None),
)

from config.pricing import estimate_cost
from config.prompt_config import build_scenario, build_tutor_context
from orchestrator.agent_adapters.openai_adapter import OpenAIAgentAdapter
from orchestrator.schemas import (
    Difficulty,
    EnvironmentConfig,
    GroundTruth,
    QuantTutorTask,
    StudentPersona,
    TaskCategory,
)


def _make_persona() -> StudentPersona:
    return StudentPersona(
        persona_id="intermediate_developer",
        knowledge_level="intermediate",
        description="Comfortable with Python and basic quant workflows.",
    )


def _make_task(*, requires_code: bool) -> QuantTutorTask:
    return QuantTutorTask(
        task_id="I10_parameter_optimization",
        difficulty=Difficulty.HARD,
        category=TaskCategory.IMPLEMENTATION,
        description="Implement and verify a parameter optimization workflow.",
        persona_ids=["intermediate_developer"],
        student_openings={
            "intermediate_developer": "How should I implement the optimization workflow?"
        },
        environment=EnvironmentConfig(
            data_files=["universe.json"],
            core_mcp_tools=["shell_exec", "file_write", "file_read", "get_environment_info"],
            docs_available=["algorithm_framework_guide.md"],
        ),
        ground_truth=GroundTruth(
            expected_outcome="Produce code, artifacts, and ranked optimization results.",
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
        self.assertTrue(any("No pricing for model" in str(item.message) for item in caught))


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
        scenario = build_scenario(_make_task(requires_code=True), "intermediate_developer")
        self.assertIn("IMPLEMENTATION TRACKING", scenario)
        self.assertIn("WHEN THE TUTOR STAYS ABSTRACT", scenario)


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


if __name__ == "__main__":
    unittest.main()
