"""Client adapter configuration.

Minimal config for baseline Anthropic adapter.
Merged from Legacy config/llm_config.py + config/pricing.py.
"""

from pathlib import Path

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

# Model
ANTHROPIC_AGENT_MODEL = "claude-haiku-4-5"
ANTHROPIC_AGENT_MODEL_OR = "anthropic/claude-haiku-4-5"

# Routing
AGENT_USE_OPENROUTER = True
ANTHROPIC_USE_SDK = False
OPENROUTER_ANTHROPIC_BASE_URL = "https://openrouter.ai/api"

# Thinking
ANTHROPIC_ENABLE_THINKING = True
ANTHROPIC_THINKING_BUDGET = 4096


# Pricing (USD per token) — both OpenRouter and native model names
MODEL_PRICING = {
    "anthropic/claude-sonnet-4-6": (0.000003, 0.000015),
    "anthropic/claude-sonnet-4.6": (0.000003, 0.000015),
    "claude-sonnet-4-6": (0.000003, 0.000015),
    "anthropic/claude-haiku-4-5": (0.0000008, 0.000004),
    "claude-haiku-4-5": (0.0000008, 0.000004),
    "anthropic/claude-opus-4-6": (0.000015, 0.000075),
    "claude-opus-4-6": (0.000015, 0.000075),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = MODEL_PRICING.get(model)
    if not pricing and "/" not in model:
        # Try with anthropic/ prefix
        pricing = MODEL_PRICING.get(f"anthropic/{model}")
    if not pricing:
        return 0.0
    return input_tokens * pricing[0] + output_tokens * pricing[1]
