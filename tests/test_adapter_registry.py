"""Tests for agent adapter registry and new adapter integration paths."""

import importlib
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure bench/ is importable
BENCH_ROOT = Path(__file__).parent.parent / "bench"
sys.path.insert(0, str(BENCH_ROOT))


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    """Tests for the central adapter registry."""

    def test_all_seven_adapters_registered(self):
        from orchestrator.agent_adapters.registry import ADAPTER_REGISTRY

        expected = {
            "generic",
            "openai",
            "anthropic",
            "google",
            "mistral",
            "strands",
            "microsoft",
        }
        assert set(ADAPTER_REGISTRY.keys()) == expected

    def test_get_available_agents_sorted(self):
        from orchestrator.agent_adapters.registry import get_available_agents

        agents = get_available_agents()
        assert agents == sorted(agents)
        assert len(agents) == 7

    def test_create_adapter_unknown_type_raises(self):
        from orchestrator.agent_adapters.registry import create_adapter

        with pytest.raises(ValueError, match="Unknown agent type"):
            create_adapter("nonexistent", system_prompt="test")

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "test", "OPENAI_API_KEY": "test"})
    def test_create_generic_adapter(self):
        from orchestrator.agent_adapters.registry import create_adapter

        adapter = create_adapter("generic", system_prompt="test")
        assert type(adapter).__name__ == "GenericLLMAdapter"
        assert hasattr(adapter, "system_prompt")

    @patch.dict(os.environ, {"OPENROUTER_API_KEY": "test", "OPENAI_API_KEY": "test"})
    def test_create_openai_adapter(self):
        from orchestrator.agent_adapters.registry import create_adapter

        adapter = create_adapter("openai", system_prompt="test")
        assert type(adapter).__name__ == "OpenAIAgentAdapter"

    @patch.dict(os.environ, {"GOOGLE_API_KEY": "test"})
    def test_create_google_adapter(self):
        from orchestrator.agent_adapters.registry import create_adapter

        adapter = create_adapter("google", system_prompt="test")
        assert type(adapter).__name__ == "GoogleAdapter"

    @patch.dict(os.environ, {"MISTRAL_API_KEY": "test"})
    def test_create_mistral_adapter(self):
        from orchestrator.agent_adapters.registry import create_adapter

        adapter = create_adapter("mistral", system_prompt="test")
        assert type(adapter).__name__ == "MistralAdapter"

    @patch.dict(
        os.environ,
        {
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
        },
    )
    def test_create_strands_adapter(self):
        from orchestrator.agent_adapters.registry import create_adapter

        adapter = create_adapter("strands", system_prompt="test")
        assert type(adapter).__name__ == "StrandsAdapter"


# ---------------------------------------------------------------------------
# Module import tests (all should import even without SDKs installed)
# ---------------------------------------------------------------------------


class TestAdapterImports:
    """Every adapter module must import without its optional SDK."""

    @pytest.mark.parametrize(
        "module",
        [
            "orchestrator.agent_adapters.generic_adapter",
            "orchestrator.agent_adapters.openai_adapter",
            "orchestrator.agent_adapters.anthropic_adapter",
            "orchestrator.agent_adapters.google_adapter",
            "orchestrator.agent_adapters.mistral_adapter",
            "orchestrator.agent_adapters.strands_adapter",
            "orchestrator.agent_adapters.microsoft_adapter",
        ],
    )
    def test_module_imports_cleanly(self, module):
        mod = importlib.import_module(module)
        assert mod is not None


# ---------------------------------------------------------------------------
# Model config tests
# ---------------------------------------------------------------------------


class TestModelConfig:
    """Tests for llm_config model mapping completeness."""

    def test_all_adapters_have_model_mapping(self):
        from config.llm_config import AGENT_MODEL_MAP
        from orchestrator.agent_adapters.registry import ADAPTER_REGISTRY

        for agent_type in ADAPTER_REGISTRY:
            assert (
                agent_type in AGENT_MODEL_MAP
            ), f"Agent type '{agent_type}' missing from AGENT_MODEL_MAP"

    def test_model_map_returns_tuple_pairs(self):
        from config.llm_config import AGENT_MODEL_MAP

        for agent_type, pair in AGENT_MODEL_MAP.items():
            assert (
                isinstance(pair, tuple) and len(pair) == 2
            ), f"AGENT_MODEL_MAP['{agent_type}'] should be (native, openrouter) tuple"
            native, openrouter = pair
            assert isinstance(native, str) and native
            assert isinstance(openrouter, str) and openrouter


