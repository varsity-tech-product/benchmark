"""Single LLM call entry point for the eval pipeline.

After #122 the scoring path has three LLM consumers: the NPC user
simulator, the QR judge, and the QP judge. This module funnels every
one of them through ``LLMRunner.call()`` so that:

1. The HTTP/SDK call to the model lives in exactly one place
   (``LLMRunner._invoke``). The decoupling grep
   ``chat.completions.create`` returning a single hit is the witness.
2. Every call writes an audit record (``LLMCallRecord``) carrying
   ``call_id``, model, prompt id/version/hash, token counts, cost,
   latency, and timestamp. Storage is pluggable via ``AuditSink``;
   the default is JSONL because it is append-only, schema-free, and
   queryable with ``jq`` / ``grep`` / pandas without any migration
   tooling.

Provider abstraction (TBD-A1): v1 is single-provider OpenRouter
(OpenAI-compatible Chat Completions). The shape is ready for a
multi-provider rewrite — ``LLMRunner`` owns the adapter — but we don't
add Anthropic / local adapters until a second consumer needs them.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from eval.llm_config import (
    create_openrouter_async_client,
    get_openrouter_base_url,
)


@dataclass
class LLMCallRecord:
    """One audit-log row per LLM call."""

    call_id: str
    model_id: str
    prompt_id: str
    prompt_version: str
    prompt_hash: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    latency_ms: float
    ts: str
    success: bool = True
    error: str | None = None


@dataclass
class LLMResponse:
    """What ``LLMRunner.call()`` returns to the caller."""

    text: str
    record: LLMCallRecord


class AuditSink(Protocol):
    def write(self, record: LLMCallRecord) -> None: ...


class NullAuditSink:
    """Audit sink that drops everything. Used when no log path is set."""

    def write(self, record: LLMCallRecord) -> None:  # noqa: D401
        return


class JsonlAuditSink:
    """Append-only JSONL audit log.

    Thread-safe: each ``write()`` takes a process-local lock and writes
    one line atomically. Grep / jq / pandas can read it directly:

        cat audit.jsonl | jq 'select(.model_id | startswith("anthropic/"))'
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, record: LLMCallRecord) -> None:
        line = json.dumps(asdict(record), ensure_ascii=False)
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")


class LLMRunner:
    """Single OpenRouter-backed entry point for eval LLM calls.

    Every call funnels through :meth:`call`, which dispatches to
    :meth:`_invoke` (the only place that talks to the OpenAI client).
    Subclasses that want to swap providers replace ``_invoke``.
    """

    def __init__(
        self,
        *,
        audit_sink: AuditSink | None = None,
        api_key: str | None = None,
        base_url: str | None = None,
    ):
        self.audit_sink = audit_sink or NullAuditSink()
        self._api_key = api_key
        self._base_url = (base_url or get_openrouter_base_url()).rstrip("/")
        self._client: Any | None = None
        self._client_lock = threading.Lock()

    def _get_client(self) -> Any:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = create_openrouter_async_client(
                        api_key=self._api_key, base_url=self._base_url
                    )
        return self._client

    async def _invoke(
        self,
        *,
        model_id: str,
        messages: list[dict[str, Any]],
        temperature: float,
    ) -> tuple[str, int, int]:
        """Provider-specific call. Returns (text, tokens_in, tokens_out).

        Single seam for swapping providers; everything else in
        :meth:`call` is provider-agnostic.
        """
        completion = await self._get_client().chat.completions.create(
            model=model_id,
            messages=messages,
            temperature=temperature,
        )
        text = completion.choices[0].message.content or ""
        usage = completion.usage
        tokens_in = int(getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        tokens_out = int(getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        return text, tokens_in, tokens_out

    async def call(
        self,
        *,
        call_id: str,
        model_id: str,
        messages: list[dict[str, Any]],
        prompt_id: str = "",
        prompt_version: str = "",
        temperature: float = 0.0,
        cost_per_input_token: float = 0.0,
        cost_per_output_token: float = 0.0,
    ) -> LLMResponse:
        """Run one LLM call and emit one audit record.

        ``call_id`` is caller-supplied (typically ``"<purpose>-<uuid>"``);
        the audit log is keyed by it. ``prompt_id`` / ``prompt_version``
        identify which rendered prompt template ran; ``prompt_hash`` is
        sha256 of the canonical messages so callers can verify identity
        of a specific call without keeping the raw prompt around.
        """
        prompt_hash = _hash_messages(messages)
        ts = _utc_now()
        started = time.monotonic()
        try:
            text, tokens_in, tokens_out = await self._invoke(
                model_id=model_id,
                messages=messages,
                temperature=temperature,
            )
        except Exception as exc:  # noqa: BLE001
            record = LLMCallRecord(
                call_id=call_id,
                model_id=model_id,
                prompt_id=prompt_id,
                prompt_version=prompt_version,
                prompt_hash=prompt_hash,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0.0,
                latency_ms=(time.monotonic() - started) * 1000,
                ts=ts,
                success=False,
                error=str(exc),
            )
            self.audit_sink.write(record)
            raise

        cost_usd = round(
            tokens_in * cost_per_input_token + tokens_out * cost_per_output_token,
            8,
        )
        record = LLMCallRecord(
            call_id=call_id,
            model_id=model_id,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            prompt_hash=prompt_hash,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            latency_ms=(time.monotonic() - started) * 1000,
            ts=ts,
        )
        self.audit_sink.write(record)
        return LLMResponse(text=text, record=record)


def _hash_messages(messages: list[dict[str, Any]]) -> str:
    """sha256 of the canonical JSON serialization of ``messages``."""
    canonical = json.dumps(
        messages, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


_DEFAULT_RUNNER: LLMRunner | None = None
_DEFAULT_LOCK = threading.Lock()


def default_runner() -> LLMRunner:
    """Module-level runner with a sink chosen from the environment.

    ``QTB_AUDIT_LOG`` env var → JSONL path; unset → :class:`NullAuditSink`.
    """
    global _DEFAULT_RUNNER
    if _DEFAULT_RUNNER is None:
        with _DEFAULT_LOCK:
            if _DEFAULT_RUNNER is None:
                audit_path = os.environ.get("QTB_AUDIT_LOG", "").strip()
                sink: AuditSink = (
                    JsonlAuditSink(audit_path) if audit_path else NullAuditSink()
                )
                _DEFAULT_RUNNER = LLMRunner(audit_sink=sink)
    return _DEFAULT_RUNNER


def reset_default_runner_for_tests() -> None:
    """Test helper: drop the cached default runner."""
    global _DEFAULT_RUNNER
    _DEFAULT_RUNNER = None
