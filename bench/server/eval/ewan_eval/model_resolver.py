"""Model resolution — DeepEval-free, server-scoped.

Drop-in replacement for ``server.config.model_resolver.resolve_deepeval_model``.
Returns ``EwanLLMClient`` or ``OAuthAnthropicModel`` — no DeepEval classes.

Public API:
    resolve_ewan_model(model) → client object with a_generate(prompt) → (text, cost)
"""

import logging
import os
import random

from server.config.llm_config import (
    EVAL_DEFAULT_MODELS,
    EVAL_JUDGE_TEMPERATURE,
    OPENROUTER_BASE_URL,
)

# OAuth settings — may not be present in server llm_config yet
EVAL_USE_OAUTH = getattr(
    __import__("server.config.llm_config", fromlist=["EVAL_USE_OAUTH"]),
    "EVAL_USE_OAUTH",
    False,
)
OAUTH_BETA_HEADER = getattr(
    __import__("server.config.llm_config", fromlist=["OAUTH_BETA_HEADER"]),
    "OAUTH_BETA_HEADER",
    "oauth-2025-04-20",
)
from server.config.pricing import MODEL_PRICING
from server.eval.ewan_eval.llm_client import EwanLLMClient

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# OAuth wrapper for Anthropic direct calls
# ──────────────────────────────────────────────────────────────


class OAuthAnthropicModel:
    """Anthropic API via OAuth token — no DeepEval base class."""

    def __init__(
        self, model, auth_token, cost_per_input_token=0.0, cost_per_output_token=0.0
    ):
        self.model = model
        self._auth_token = auth_token
        self._cpi = cost_per_input_token
        self._cpo = cost_per_output_token

    async def a_generate(self, prompt, schema=None):
        import httpx

        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "Authorization": f"Bearer {self._auth_token}",
                    "anthropic-version": "2023-06-01",
                    "anthropic-beta": OAUTH_BETA_HEADER,
                    "content-type": "application/json",
                },
                json={
                    "model": self.model,
                    "max_tokens": 8192,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
        text = data["content"][0]["text"]
        usage = data.get("usage", {})
        cost = (
            usage.get("input_tokens", 0) * self._cpi
            + usage.get("output_tokens", 0) * self._cpo
        )
        return text, cost

    async def a_generate_raw(self, prompt, top_logprobs=5):
        text, cost = await self.a_generate(prompt)
        return _FakeCompletion(text), cost

    def generate(self, prompt, schema=None):
        import asyncio
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self.a_generate(prompt, schema)).result()

    def get_model_name(self):
        return self.model


class _FakeCompletion:
    def __init__(self, text):
        self.choices = [
            type(
                "C",
                (),
                {"message": type("M", (), {"content": text})(), "logprobs": None},
            )()
        ]


def _or_to_anthropic_native(or_name):
    name = or_name.split("/", 1)[1] if "/" in or_name else or_name
    return name.replace(".", "-")


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────


def resolve_ewan_model(
    model=None, *, skip_oauth=False, temperature=EVAL_JUDGE_TEMPERATURE
):
    """Resolve model name to LLM client object.

    Drop-in for ``resolve_deepeval_model`` — same resolution order.
    """
    if model is not None and not isinstance(model, (str, list)):
        return model

    if isinstance(model, list):
        model = random.choice(model)
    elif model is None:
        model = random.choice(EVAL_DEFAULT_MODELS)

    # Step 1: OAuth direct
    if (
        not skip_oauth
        and EVAL_USE_OAUTH
        and isinstance(model, str)
        and model.startswith("anthropic/")
    ):
        try:
            from server.config.auth import get_oauth_token
        except ImportError:
            get_oauth_token = lambda: None  # noqa: E731
        oauth_token = get_oauth_token()
        if oauth_token:
            native = _or_to_anthropic_native(model)
            pricing = MODEL_PRICING.get(model, (0.0, 0.0))
            return OAuthAnthropicModel(
                model=native,
                auth_token=oauth_token,
                cost_per_input_token=pricing[0],
                cost_per_output_token=pricing[1],
            )

    # Step 2: OpenRouter
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        or_model = model if "/" in model else f"openai/{model}"
        pricing = MODEL_PRICING.get(or_model, (0.0, 0.0))
        return EwanLLMClient(
            model=or_model,
            api_key=openrouter_key,
            base_url=OPENROUTER_BASE_URL,
            temperature=temperature,
            cost_per_input_token=pricing[0],
            cost_per_output_token=pricing[1],
        )

    # Step 3: Fallback
    if model.startswith("openai/"):
        return model[len("openai/") :]
    return model


def require_ewan_model(
    model=None,
    *,
    purpose: str = "LLM component",
    skip_oauth: bool = False,
    temperature=EVAL_JUDGE_TEMPERATURE,
):
    """Resolve a model and require a concrete callable client object."""
    resolved = resolve_ewan_model(
        model,
        skip_oauth=skip_oauth,
        temperature=temperature,
    )
    if hasattr(resolved, "generate"):
        return resolved

    model_name = model or resolved or "default"
    raise RuntimeError(
        f"Unable to initialize the {purpose} model ({model_name}). "
        "No usable model client was configured. Set OPENROUTER_API_KEY in "
        "the server environment or choose a simulator model backed by a "
        "configured provider."
    )


# Backward-compat alias
resolve_deepeval_model = resolve_ewan_model
