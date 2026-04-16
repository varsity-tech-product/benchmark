"""Central LLM model configuration."""

# --- Generic / Baseline (via OpenRouter) ---
AGENT_DEFAULT_MODEL = "openai/gpt-5.2"

# --- Per-SDK agent models (native API format) ---
OPENAI_AGENT_MODEL = "gpt-5.2"
ANTHROPIC_AGENT_MODEL = "claude-sonnet-4-6"
GOOGLE_AGENT_MODEL = "gemini-2.5-flash"

# --- OpenRouter equivalents (for baseline comparison) ---
OPENAI_AGENT_MODEL_OR = "openai/gpt-5.2"
ANTHROPIC_AGENT_MODEL_OR = "anthropic/claude-sonnet-4-6"
GOOGLE_AGENT_MODEL_OR = "google/gemini-2.5-flash-preview"

# --- Reference / Evaluation / Simulation ---
REFERENCE_DEFAULT_MODEL = "openai/gpt-5.2"
TC_CHECKER_MODEL = "anthropic/claude-sonnet-4-6"

# --- Student simulator model pool (vision-capable only) ---
# All models must support image input via OpenRouter.
# Source: https://openrouter.ai/models?supported_parameters=images
# Last verified: 2026-04-15
STUDENT_MODEL_POOL: dict[str, list[str]] = {
    # Budget tier — < $0.50/1M input tokens
    "budget": [
        "openai/gpt-5-nano",                        # $0.05/1M, 400K ctx
        "google/gemini-2.0-flash-001",               # $0.10/1M, 1M ctx
        "google/gemini-2.5-flash-lite",              # $0.10/1M, 1M ctx
        "openai/gpt-4.1-nano",                       # $0.10/1M, 1M ctx
        "openai/gpt-4o-mini",                        # $0.15/1M, 128K ctx
        "openai/gpt-5-mini",                         # $0.25/1M, 400K ctx
        "google/gemini-2.5-flash",                   # $0.30/1M, 1M ctx
        "openai/gpt-4.1-mini",                       # $0.40/1M, 1M ctx
    ],
    # Standard tier — $0.50–$3.00/1M input tokens
    "standard": [
        "anthropic/claude-3.5-haiku",                # $0.80/1M, 200K ctx
        "anthropic/claude-haiku-4.5",                # $1.00/1M, 200K ctx
        "google/gemini-2.5-pro",                     # $1.25/1M, 1M ctx
        "openai/gpt-5",                              # $1.25/1M, 400K ctx
        "openai/gpt-5.1",                            # $1.25/1M, 400K ctx
        "openai/gpt-5.2",                            # $1.75/1M, 400K ctx
        "openai/gpt-4.1",                            # $2.00/1M, 1M ctx
        "openai/gpt-4o",                             # $2.50/1M, 128K ctx
        "openai/gpt-5.4",                            # $2.50/1M, 1M ctx
        "anthropic/claude-sonnet-4",                 # $3.00/1M, 1M ctx
        "anthropic/claude-sonnet-4.5",               # $3.00/1M, 1M ctx
        "anthropic/claude-sonnet-4.6",               # $3.00/1M, 1M ctx
        "x-ai/grok-4",                               # $3.00/1M, 256K ctx
    ],
    # Premium tier — > $3.00/1M input tokens
    "premium": [
        "anthropic/claude-opus-4.5",                 # $5.00/1M, 200K ctx
        "anthropic/claude-opus-4.6",                 # $5.00/1M, 1M ctx
        "openai/gpt-5-pro",                          # $15.00/1M, 400K ctx
    ],
}

# Flat set for fast membership check
STUDENT_MODEL_POOL_ALL: frozenset[str] = frozenset(
    model for tier in STUDENT_MODEL_POOL.values() for model in tier
)

SIMULATOR_DEFAULT_MODEL = "openai/gpt-5.2"  # must be in STUDENT_MODEL_POOL_ALL
EVAL_DEFAULT_MODELS: list[str] = [
    "anthropic/claude-haiku-4.5",
    # "anthropic/claude-sonnet-4.6",
    # "openai/gpt-5.2",
    # "anthropic/claude-opus-4.6",
]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
# Anthropic Skin: exposes Anthropic Messages API at /api (SDK appends /v1/messages).
# Passes through thinking blocks + native tool use. Used by AGENT_USE_OPENROUTER.
OPENROUTER_ANTHROPIC_BASE_URL = "https://openrouter.ai/api"

# causing judges to run at Anthropic's default temp=1.0 instead of 0.0.

# --- Anthropic agent transport ---
# True = Claude Agent SDK (black-box). False = Messages API loop (visible COT).
ANTHROPIC_USE_SDK = False

# True = route agent calls through OpenRouter Anthropic Skin (OPENROUTER_API_KEY).
# Supports tool_runner, thinking, compaction — transparent proxy to Anthropic API.
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
