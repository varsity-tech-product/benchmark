"""Model resolution — DeepEval-free version.

Replaces ``config.model_resolver.resolve_deepeval_model`` with
``resolve_ewan_model`` that returns ``EwanLLMClient`` or
``OAuthAnthropicModel`` directly — no DeepEval classes involved.

Resolution order (identical to legacy):
    1. Anthropic models + EVAL_USE_OAUTH → OAuthAnthropicModel (direct API)
    2. Any model + OPENROUTER_API_KEY → EwanLLMClient (OpenRouter)
    3. Fallback → plain model name string
"""

import logging
import os
import random

from config.llm_config import (
    EVAL_DEFAULT_MODELS,
    EVAL_JUDGE_TEMPERATURE,
    EVAL_USE_OAUTH,
    OAUTH_BETA_HEADER,
    OPENROUTER_BASE_URL,
)
from config.pricing import MODEL_PRICING

from evaluation.ewan_eval.llm_client import EwanLLMClient

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# OAuth wrapper for Anthropic direct calls
# ──────────────────────────────────────────────────────────────


class OAuthAnthropicModel:
    """Anthropic API via OAuth token — no DeepEval base class.

    Interface matches ``EwanLLMClient``: ``a_generate(prompt) → (text, cost)``.
    """

    def __init__(
        self,
        model: str,
        auth_token: str,
        cost_per_input_token: float = 0.0,
        cost_per_output_token: float = 0.0,
    ):
        self.model = model
        self._auth_token = auth_token
        self._cpi = cost_per_input_token
        self._cpo = cost_per_output_token

    async def a_generate(self, prompt: str, schema=None) -> tuple[str, float]:
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
            _check_rate_limit(resp.headers)
            resp.raise_for_status()
            data = resp.json()

        text = data["content"][0]["text"]
        usage = data.get("usage", {})
        cost = (
            usage.get("input_tokens", 0) * self._cpi
            + usage.get("output_tokens", 0) * self._cpo
        )
        return text, cost

    async def a_generate_raw(self, prompt: str, top_logprobs: int = 5):
        """OAuth models don't support logprobs — fall back to plain call."""
        text, cost = await self.a_generate(prompt)
        # Return a minimal duck-typed object matching ChatCompletion shape
        return _FakeCompletion(text), cost

    def generate(self, prompt: str, schema=None) -> tuple[str, float]:
        import asyncio
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self.a_generate(prompt, schema)).result()

    def get_model_name(self) -> str:
        return self.model

    def __repr__(self) -> str:
        return f"OAuthAnthropicModel({self.model!r})"


class _FakeCompletion:
    """Minimal stand-in for ChatCompletion when logprobs aren't available."""

    def __init__(self, text: str):
        self.choices = [_FakeChoice(text)]


class _FakeChoice:
    def __init__(self, text: str):
        self.message = _FakeMessage(text)
        self.logprobs = None


class _FakeMessage:
    def __init__(self, text: str):
        self.content = text


_RATE_LIMIT_WARNED: set[str] = set()
_WINDOW_KEY = {"five_hour": "5h", "seven_day": "7d"}


def _check_rate_limit(headers) -> None:
    """Log a warning when OAuth rate-limit utilization is high."""
    status = headers.get("anthropic-ratelimit-unified-status", "")
    if status in ("allowed_warning", "throttled"):
        claim = headers.get("anthropic-ratelimit-unified-representative-claim", "")
        hdr_key = _WINDOW_KEY.get(claim, claim)
        util = headers.get(f"anthropic-ratelimit-unified-{hdr_key}-utilization", "?")
        key = f"{hdr_key}:{util}"
        if key not in _RATE_LIMIT_WARNED:
            _RATE_LIMIT_WARNED.add(key)
            pct = f"{float(util) * 100:.0f}%" if util != "?" else "?"
            level = "THROTTLED" if status == "throttled" else "WARNING"
            log.warning(
                "OAuth rate limit %s — %s window at %s utilization.",
                level,
                hdr_key,
                pct,
            )


def _or_to_anthropic_native(or_name: str) -> str:
    """``anthropic/claude-sonnet-4.6`` → ``claude-sonnet-4-6``"""
    name = or_name.split("/", 1)[1] if "/" in or_name else or_name
    return name.replace(".", "-")


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────


def resolve_ewan_model(
    model=None,
    *,
    skip_oauth: bool = False,
    temperature: float = EVAL_JUDGE_TEMPERATURE,
):
    """Resolve a model name to an LLM client object.

    Drop-in replacement for ``config.model_resolver.resolve_deepeval_model``.
    Returns ``OAuthAnthropicModel`` or ``EwanLLMClient`` — both expose
    ``a_generate(prompt) → (text, cost)``.
    """
    if model is not None and not isinstance(model, (str, list)):
        return model  # already a client object

    if isinstance(model, list):
        model = random.choice(model)
    elif model is None:
        model = random.choice(EVAL_DEFAULT_MODELS)

    # ── Step 1: OAuth direct for Anthropic models ──
    if (
        not skip_oauth
        and EVAL_USE_OAUTH
        and isinstance(model, str)
        and model.startswith("anthropic/")
    ):
        from config.auth import get_oauth_token

        oauth_token = get_oauth_token()
        if oauth_token:
            native = _or_to_anthropic_native(model)
            pricing = MODEL_PRICING.get(model, (0.0, 0.0))
            log.debug("OAuth direct: %s → %s", model, native)
            return OAuthAnthropicModel(
                model=native,
                auth_token=oauth_token,
                cost_per_input_token=pricing[0],
                cost_per_output_token=pricing[1],
            )

    # ── Step 2: OpenRouter ──
    openrouter_key = os.environ.get("OPENROUTER_API_KEY", "")
    if openrouter_key:
        or_model = model
        if "/" not in model:
            or_model = f"openai/{model}"
        pricing = MODEL_PRICING.get(or_model, (0.0, 0.0))
        return EwanLLMClient(
            model=or_model,
            api_key=openrouter_key,
            base_url=OPENROUTER_BASE_URL,
            temperature=temperature,
            cost_per_input_token=pricing[0],
            cost_per_output_token=pricing[1],
        )

    # ── Step 3: Fallback ──
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if "openrouter" in base_url:
        return model
    if model.startswith("openai/"):
        return model[len("openai/") :]
    return model
