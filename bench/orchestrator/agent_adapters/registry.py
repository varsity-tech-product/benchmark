"""Adapter registry — maps --agent names to their adapter classes.

Centralises the mapping so new SDKs can be added without touching
run_benchmark.py.  Each adapter is lazy-imported when first used.
"""

import importlib
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AdapterSpec:
    """Specification for an agent adapter."""

    module: str  # dotted path relative to orchestrator.agent_adapters
    class_name: str
    use_openrouter: bool = False  # model lookup flag passed to get_model_for_agent
    extra_kwargs: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Registry — every supported --agent value lives here
# ---------------------------------------------------------------------------
ADAPTER_REGISTRY: dict[str, AdapterSpec] = {
    "generic": AdapterSpec(
        module="orchestrator.agent_adapters.generic_adapter",
        class_name="GenericLLMAdapter",
    ),
    "openai": AdapterSpec(
        module="orchestrator.agent_adapters.openai_adapter",
        class_name="OpenAIAgentAdapter",
        use_openrouter=True,
    ),
    "anthropic": AdapterSpec(
        module="orchestrator.agent_adapters.anthropic_adapter",
        class_name="ClaudeAgentAdapter",
    ),
    "google": AdapterSpec(
        module="orchestrator.agent_adapters.google_adapter",
        class_name="GoogleAdapter",
    ),
    "mistral": AdapterSpec(
        module="orchestrator.agent_adapters.mistral_adapter",
        class_name="MistralAdapter",
    ),
    "strands": AdapterSpec(
        module="orchestrator.agent_adapters.strands_adapter",
        class_name="StrandsAdapter",
    ),
    "microsoft": AdapterSpec(
        module="orchestrator.agent_adapters.microsoft_adapter",
        class_name="MicrosoftAgentAdapter",
    ),
}


def get_available_agents() -> list[str]:
    """Return sorted list of registered agent names (for argparse choices)."""
    return sorted(ADAPTER_REGISTRY.keys())


def create_adapter(
    agent_type: str,
    *,
    model: str | None = None,
    system_prompt: str = "",
    agent_name: str = "",
) -> Any:
    """Lazy-import and instantiate the adapter for *agent_type*.

    Extra kwargs stored in the AdapterSpec (e.g. base_url for OpenAI) are
    forwarded to the adapter constructor.
    """
    from config.llm_config import OPENROUTER_BASE_URL, get_model_for_agent

    spec = ADAPTER_REGISTRY.get(agent_type)
    if spec is None:
        raise ValueError(
            f"Unknown agent type '{agent_type}'. "
            f"Available: {', '.join(get_available_agents())}"
        )

    # Resolve model
    resolved_model = model or get_model_for_agent(
        agent_type, use_openrouter=spec.use_openrouter
    )

    # Lazy-import the module
    mod = importlib.import_module(spec.module)
    cls = getattr(mod, spec.class_name)

    # Build constructor kwargs
    kwargs: dict[str, Any] = {
        "model": resolved_model,
        "system_prompt": system_prompt,
        "agent_name": agent_name or f"{agent_type}_adapter",
    }

    # OpenAI adapter needs base_url when routing through OpenRouter
    if agent_type == "openai" and spec.use_openrouter:
        kwargs["base_url"] = OPENROUTER_BASE_URL

    # Merge any spec-level extra kwargs
    kwargs.update(spec.extra_kwargs)

    return cls(**kwargs)
