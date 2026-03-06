"""Central LLM model configuration — constants only.

All model names use OpenRouter format (e.g. "openai/gpt-5.2") unless noted.
For resolution utilities see ``config.model_resolver``.
"""

# --- Generic / Baseline (via OpenRouter) ---
AGENT_DEFAULT_MODEL = "openai/gpt-5.2"

# --- Per-SDK agent models (native API format) ---
OPENAI_AGENT_MODEL = "gpt-5.2"
ANTHROPIC_AGENT_MODEL = "claude-sonnet-4-6"
GOOGLE_AGENT_MODEL = "gemini-2.5-flash"

# --- Corresponding OpenRouter names (for baseline controlled comparison) ---
OPENAI_AGENT_MODEL_OR = "openai/gpt-5.2"
ANTHROPIC_AGENT_MODEL_OR = "anthropic/claude-sonnet-4.6"
GOOGLE_AGENT_MODEL_OR = "google/gemini-2.5-flash-preview"

# --- Reference Oracle (for generating scoring anchors) ---
REFERENCE_DEFAULT_MODEL = "openai/gpt-5.2"

# --- Evaluation / Simulation ---
SIMULATOR_DEFAULT_MODEL = "openai/gpt-5.2"
EVAL_DEFAULT_MODELS: list[str] = [
    "anthropic/claude-sonnet-4.6",
    # "openai/gpt-5.2",  # disabled during testing phase
    # "anthropic/claude-opus-4.6",  # disabled during testing phase
]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# --- Model mapping: agent type → (native model, OpenRouter model) ---
AGENT_MODEL_MAP: dict[str, tuple[str, str]] = {
    "openai": (OPENAI_AGENT_MODEL, OPENAI_AGENT_MODEL_OR),
    "anthropic": (ANTHROPIC_AGENT_MODEL, ANTHROPIC_AGENT_MODEL_OR),
    "google": (GOOGLE_AGENT_MODEL, GOOGLE_AGENT_MODEL_OR),
    "generic": (AGENT_DEFAULT_MODEL, AGENT_DEFAULT_MODEL),
}
