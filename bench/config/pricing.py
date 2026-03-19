"""Model pricing — hardcoded from provider pricing snapshots.

Per-token USD prices.  Primary keys use OpenRouter format; native SDK
model names are resolved via ``_NATIVE_TO_OR`` alias table.
"""

import warnings

# (input_price_per_token, output_price_per_token)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "openai/gpt-5.2": (0.00000175, 0.000014),
    "openai/gpt-4o-mini": (0.00000015, 0.0000006),
    "gpt-4o-mini": (0.00000015, 0.0000006),
    "anthropic/claude-sonnet-4.6": (0.000003, 0.000015),
    "anthropic/claude-opus-4.6": (0.000005, 0.000025),
}

# Native SDK model name → OpenRouter name (for pricing lookup)
_NATIVE_TO_OR: dict[str, str] = {
    "claude-sonnet-4-6": "anthropic/claude-sonnet-4.6",
    "claude-opus-4-6": "anthropic/claude-opus-4.6",
    "gpt-5.2": "openai/gpt-5.2",
}


def _resolve_pricing(model: str) -> tuple[float, float] | None:
    """Look up pricing, trying direct match then native→OR alias."""
    pricing = MODEL_PRICING.get(model)
    if pricing is None:
        or_name = _NATIVE_TO_OR.get(model)
        if or_name:
            pricing = MODEL_PRICING.get(or_name)
    return pricing


def resolve_pricing_key(model: str) -> str | None:
    """Return the best-known pricing key for a model identifier."""
    normalized = (model or "").strip()
    if not normalized:
        return None

    candidates = [normalized]
    if "/" in normalized:
        candidates.append(normalized.split("/")[-1])
    else:
        candidates.extend(
            [
                f"openai/{normalized}",
                f"anthropic/{normalized}",
                f"google/{normalized}",
            ]
        )

    for candidate in candidates:
        if candidate in MODEL_PRICING:
            return candidate
    return None


def estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return USD cost from token counts.  0.0 + warning if model unknown."""
    pricing = _resolve_pricing(model)
    if pricing is None:
        warnings.warn(f"No pricing for model '{model}', cost=0")
        return 0.0
    return input_tokens * pricing[0] + output_tokens * pricing[1]


def get_deepeval_cost_kwargs(model: str) -> dict:
    """Return ``cost_per_input_token`` / ``cost_per_output_token`` kwargs
    suitable for ``deepeval.models.llms.openai_model.GPTModel``."""
    pricing = _resolve_pricing(model)
    if pricing is None:
        return {}
    return {
        "cost_per_input_token": pricing[0],
        "cost_per_output_token": pricing[1],
    }
