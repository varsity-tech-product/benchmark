"""Lightweight LLM client wrapper for evaluation judges.

Thin shim that preserves the existing
``async a_generate(prompt) -> (text, cost)`` interface while delegating
the actual call (and audit logging) to :mod:`eval.llm.runner`. The HTTP
call lives in exactly one place — ``LLMRunner._invoke`` — so the
``chat.completions.create`` grep returns a single hit.

Slice 6 deliberately does not propagate ``call_id`` / ``prompt_id`` /
``prompt_version`` from the caller; slice 7 lifts those into the three
call sites (NPC, QR judge, QP judge) for richer audit metadata.
"""

import uuid

from eval.llm.runner import LLMRunner, default_runner

# Re-export for convenience
from eval.judges.runtime.scoring_utils import (  # noqa: F401
    extract_json_from_response,
)


class EwanLLMClient:
    """OpenRouter-backed LLM client for evaluation judges.

    Exposes ``a_generate`` returning ``(text, cost)`` tuple, backed by
    :class:`eval.llm.runner.LLMRunner`. Pass ``runner=`` to override the
    module-level default (e.g. in tests, or to attach a job-scoped audit
    sink).
    """

    def __init__(
        self,
        model: str,
        api_key: str | None = None,
        base_url: str | None = None,
        temperature: float = 0.0,
        cost_per_input_token: float = 0.0,
        cost_per_output_token: float = 0.0,
        runner: LLMRunner | None = None,
    ):
        self.model = model
        self.temperature = temperature
        self._cpi = cost_per_input_token
        self._cpo = cost_per_output_token
        # Per-client connection overrides build a dedicated runner so the
        # caller's api_key/base_url are honored. The dedicated runner shares
        # the default's audit sink so experiment-scoped clients still emit
        # audit records to the same log.
        if runner is None and (api_key is not None or base_url is not None):
            runner = LLMRunner(
                audit_sink=default_runner().audit_sink,
                api_key=api_key,
                base_url=base_url,
            )
        self._runner = runner

    @property
    def runner(self) -> LLMRunner:
        """Lazily resolved so tests that swap default_runner see it."""
        return self._runner or default_runner()

    async def a_generate(
        self,
        prompt: str,
        schema=None,
        images: list[dict] | None = None,
        *,
        call_id: str | None = None,
        prompt_id: str = "",
        prompt_version: str = "",
    ) -> tuple[str, float]:
        """Generate text completion. Returns ``(text, cost)``.

        When *images* is provided, constructs multimodal content blocks
        using the OpenAI ``image_url`` format (base64 data URI).

        ``call_id`` / ``prompt_id`` / ``prompt_version`` are forwarded to
        the audit log. Pass them from the call site (judge dimension or
        student-sim turn) so audit rows are attributable; defaults
        produce an opaque ``ewan-<uuid>`` row.
        """
        messages = _build_messages(prompt, images)
        response = await self.runner.call(
            call_id=call_id or f"ewan-{uuid.uuid4().hex[:12]}",
            model_id=self.model,
            messages=messages,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            temperature=self.temperature,
            cost_per_input_token=self._cpi,
            cost_per_output_token=self._cpo,
        )
        return response.text, response.record.cost_usd

    def generate(
        self,
        prompt: str,
        schema=None,
        images: list[dict] | None = None,
        *,
        call_id: str | None = None,
        prompt_id: str = "",
        prompt_version: str = "",
    ) -> tuple[str, float]:
        """Synchronous wrapper for a_generate."""
        import asyncio
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(
                asyncio.run,
                self.a_generate(
                    prompt,
                    schema,
                    images=images,
                    call_id=call_id,
                    prompt_id=prompt_id,
                    prompt_version=prompt_version,
                ),
            ).result()

    def get_model_name(self) -> str:
        return self.model

    def __repr__(self) -> str:
        return f"EwanLLMClient({self.model!r})"


def _build_messages(prompt: str, images: list[dict] | None) -> list[dict]:
    if not images:
        return [{"role": "user", "content": prompt}]
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
    return [{"role": "user", "content": content}]
