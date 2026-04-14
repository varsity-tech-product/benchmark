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
    _resolve_persona_pin,
    _session_random_seed,
)
from server.core.staging import create_staged_sample_code


class SampleCodeStagingTests(unittest.TestCase):
    def test_create_staged_sample_code_uses_neutral_filename(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            student_root = root / "student_code"
            student_root.mkdir()
            source = student_root / "alpha_conflict.cs"
            source.write_text("// demo", encoding="utf-8")

            staged_dir, temp_dirs = create_staged_sample_code(
                "student_code/alpha_conflict.cs",
                student_code_dir=str(student_root),
            )

            self.assertIsNotNone(staged_dir)
            staged_root = Path(staged_dir)
            self.assertEqual(
                sorted(p.name for p in staged_root.iterdir()), ["student_code.cs"]
            )
            self.assertEqual(
                (staged_root / "student_code.cs").read_text(encoding="utf-8"), "// demo"
            )
            self.assertEqual(temp_dirs, [staged_dir])


class SessionContextTests(unittest.TestCase):
    def test_build_session_context_omits_task_identifiers_and_sample_code(self):
        state = SessionState(session_id="sess-123", use_docker=False)
        state.task_id = "X09_alpha_conflict"
        state.task = SimpleNamespace(
            category=SimpleNamespace(value="debug"),
            requires_code=True,
            sample_code="student_code/alpha_conflict.cs",
            environment=SimpleNamespace(
                sandbox_image="quant-tutor-env:v2.2-lean",
                max_backtest_trials=5,
            ),
        )

        context = state._build_session_context(
            docs_available=["algorithm_framework_guide.md"],
            student_code_dir="/student_code",
        )

        self.assertEqual(
            context,
            {
                "category": "debug",
                "requires_code": True,
                "docs_available": ["algorithm_framework_guide.md"],
                "max_backtest_trials": 5,
                "sandbox_image": "quant-tutor-env:v2.2-lean",
                "student_code_available": True,
            },
        )
        self.assertNotIn("task_id", context)
        self.assertNotIn("sample_code", context)


class PersonaPinTests(unittest.TestCase):
    def test_resolve_persona_pin_prefers_json_mapping(self):
        with mock.patch.dict(
            "os.environ",
            {
                "QTB_TEST_PERSONA_PIN_JSON": json.dumps(
                    {"X09_alpha_conflict": "intermediate_developer"}
                )
            },
            clear=False,
        ):
            chosen = _resolve_persona_pin(
                "X09_alpha_conflict",
                ["beginner_no_finance", "intermediate_developer"],
            )
        self.assertEqual(chosen, "intermediate_developer")

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
