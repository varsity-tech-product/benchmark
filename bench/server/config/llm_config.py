"""Server-scoped LLM model configuration.

Server runtime defaults and shared experiment model lists live here.
Client/orchestrator agent configuration lives in bench/config/llm_config.py.
"""

import os
from typing import Any

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
# TC_CHECKER_MODEL removed (#122 slice 5): TC is programmatic — see
# server/core/tc_checker.py.

# --- Student simulator model pool (vision-capable only) ---
# All models must support image input via OpenRouter.
# Source: https://openrouter.ai/models?supported_parameters=images
# Last verified: 2026-04-15
STUDENT_MODEL_POOL: dict[str, list[str]] = {
    # Budget tier — < $0.50/1M input tokens
    "budget": [
        "openai/gpt-5-nano",  # $0.05/1M, 400K ctx
        "google/gemini-2.0-flash-001",  # $0.10/1M, 1M ctx
        "google/gemini-2.5-flash-lite",  # $0.10/1M, 1M ctx
        "openai/gpt-4.1-nano",  # $0.10/1M, 1M ctx
        "openai/gpt-4o-mini",  # $0.15/1M, 128K ctx
        "openai/gpt-5-mini",  # $0.25/1M, 400K ctx
        "google/gemini-2.5-flash",  # $0.30/1M, 1M ctx
        "openai/gpt-4.1-mini",  # $0.40/1M, 1M ctx
    ],
    # Standard tier — $0.50–$3.00/1M input tokens
    "standard": [
        "anthropic/claude-3.5-haiku",  # $0.80/1M, 200K ctx
        "anthropic/claude-haiku-4.5",  # $1.00/1M, 200K ctx
        "google/gemini-2.5-pro",  # $1.25/1M, 1M ctx
        "openai/gpt-5",  # $1.25/1M, 400K ctx
        "openai/gpt-5.1",  # $1.25/1M, 400K ctx
        "openai/gpt-5.2",  # $1.75/1M, 400K ctx
        "openai/gpt-4.1",  # $2.00/1M, 1M ctx
        "openai/gpt-4o",  # $2.50/1M, 128K ctx
        "openai/gpt-5.4",  # $2.50/1M, 1M ctx
        "anthropic/claude-sonnet-4",  # $3.00/1M, 1M ctx
        "anthropic/claude-sonnet-4.5",  # $3.00/1M, 1M ctx
        "anthropic/claude-sonnet-4.6",  # $3.00/1M, 1M ctx
        "x-ai/grok-4",  # $3.00/1M, 256K ctx
    ],
    # Premium tier — > $3.00/1M input tokens
    "premium": [
        "anthropic/claude-opus-4.5",  # $5.00/1M, 200K ctx
        "anthropic/claude-opus-4.6",  # $5.00/1M, 1M ctx
        "openai/gpt-5-pro",  # $15.00/1M, 400K ctx
    ],
}

# Flat set for fast membership check
STUDENT_MODEL_POOL_ALL: frozenset[str] = frozenset(
    model for tier in STUDENT_MODEL_POOL.values() for model in tier
)

SIMULATOR_DEFAULT_MODEL = "openai/gpt-5.4"  # must be in STUDENT_MODEL_POOL_ALL
EVAL_DEFAULT_MODELS: list[str] = [
    "anthropic/claude-haiku-4.5",
    # "anthropic/claude-sonnet-4-6",
    # "openai/gpt-5.2",
    # "anthropic/claude-opus-4.6",
]
EVAL_DEFAULT_MODEL = EVAL_DEFAULT_MODELS[0]
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# --- Issue #83 student-simulator validation experiment ---
# This is the single model-list entrypoint for
# bench/experiments/student_sim_stability. Edit these values to change the
# three student-simulator candidates, the live tutor stimulus, or the judge
# panel used by that experiment.
STUDENT_SIM_STABILITY_STUDENT_MODELS: list[str] = [
    "openai/gpt-5.4",
    "anthropic/claude-sonnet-4-6",
    "google/gemini-3.1-pro-preview",
]
STUDENT_SIM_STABILITY_TUTOR_MODEL = "openai/gpt-4.1-nano"
STUDENT_SIM_STABILITY_JUDGE_MODELS: list[str] = [
    "anthropic/claude-sonnet-4-6",
    "openai/gpt-5.4",
    "google/gemini-3.1-pro-preview",
]
STUDENT_SIM_STABILITY_PRIMARY_JUDGE_MODEL = STUDENT_SIM_STABILITY_JUDGE_MODELS[0]


def get_openrouter_api_key() -> str:
    """Return the configured OpenRouter API key, if any."""
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def has_openrouter_api_key() -> bool:
    return bool(get_openrouter_api_key())


def get_openrouter_base_url() -> str:
    """Return the configured OpenRouter base URL."""
    return (
        os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL).strip().rstrip("/")
    )


def require_openrouter_api_key(*, purpose: str = "LLM call") -> str:
    key = get_openrouter_api_key()
    if not key:
        raise RuntimeError(
            f"OPENROUTER_API_KEY not set for {purpose}. "
            "Put it in .env or the server environment."
        )
    return key


def create_openrouter_async_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> Any:
    """Create an async OpenRouter client via the shared server LLM config."""
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=api_key or require_openrouter_api_key(purpose="async LLM client"),
        base_url=(base_url or get_openrouter_base_url()).rstrip("/"),
        timeout=timeout,
    )


def create_openrouter_sync_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> Any:
    """Create a sync OpenRouter client via the shared server LLM config."""
    import openai

    return openai.OpenAI(
        api_key=api_key or require_openrouter_api_key(purpose="sync LLM client"),
        base_url=(base_url or get_openrouter_base_url()).rstrip("/"),
        timeout=timeout,
    )


# --- Evaluation temperature ---
# Judge / TC checker / simulator temperature.  Deterministic (0.0) ensures
# reproducible scores across runs.  Agent temperature is NOT controlled here
# --- agents run at their provider's default to reflect real-world behaviour.
EVAL_JUDGE_TEMPERATURE = 0.0
