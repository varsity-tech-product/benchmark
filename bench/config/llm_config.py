"""Central LLM model configuration for QuantTutorBench.

All model names use OpenRouter format (e.g. "openai/gpt-4o") unless noted.
"""

import os

# --- Generic / Baseline (via OpenRouter) ---
AGENT_DEFAULT_MODEL = "google/gemini-3-flash-preview"

# --- Per-SDK agent models (native API format) ---
OPENAI_AGENT_MODEL = "gpt-4o"
ANTHROPIC_AGENT_MODEL = "claude-sonnet-4-6"
GOOGLE_AGENT_MODEL = "gemini-2.5-flash"

# --- Corresponding OpenRouter names (for baseline controlled comparison) ---
OPENAI_AGENT_MODEL_OR = "openai/gpt-4o"
ANTHROPIC_AGENT_MODEL_OR = "anthropic/claude-sonnet-4.6"
GOOGLE_AGENT_MODEL_OR = "google/gemini-2.5-flash-preview"

# --- Evaluation / Simulation ---
SIMULATOR_DEFAULT_MODEL = "openai/gpt-4o"
EVAL_DEFAULT_MODEL = "anthropic/claude-sonnet-4.6"
SYNTHESIS_DEFAULT_MODEL = "google/gemini-2.5-flash-preview"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# --- Model mapping: agent type → (native model, OpenRouter model) ---
AGENT_MODEL_MAP: dict[str, tuple[str, str]] = {
    "openai": (OPENAI_AGENT_MODEL, OPENAI_AGENT_MODEL_OR),
    "anthropic": (ANTHROPIC_AGENT_MODEL, ANTHROPIC_AGENT_MODEL_OR),
    "google": (GOOGLE_AGENT_MODEL, GOOGLE_AGENT_MODEL_OR),
    "generic": (AGENT_DEFAULT_MODEL, AGENT_DEFAULT_MODEL),
}


def get_model_for_agent(agent_type: str, use_openrouter: bool = False) -> str:
    """Get the model name for an agent type.

    Args:
        agent_type: One of "openai", "anthropic", "google", "generic".
        use_openrouter: If True, return the OpenRouter variant (for pure LLM
                        conditions that don't use the native SDK).
    """
    native, openrouter = AGENT_MODEL_MAP.get(
        agent_type, (AGENT_DEFAULT_MODEL, AGENT_DEFAULT_MODEL)
    )
    return openrouter if use_openrouter else native


def resolve_deepeval_model(model=None):
    """Resolve model for DeepEval components (judge, simulator).

    DeepEval uses the OpenAI SDK internally. This function handles three cases:

    1. OPENAI_BASE_URL → OpenRouter: return model name as-is (OpenRouter format).
    2. Native OpenAI API + OpenAI model (e.g. "openai/gpt-4o"): strip prefix → "gpt-4o".
    3. Native OpenAI API + non-OpenAI model (e.g. "anthropic/claude-sonnet-4.6"):
       create a GPTModel routed through OpenRouter using OPENROUTER_API_KEY.

    Returns either a model name string or a DeepEval GPTModel instance.
    DeepEval metrics accept both.
    """
    # Already resolved to a DeepEval model object — return as-is (idempotent)
    if model is not None and not isinstance(model, str):
        return model

    model = model or EVAL_DEFAULT_MODEL

    # Case 1: OPENAI_BASE_URL points to OpenRouter — keep full model name
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if "openrouter" in base_url:
        return model

    # Case 2: Native OpenAI API with OpenAI model — strip "openai/" prefix
    if model.startswith("openai/"):
        return model[len("openai/") :]

    # Non-provider-prefixed model (e.g. "gpt-4o") — assumed OpenAI-compatible
    if "/" not in model:
        return model

    # Case 3: Non-OpenAI model (e.g. "anthropic/...") on native OpenAI API
    # → Route through OpenRouter if OPENROUTER_API_KEY is available
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        try:
            from deepeval.models.llms.openai_model import GPTModel

            return GPTModel(
                model=model,
                api_key=openrouter_key,
                base_url=OPENROUTER_BASE_URL,
            )
        except Exception:
            pass

    return model
