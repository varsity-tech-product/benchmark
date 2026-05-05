import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if "mcp" not in sys.modules:
    mcp_module = types.ModuleType("mcp")
    mcp_types_module = types.ModuleType("mcp.types")

    class _TextContent:
        def __init__(self, type: str, text: str):
            self.type = type
            self.text = text

    class _Tool:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    mcp_types_module.TextContent = _TextContent
    mcp_types_module.Tool = _Tool
    mcp_module.types = mcp_types_module
    sys.modules["mcp"] = mcp_module
    sys.modules["mcp.types"] = mcp_types_module

from server.api.session_api import (
    SessionState,
    _environment_sandbox_digest,
    _resolve_persona_pin,
    _session_random_seed,
)
from server.core.staging import create_staged_sample_code


class SampleCodeStagingTests(unittest.TestCase):
    def test_create_staged_sample_code_uses_neutral_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            user_root = root / "user_code"
            user_root.mkdir()
            source = user_root / "alpha_conflict.cs"
            source.write_text("// demo", encoding="utf-8")

            staged_dir, temp_dirs = create_staged_sample_code(
                "user_code/alpha_conflict.cs",
                user_code_dir=str(user_root),
            )

            self.assertIsNotNone(staged_dir)
            staged_root = Path(staged_dir)
            self.assertEqual(
                sorted(p.name for p in staged_root.iterdir()), ["user_code.cs"]
            )
            self.assertEqual(
                (staged_root / "user_code.cs").read_text(encoding="utf-8"), "// demo"
            )
            self.assertEqual(temp_dirs, [staged_dir])


class SessionContextTests(unittest.TestCase):
    def test_build_session_context_omits_task_identifiers_and_sample_code(self):
        state = SessionState(session_id="sess-123", use_docker=False)
        state.task_id = "X09_alpha_conflict"
        state.task = SimpleNamespace(
            category=SimpleNamespace(value="debug"),
            requires_code=True,
            sample_code="user_code/alpha_conflict.cs",
            environment=SimpleNamespace(
                sandbox_image="quant-tutor-env:v2.2-lean",
                max_backtest_trials=5,
            ),
        )

        context = state._build_session_context(
            docs_available=["algorithm_framework_guide.md"],
            user_code_dir="/user_code",
        )

        self.assertEqual(
            context,
            {
                "category": "debug",
                "requires_code": True,
                "docs_available": ["algorithm_framework_guide.md"],
                "max_backtest_trials": 5,
                "sandbox_image": "quant-tutor-env:v2.2-lean",
                "user_code_available": True,
            },
        )
        self.assertNotIn("task_id", context)
        self.assertNotIn("sample_code", context)

    def test_session_context_uses_sandbox_spec_image(self):
        state = SessionState(session_id="sess-123", use_docker=False)
        state.task = SimpleNamespace(
            category=SimpleNamespace(value="implementation"),
            requires_code=True,
            sample_code=None,
            environment=SimpleNamespace(
                sandbox_image="legacy:image",
                sandbox_spec=SimpleNamespace(image_uri="spec:image-lean"),
                max_backtest_trials=3,
            ),
        )

        context = state._build_session_context(
            docs_available=[],
            user_code_dir=None,
        )

        self.assertEqual(context["sandbox_image"], "spec:image-lean")

    def test_build_background_uses_sandbox_spec_and_data_mounts(self):
        from server.core.session import build_background

        task = SimpleNamespace(
            sample_code="user_code/alpha_conflict.cs",
            series=None,
            custom_data_key=None,
            environment=SimpleNamespace(
                sandbox_image="legacy:image",
                sandbox_image_uri="spec:image-lean",
                docs_available=["alpha_conflict_guide.md"],
                data_files=[],
                data_mounts=[
                    {
                        "uri": "file:///secret/alpha_conflict_source",
                        "target_path": "/data/lean",
                        "read_only": True,
                    }
                ],
            ),
        )

        background = build_background(task)

        json.dumps(background)
        self.assertEqual(background["schema_version"], "platform_background.v1")
        self.assertEqual(background["sandbox"]["image"], "spec:image-lean")
        self.assertTrue(background["mounts"]["user_code"]["present"])
        self.assertNotIn("source", background["mounts"]["user_code"])
        self.assertTrue(background["mounts"]["docs"]["present"])
        self.assertNotIn("files", background["mounts"]["docs"])
        self.assertTrue(background["mounts"]["data"]["present"])
        self.assertNotIn("files", background["mounts"]["data"])
        self.assertEqual(
            background["mounts"]["data"]["mounts"][0]["target_path"],
            "/data/lean",
        )
        self.assertNotIn("uri", background["mounts"]["data"]["mounts"][0])
        self.assertIn(
            "algorithmic_trading_engine",
            {system["name"] for system in background["systems"]},
        )

        raw = json.dumps(background)
        for phrase in (
            "If the tutor asks",
            "send_message",
            "MUST",
            "Call get_environment_info",
            "only way your words reach the user",
            "alpha_conflict",
            "file://",
            "secret",
        ):
            self.assertNotIn(phrase, raw)

    def test_reference_prompt_owns_user_behavior_rules(self):
        from server.config.prompt_config import build_user_description
        from server.reference.prompts import RefSystemPrompt

        persona = SimpleNamespace(
            description="Comfortable with Python and basic quant workflows.",
            familiar_concepts=["returns"],
            unfamiliar_concepts=["slippage"],
            emotional_profile="",
            behavioral_rules=["Ask for clarification when confused."],
        )

        prompt = build_user_description(persona)

        self.assertEqual(prompt, RefSystemPrompt.build_user_description(persona))
        self.assertIn("[If the tutor asks you a question", prompt)
        self.assertIn("Ask for clarification when confused.", prompt)

    def test_sandbox_digest_preserves_legacy_network_flag(self):
        digest = _environment_sandbox_digest(
            SimpleNamespace(
                sandbox_image="legacy:image",
                sandbox_spec=None,
                network_enabled=True,
                data_mounts=[],
            )
        )

        self.assertTrue(digest["resource_limits"]["network_enabled"])


