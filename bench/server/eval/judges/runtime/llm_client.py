"""Lightweight LLM client for evaluation judges.

All calls go through OpenRouter (OpenAI-compatible API).

Interface contract — every model object exposes:
    async a_generate(prompt: str) -> tuple[str, float]
    get_model_name() -> str
"""

from typing import Any

from server.config.llm_config import (
    create_openrouter_async_client,
    get_openrouter_base_url,
)

# Re-export for convenience
from server.eval.judges.runtime.scoring_utils import (  # noqa: F401
    extract_json_from_response,
)


class EwanLLMClient:
    """OpenRouter-backed LLM client for evaluation judges.

    Exposes ``a_generate`` returning ``(text, cost)`` tuple.
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
        self._api_key = api_key
        self._base_url = (base_url or get_openrouter_base_url()).rstrip("/")
        self._client: Any | None = None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = create_openrouter_async_client(
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
