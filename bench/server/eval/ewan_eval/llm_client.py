"""Lightweight LLM client for evaluation judges.

Replaces deepeval.models.llms.openai_model.GPTModel with direct OpenAI SDK
calls.  All calls go through OpenRouter (OpenAI-compatible API).

Interface contract — every model object exposes:
    async a_generate(prompt: str) -> tuple[str, float]
    get_model_name() -> str
"""

import logging
import math
import os

from openai import AsyncOpenAI

log = logging.getLogger(__name__)

# Re-export for convenience
from server.eval.ewan_eval._scoring_utils import (  # noqa: F401
    extract_json_from_response,
    normalize_10pt,
)


class EwanLLMClient:
    """OpenRouter-backed LLM client for evaluation judges.

    Drop-in replacement for ``GPTModel`` — same ``a_generate`` signature
    returning ``(text, cost)`` tuple.
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        cost_per_input_token: float = 0.0,
        cost_per_output_token: float = 0.0,
    ):
        self.model = model
        self.temperature = temperature
        self._cpi = cost_per_input_token
        self._cpo = cost_per_output_token
        self._api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        self._base_url = (
            base_url
            or os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        ).rstrip("/")
        self._client: AsyncOpenAI | None = None

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self._api_key,
                base_url=self._base_url,
                timeout=120.0,
            )
        return self._client

    async def a_generate(
        self, prompt: str, schema=None, images: list[dict] | None = None
    ) -> tuple[str, float]:
        """Generate text completion. Returns (text, cost).

        When *images* is provided, constructs multimodal content blocks
        using the OpenAI ``image_url`` format (base64 data URI).
        """
        if images:
            content: list[dict] = [{"type": "text", "text": prompt}]
            for img in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{img['media_type']};base64,{img['data']}"
                        },
                    }
                )
            messages = [{"role": "user", "content": content}]
        else:
            messages = [{"role": "user", "content": prompt}]
        client = self._get_client()
        completion = await client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
        )
        text = completion.choices[0].message.content or ""
        usage = completion.usage
        cost = 0.0
        if usage:
            cost = usage.prompt_tokens * self._cpi + usage.completion_tokens * self._cpo
        return text, cost

    async def a_generate_raw(
        self, prompt: str, top_logprobs: int = 5
    ) -> tuple[object, float]:
        """Generate with raw ChatCompletion object (for logprobs).

        Falls back to plain call if the model rejects logprobs
        (e.g. reasoning models like GPT-5.2).
        """
        client = self._get_client()
        try:
            completion = await client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                logprobs=True,
                top_logprobs=top_logprobs,
            )
        except Exception as e:
            msg = str(e).lower()
            if "logprobs" in msg or "unsupported" in msg or "include" in msg:
                log.debug("logprobs not supported for %s, retrying without", self.model)
                completion = await client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=self.temperature,
                )
            else:
                raise
        usage = completion.usage
        cost = 0.0
        if usage:
            cost = usage.prompt_tokens * self._cpi + usage.completion_tokens * self._cpo
        return completion, cost

    def generate(
        self, prompt: str, schema=None, images: list[dict] | None = None
    ) -> tuple[str, float]:
        """Synchronous wrapper for a_generate."""
        import asyncio
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run, self.a_generate(prompt, schema, images=images)
            ).result()

    def get_model_name(self) -> str:
        return self.model

    def __repr__(self) -> str:
        return f"EwanLLMClient({self.model!r})"


def calculate_weighted_score(completion) -> float | None:
    """Calculate logprobs-weighted score from a raw ChatCompletion.

    Replicates DeepEval's ``calculate_weighted_summed_score`` exactly:
    - Scans top_logprobs for numeric tokens in [0, 10]
    - Filters tokens with < 1% probability (logprob < -4.605)
    - Returns weighted average, or None if logprobs unavailable.
    """
    try:
        logprobs_content = completion.choices[0].logprobs
        if logprobs_content is None:
            return None
        logprobs_list = logprobs_content.content
        if not logprobs_list:
            return None
        # Scan from the end for the score token
        for token_info in reversed(logprobs_list):
            tops = token_info.top_logprobs
            if not tops:
                continue
            scores: dict[float, float] = {}
            for lp in tops:
                try:
                    val = float(lp.token.strip())
                    if 0 <= val <= 10 and lp.logprob >= -4.605:
                        scores[val] = math.exp(lp.logprob)
                except (ValueError, TypeError):
                    continue
            if scores:
                total_prob = sum(scores.values())
                return sum(s * p for s, p in scores.items()) / total_prob
    except (AttributeError, IndexError):
        pass
    return None
