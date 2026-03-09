"""Model pricing — hardcoded from OpenRouter API (2026-03-03).

Per-token USD prices.  Model names use OpenRouter format.
"""

import warnings

# (input_price_per_token, output_price_per_token)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "openai/gpt-5.2": (0.00000175, 0.000014),
    "anthropic/claude-sonnet-4.6": (0.000003, 0.000015),
    "anthropic/claude-opus-4.6": (0.000005, 0.000025),
}


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost from token counts.  0.0 + warning if model unknown."""
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        warnings.warn(f"No pricing for model '{model}', cost=0")
        return 0.0
    return input_tokens * pricing[0] + output_tokens * pricing[1]


def get_deepeval_cost_kwargs(model: str) -> dict:
    """Return ``cost_per_input_token`` / ``cost_per_output_token`` kwargs
    suitable for ``deepeval.models.llms.openai_model.GPTModel``."""
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        return {}
    return {
        "cost_per_input_token": pricing[0],
        "cost_per_output_token": pricing[1],
    }