class ContainerManagerMountTests(unittest.TestCase):
    def test_prepare_nested_data_mount_targets_creates_staged_paths(self):
        from platform_api.runtime import SandboxMount
        from server.core.container import ContainerManager

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            data_dir = root / "staged_data"
            data_dir.mkdir()
            mounted_dir = root / "lean_data"
            mounted_dir.mkdir()
            mounted_file = root / "universe.json"
            mounted_file.write_text("{}", encoding="utf-8")

            ContainerManager._prepare_nested_mount_targets(
                (
                    SandboxMount(str(mounted_dir), "/data/lean"),
                    SandboxMount(str(mounted_file), "/data/meta/universe.json"),
                ),
                ((str(data_dir), "/data"),),
            )

            self.assertTrue((data_dir / "lean").is_dir())
            self.assertTrue((data_dir / "meta" / "universe.json").is_file())


class PersonaPinTests(unittest.TestCase):
    def test_resolve_persona_pin_prefers_json_mapping(self):
        with mock.patch.dict(
            "os.environ",
            {
                "QTB_TEST_PERSONA_PIN_JSON": json.dumps(
                    {"X09_alpha_conflict": "developer_crossover"}
                )
            },
            clear=False,
        ):
            chosen = _resolve_persona_pin(
                "X09_alpha_conflict",
                ["fullstack_practitioner", "developer_crossover"],
            )
        self.assertEqual(chosen, "developer_crossover")

    def test_session_random_seed_is_stable_with_internal_override(self):
        with mock.patch.dict(
            "os.environ",
            {"QTB_TEST_RANDOM_SEED": "regression-seed"},
            clear=False,
        ):
            seed_a = _session_random_seed("X09_alpha_conflict", "session-a", None)
            seed_b = _session_random_seed("X09_alpha_conflict", "session-b", None)

        self.assertEqual(seed_a, seed_b)


if __name__ == "__main__":
    unittest.main()
