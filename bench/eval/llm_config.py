"""LLM configuration owned by the eval package.

These are the only model defaults and OpenRouter knobs the scoring
pipeline reads. Keeping them inside ``bench/eval/`` lets scoring run
in any environment that has ``bench/`` on ``sys.path`` without
pulling in ``bench/server/``.

The duplicate pieces in ``bench/server/config/llm_config.py`` are
server runtime concerns (NPC student model pool, agent transport
flags, etc.) — those stay where they are.
"""

from __future__ import annotations

import os
from typing import Any

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# --- Judge defaults ---
# Deterministic temperature so scores are reproducible across runs.
EVAL_JUDGE_TEMPERATURE = 0.0
EVAL_DEFAULT_MODELS: list[str] = [
    "anthropic/claude-haiku-4.5",
]


def get_openrouter_api_key() -> str:
    return os.environ.get("OPENROUTER_API_KEY", "").strip()


def has_openrouter_api_key() -> bool:
    return bool(get_openrouter_api_key())


def get_openrouter_base_url() -> str:
    return (
        os.environ.get("OPENROUTER_BASE_URL", OPENROUTER_BASE_URL).strip().rstrip("/")
    )


def require_openrouter_api_key(*, purpose: str = "LLM call") -> str:
    key = get_openrouter_api_key()
    if not key:
        raise RuntimeError(
            f"OPENROUTER_API_KEY not set for {purpose}. "
            "Put it in .env or the runtime environment."
        )
    return key


def create_openrouter_async_client(
    *,
    api_key: str | None = None,
    base_url: str | None = None,
    timeout: float = 120.0,
) -> Any:
    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=api_key or require_openrouter_api_key(purpose="async LLM client"),
        base_url=(base_url or get_openrouter_base_url()).rstrip("/"),
        timeout=timeout,
    )
