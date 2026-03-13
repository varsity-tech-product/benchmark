"""Model resolution utilities for DeepEval and agent adapters."""

import logging
import os
import random

from deepeval.models.base_model import DeepEvalBaseLLM

from config.llm_config import (
    AGENT_DEFAULT_MODEL,
    AGENT_MODEL_MAP,
    EVAL_DEFAULT_MODELS,
    EVAL_USE_OAUTH,
    OAUTH_BETA_HEADER,
    OPENROUTER_BASE_URL,
)
from config.pricing import MODEL_PRICING, get_deepeval_cost_kwargs

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# OAuth wrapper for Anthropic direct calls (Claude Max plan)
# ---------------------------------------------------------------------------


class _OAuthAnthropicModel(DeepEvalBaseLLM):
    """Model that calls Anthropic API via OAuth token.

    Inherits from ``DeepEvalBaseLLM`` so it passes type checks in
    DeepEval components (ConversationalGEval, metrics, etc.).

    Implements the ``a_generate(prompt) -> (text, cost)`` interface expected
    by all evaluation dimensions (result_judge, process_metrics, etc.).

    Requires ``anthropic-beta: oauth-2025-04-20`` header to authenticate.
    """

    def __init__(
        self,
        model: str,
        auth_token: str,
        cost_per_input_token: float = 0.0,
        cost_per_output_token: float = 0.0,
    ):
        # Skip DeepEvalBaseLLM.__init__ — it calls load_model() and
        # parse_model_name() which don't apply to our OAuth flow.
        self._model_name = model
        self.model = model
        self._auth_token = auth_token
        self._cpi = cost_per_input_token
        self._cpo = cost_per_output_token

    def load_model(self):
        return self

    def get_model_name(self):
        return self._model_name

    def generate(self, prompt, schema=None):
        import asyncio
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self.a_generate(prompt, schema)).result()

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
                    "model": self._model_name,
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

    def __repr__(self):
        return f"_OAuthAnthropicModel({self._model_name!r})"


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
                "OAuth rate limit %s — %s window at %s utilization. "
                "Consider setting EVAL_USE_OAUTH=False to fall back to OpenRouter.",
                level,
                hdr_key,
                pct,
            )


def _or_to_anthropic_native(or_name: str) -> str:
    """Convert OpenRouter name to Anthropic native name.

    ``anthropic/claude-sonnet-4.6`` → ``claude-sonnet-4-6``
    """
    name = or_name.split("/", 1)[1] if "/" in or_name else or_name
    return name.replace(".", "-")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_model_for_agent(agent_type: str, use_openrouter: bool = False) -> str:
    """Get the model name for an agent type.

    Args:
        agent_type: One of "openai", "anthropic", "google", "generic".
        use_openrouter: If True, return the OpenRouter variant.
    """
    native, openrouter = AGENT_MODEL_MAP.get(
        agent_type, (AGENT_DEFAULT_MODEL, AGENT_DEFAULT_MODEL)
    )
    return openrouter if use_openrouter else native


def resolve_deepeval_model(model=None):
    """Resolve model for DeepEval components (judge, simulator).

    Resolution order (when EVAL_USE_OAUTH is True):
      1. Anthropic models → OAuth direct (if token available)
      2. Any model → OpenRouter (if key available)
      3. Plain model name string (fallback)

    When EVAL_USE_OAUTH is False, skips step 1 entirely.
    """
    if model is not None and not isinstance(model, (str, list)):
        return model

    if isinstance(model, list):
        model = random.choice(model)
    elif model is None:
        model = random.choice(EVAL_DEFAULT_MODELS)

    # ── Step 1: OAuth direct for Anthropic models ──
    if EVAL_USE_OAUTH and isinstance(model, str) and model.startswith("anthropic/"):
        oauth_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
        if oauth_token:
            native = _or_to_anthropic_native(model)
            pricing = MODEL_PRICING.get(model, (0.0, 0.0))
            log.debug("OAuth direct: %s → %s", model, native)
            return _OAuthAnthropicModel(
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
        try:
            from deepeval.models.llms.openai_model import GPTModel

            return GPTModel(
                model=or_model,
                api_key=openrouter_key,
                base_url=OPENROUTER_BASE_URL,
                **get_deepeval_cost_kwargs(or_model),
            )
        except Exception:
            pass

    # ── Step 3: Fallback ──
    base_url = os.environ.get("OPENAI_BASE_URL", "")
    if "openrouter" in base_url:
        return model

    if model.startswith("openai/"):
        return model[len("openai/") :]

    return model