# ---------------------------------------------------------------------------
# Strands model resolution tests
# ---------------------------------------------------------------------------


class TestStrandsModelResolution:
    """Verify Strands remaps Bedrock model IDs for fallback providers."""

    def test_strands_bedrock_model_is_default(self):
        from config.llm_config import STRANDS_AGENT_MODEL, STRANDS_BEDROCK_MODEL

        assert STRANDS_AGENT_MODEL == STRANDS_BEDROCK_MODEL

    def test_strands_fallback_models_differ_from_bedrock(self):
        from config.llm_config import (
            STRANDS_ANTHROPIC_MODEL,
            STRANDS_BEDROCK_MODEL,
            STRANDS_OPENAI_MODEL,
        )

        assert STRANDS_ANTHROPIC_MODEL != STRANDS_BEDROCK_MODEL
        assert STRANDS_OPENAI_MODEL != STRANDS_BEDROCK_MODEL


# ---------------------------------------------------------------------------
# Microsoft env bridge guard tests
# ---------------------------------------------------------------------------


class TestMicrosoftEnvGuard:
    """Microsoft adapter must reject the OpenRouter bridge key."""

    @patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "sk-or-fake",
            "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
        },
    )
    def test_rejects_openrouter_bridge(self):
        from orchestrator.agent_adapters.microsoft_adapter import (
            MicrosoftAgentAdapter,
        )

        with pytest.raises(ValueError, match="native OPENAI_API_KEY"):
            MicrosoftAgentAdapter(system_prompt="test")

    @patch.dict(
        os.environ,
        {
            "OPENAI_API_KEY": "sk-real-key",
            "OPENAI_BASE_URL": "",
        },
    )
    def test_accepts_real_openai_key(self):
        from orchestrator.agent_adapters.microsoft_adapter import (
            MicrosoftAgentAdapter,
        )

        adapter = MicrosoftAgentAdapter(system_prompt="test")
        assert adapter.api_key == "sk-real-key"

    @patch.dict(os.environ, {"OPENAI_API_KEY": "sk-real"}, clear=False)
    def test_explicit_api_key_bypasses_bridge_check(self):
        """When api_key is passed explicitly, skip the env bridge check."""
        from orchestrator.agent_adapters.microsoft_adapter import (
            MicrosoftAgentAdapter,
        )

        # Even with openrouter in OPENAI_BASE_URL, an explicit key is OK
        with patch.dict(
            os.environ,
            {
                "OPENAI_BASE_URL": "https://openrouter.ai/api/v1",
            },
        ):
            adapter = MicrosoftAgentAdapter(api_key="sk-explicit", system_prompt="test")
            assert adapter.api_key == "sk-explicit"


# ---------------------------------------------------------------------------
# Tool schema builder tests
# ---------------------------------------------------------------------------


class TestToolSchemaBuilders:
    """Verify tool wrappers expose typed parameter schemas."""

    SAMPLE_TOOLS = [
        {
            "name": "fetch_data",
            "description": "Fetch market data",
            "parameters": {
                "ticker": {
                    "type": "string",
                    "description": "Stock ticker symbol",
                    "required": True,
                },
                "period": {
                    "type": "string",
                    "description": "Time period",
                    "required": False,
                },
            },
        }
    ]

    def test_strands_tool_has_schema(self):
        from orchestrator.agent_adapters.strands_adapter import (
            _build_json_schema,
        )

        params = self.SAMPLE_TOOLS[0]["parameters"]
        schema = _build_json_schema(params)
        assert schema["type"] == "object"
        assert "ticker" in schema["properties"]
        assert schema["properties"]["ticker"]["type"] == "string"
        assert "required" in schema
        assert "ticker" in schema["required"]
        assert "period" not in schema["required"]

    def test_microsoft_tool_has_schema(self):
        from orchestrator.agent_adapters.microsoft_adapter import (
            _build_json_schema,
        )

        params = self.SAMPLE_TOOLS[0]["parameters"]
        schema = _build_json_schema(params)
        assert schema["type"] == "object"
        assert "ticker" in schema["properties"]
        assert "required" in schema
        assert "ticker" in schema["required"]
