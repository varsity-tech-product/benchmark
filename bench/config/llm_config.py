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
TC_CHECKER_MODEL = "anthropic/claude-sonnet-4-6"
EVAL_DEFAULT_MODELS: list[str] = [
    # "anthropic/claude-haiku-4.5",
    "anthropic/claude-sonnet-4.6",
    # "openai/gpt-5.2",
    # "anthropic/claude-opus-4.6",
]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Anthropic Skin: exposes Anthropic Messages API at /api (SDK appends /v1/messages).
# Passes through thinking blocks + native tool use. Used by AGENT_USE_OPENROUTER.
OPENROUTER_ANTHROPIC_BASE_URL = "https://openrouter.ai/api"

# --- OAuth direct (Claude Max) ---
# Anthropic eval models use OAuth → Anthropic API, falling back to OpenRouter.
# Disabled for eval: OAuth path bypasses DeepEval's temperature controls,
# causing judges to run at Anthropic's default temp=1.0 instead of 0.0.
EVAL_USE_OAUTH = False
OAUTH_BETA_HEADER = "oauth-2025-04-20"

# --- Anthropic agent transport ---
# True = Claude Agent SDK (black-box). False = Messages API loop (visible COT).
# SDK mode requires API key; does NOT work with OAuth.
ANTHROPIC_USE_SDK = False

# --- Agent OAuth (Claude Max) ---
# Uses CLAUDE_CODE_OAUTH_TOKEN instead of ANTHROPIC_API_KEY. Only when SDK=False.
AGENT_USE_OAUTH = False
# True = route agent calls through OpenRouter Anthropic Skin (OPENROUTER_API_KEY).
# Supports tool_runner, thinking, compaction — transparent proxy to Anthropic API.
# False = Anthropic direct API (ANTHROPIC_API_KEY). Only when AGENT_USE_OAUTH=False.
AGENT_USE_OPENROUTER = True

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

# --- Evaluation temperature ---
# Judge / TC checker / simulator temperature.  Deterministic (0.0) ensures
# reproducible scores across runs.  Agent temperature is NOT controlled here
# — agents run at their provider's default to reflect real-world behaviour.
EVAL_JUDGE_TEMPERATURE = 0.0

# --- Model mapping: agent type → (native model, OpenRouter model) ---
AGENT_MODEL_MAP: dict[str, tuple[str, str]] = {
    "openai": (OPENAI_AGENT_MODEL, OPENAI_AGENT_MODEL_OR),
    "anthropic": (ANTHROPIC_AGENT_MODEL, ANTHROPIC_AGENT_MODEL_OR),
    "google": (GOOGLE_AGENT_MODEL, GOOGLE_AGENT_MODEL_OR),
    "generic": (AGENT_DEFAULT_MODEL, AGENT_DEFAULT_MODEL),
}
