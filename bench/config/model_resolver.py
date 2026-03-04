"""Model resolution utilities for DeepEval and agent adapters."""

import os
import random

from config.llm_config import (
    AGENT_DEFAULT_MODEL,
    AGENT_MODEL_MAP,
    EVAL_DEFAULT_MODELS,
    OPENROUTER_BASE_URL,
)
from config.pricing import get_deepeval_cost_kwargs


def get_model_for_agent(agent_type: str, use_openrouter: bool = False) -> str:
    """Get the model name for an agent type.

    Args:
        agent_type: One of "openai", "anthropic", "google", "generic".
        use_openrouter: If True, return the OpenRouter variant.
    """
    native, openrouter = AGENT_MODEL_MAP.get(
        agent_type, (AGENT_DEFAULT_MODEL, AGENT_DEFAULT_MODEL)
    )
    return openrouter if use_openrouter else native


def resolve_deepeval_model(model=None):
    """Resolve model for DeepEval components (judge, simulator).

    Always routes through OpenRouter when OPENROUTER_API_KEY is available.
    Returns a GPTModel instance or a plain model name string.
    """
    if model is not None and not isinstance(model, (str, list)):
        return model

    if isinstance(model, list):
        model = random.choice(model)
    elif model is None:
        model = random.choice(EVAL_DEFAULT_MODELS)

    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        or_model = model
        if "/" not in model:
            or_model = f"openai/{model}"
        try:
            from deepeval.models.llms.openai_model import GPTModel

            return GPTModel(
                model=or_model,
                api_key=openrouter_key,
                base_url=OPENROUTER_BASE_URL,
                **get_deepeval_cost_kwargs(or_model),
            )
        except Exception:
            pass

    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if "openrouter" in base_url:
        return model

    if model.startswith("openai/"):
        return model[len("openai/") :]

    return model
