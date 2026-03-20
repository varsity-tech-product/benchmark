"""Central LLM model configuration.

Model names use OpenRouter format unless noted.
Resolution utilities: ``config.model_resolver``.
"""

# --- Generic / Baseline (via OpenRouter) ---
AGENT_DEFAULT_MODEL = "openai/gpt-5.2"

# --- Per-SDK agent models (native API format) ---
OPENAI_AGENT_MODEL = "gpt-5.2"
ANTHROPIC_AGENT_MODEL = "claude-haiku-4-5-20251001"
GOOGLE_AGENT_MODEL = "gemini-2.5-flash"

# --- OpenRouter equivalents (for baseline comparison) ---
OPENAI_AGENT_MODEL_OR = "openai/gpt-5.2"
ANTHROPIC_AGENT_MODEL_OR = "anthropic/claude-haiku-4-5-20251001"
GOOGLE_AGENT_MODEL_OR = "google/gemini-2.5-flash-preview"

# --- Reference / Evaluation / Simulation ---
REFERENCE_DEFAULT_MODEL = "openai/gpt-5.2"
SIMULATOR_DEFAULT_MODEL = "openai/gpt-5.2"
EVAL_DEFAULT_MODELS: list[str] = [
    "anthropic/claude-sonnet-4.6",
    # "openai/gpt-5.2",
    # "anthropic/claude-opus-4.6",
]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# --- OAuth direct (Claude Max) ---
# Anthropic eval models use OAuth → Anthropic API, falling back to OpenRouter.
EVAL_USE_OAUTH = False
OAUTH_BETA_HEADER = "oauth-2025-04-20"

# --- Anthropic agent transport ---
# True = Claude Agent SDK (black-box). False = Messages API loop (visible COT).
# SDK mode requires API key; does NOT work with OAuth.
ANTHROPIC_USE_SDK = False

# --- Agent OAuth (Claude Max) ---
# Uses CLAUDE_CODE_OAUTH_TOKEN instead of ANTHROPIC_API_KEY. Only when SDK=False.
AGENT_USE_OAUTH = True

# --- OpenAI direct API mode ---
# True = Chat Completions loop (visible reasoning). False = Agents SDK Runner.
OPENAI_USE_DIRECT_API = True

# --- Anthropic extended thinking ---
# Thinking blocks captured in trace, stripped from history to avoid O(n²) growth.
ANTHROPIC_ENABLE_THINKING = True
ANTHROPIC_THINKING_BUDGET = 4096

# --- OpenAI reasoning ---
# Effort levels: "none", "low", "medium", "high". No reasoning text exposed.
OPENAI_ENABLE_REASONING = False
OPENAI_REASONING_EFFORT = "medium"

# --- Model mapping: agent type → (native model, OpenRouter model) ---
AGENT_MODEL_MAP: dict[str, tuple[str, str]] = {
    "openai": (OPENAI_AGENT_MODEL, OPENAI_AGENT_MODEL_OR),
    "anthropic": (ANTHROPIC_AGENT_MODEL, ANTHROPIC_AGENT_MODEL_OR),
    "google": (GOOGLE_AGENT_MODEL, GOOGLE_AGENT_MODEL_OR),
    "generic": (AGENT_DEFAULT_MODEL, AGENT_DEFAULT_MODEL),
}
