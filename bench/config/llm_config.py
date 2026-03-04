"""Central LLM model configuration for QuantTutorBench.

All model names use OpenRouter format (e.g. "openai/gpt-4o") unless noted.
"""

import os

# --- Generic / Baseline (via OpenRouter) ---
# AGENT_DEFAULT_MODEL = "google/gemini-3-flash-preview"
AGENT_DEFAULT_MODEL = "openai/gpt-5.2"

# --- Per-SDK agent models (native API format) ---
OPENAI_AGENT_MODEL = "gpt-5.2"
ANTHROPIC_AGENT_MODEL = "claude-sonnet-4-6"
GOOGLE_AGENT_MODEL = "gemini-2.5-flash"
MISTRAL_AGENT_MODEL = "mistral-large-latest"
MICROSOFT_AGENT_MODEL = "gpt-4o"

# Strands supports multiple backends; each needs its own model ID format.
STRANDS_BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-20250514-v1:0"
STRANDS_ANTHROPIC_MODEL = "claude-sonnet-4-6"
STRANDS_OPENAI_MODEL = "gpt-4o"
STRANDS_AGENT_MODEL = STRANDS_BEDROCK_MODEL  # default (Bedrock)

# --- Corresponding OpenRouter names (for baseline controlled comparison) ---
OPENAI_AGENT_MODEL_OR = "openai/gpt-5.2"
ANTHROPIC_AGENT_MODEL_OR = "anthropic/claude-sonnet-4.6"
GOOGLE_AGENT_MODEL_OR = "google/gemini-2.5-flash-preview"
MISTRAL_AGENT_MODEL_OR = "mistralai/mistral-large-latest"
STRANDS_AGENT_MODEL_OR = "anthropic/claude-sonnet-4"
MICROSOFT_AGENT_MODEL_OR = "openai/gpt-4o"

# --- Evaluation / Simulation ---
SIMULATOR_DEFAULT_MODEL = "openai/gpt-5.2"
EVAL_DEFAULT_MODEL = "anthropic/claude-sonnet-4.6"
SYNTHESIS_DEFAULT_MODEL = "google/gemini-2.5-flash-preview"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


# --- Model mapping: agent type → (native model, OpenRouter model) ---
AGENT_MODEL_MAP: dict[str, tuple[str, str]] = {
    "openai": (OPENAI_AGENT_MODEL, OPENAI_AGENT_MODEL_OR),
    "anthropic": (ANTHROPIC_AGENT_MODEL, ANTHROPIC_AGENT_MODEL_OR),
    "google": (GOOGLE_AGENT_MODEL, GOOGLE_AGENT_MODEL_OR),
    "generic": (AGENT_DEFAULT_MODEL, AGENT_DEFAULT_MODEL),
    "mistral": (MISTRAL_AGENT_MODEL, MISTRAL_AGENT_MODEL_OR),
    "strands": (STRANDS_AGENT_MODEL, STRANDS_AGENT_MODEL_OR),
    "microsoft": (MICROSOFT_AGENT_MODEL, MICROSOFT_AGENT_MODEL_OR),
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

    Strategy: Always route DeepEval traffic through OpenRouter when
    OPENROUTER_API_KEY is available.  This returns a GPTModel object that
    carries its own api_key and base_url, so it is completely independent
    of OPENAI_API_KEY / OPENAI_BASE_URL env vars.  Native SDK adapters
    (OpenAI, Anthropic, Google) manage their own credentials separately.

    Returns either a DeepEval GPTModel instance or a plain model name string.
    """
    # Already resolved to a DeepEval model object — return as-is (idempotent)
    if model is not None and not isinstance(model, str):
        return model

    model = model or EVAL_DEFAULT_MODEL

    # Prefer OpenRouter for all DeepEval traffic — avoids rate-limit
    # conflicts with native SDK adapters that share OPENAI_API_KEY.
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        # Ensure model name has a provider prefix (OpenRouter format)
        or_model = model
        if "/" not in model:
            or_model = f"openai/{model}"  # e.g. "gpt-4o" → "openai/gpt-4o"
        try:
            from deepeval.models.llms.openai_model import GPTModel

            return GPTModel(
                model=or_model,
                api_key=openrouter_key,
                base_url=OPENROUTER_BASE_URL,
            )
        except Exception:
            pass

    # Fallback: no OpenRouter key — use OPENAI_API_KEY env vars directly
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if "openrouter" in base_url:
        return model

    if model.startswith("openai/"):
        return model[len("openai/") :]

    if "/" not in model:
        return model

    return model
