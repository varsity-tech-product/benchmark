"""Tests for the unified LLMRunner + audit sinks."""

from __future__ import annotations

import asyncio
import json
import threading
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from eval.llm.runner import (
    JsonlAuditSink,
    LLMCallRecord,
    LLMResponse,
    LLMRunner,
    NullAuditSink,
    _hash_messages,
    default_runner,
    reset_default_runner_for_tests,
)


def _fake_completion(text: str, prompt_tokens: int, completion_tokens: int):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        ),
    )


def _patched_runner(*, completion=None, raise_exc: Exception | None = None) -> LLMRunner:
    runner = LLMRunner(audit_sink=NullAuditSink())
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    side_effect=raise_exc,
                    return_value=completion,
                )
            )
        )
    )
    runner._client = fake_client
    return runner


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# JsonlAuditSink
# ---------------------------------------------------------------------------


def _record(**overrides) -> LLMCallRecord:
    base = dict(
        call_id="c1",
        model_id="anthropic/claude-haiku-4.5",
        prompt_id="qr.result_judge",
        prompt_version="1",
        prompt_hash="deadbeef",
        tokens_in=100,
        tokens_out=200,
        cost_usd=0.0001,
        latency_ms=42.0,
        ts="2026-05-02T12:00:00Z",
    )
    base.update(overrides)
    return LLMCallRecord(**base)


def test_jsonl_audit_sink_appends_one_line_per_call(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    sink.write(_record(call_id="c1"))
    sink.write(_record(call_id="c2"))

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rows = [json.loads(line) for line in lines]
    assert [r["call_id"] for r in rows] == ["c1", "c2"]


def test_jsonl_audit_sink_creates_parent_directory(tmp_path):
    target = tmp_path / "nested" / "deeper" / "audit.jsonl"
    sink = JsonlAuditSink(target)
    sink.write(_record())
    assert target.exists()


def test_jsonl_audit_sink_concurrent_writes_dont_interleave(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    barrier = threading.Barrier(8)

    def hit():
        barrier.wait()
        for i in range(50):
            sink.write(_record(call_id=f"{threading.get_ident()}-{i}"))

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 8 * 50
    # Every line must be valid JSON — no torn writes.
    for line in lines:
        json.loads(line)


def test_null_audit_sink_is_silent():
    NullAuditSink().write(_record())  # no exception, no side effect


# ---------------------------------------------------------------------------
# _hash_messages
# ---------------------------------------------------------------------------


def test_hash_messages_is_deterministic():
    msgs = [{"role": "user", "content": "hi"}]
    assert _hash_messages(msgs) == _hash_messages(msgs)


def test_hash_messages_distinguishes_payloads():
    a = [{"role": "user", "content": "hi"}]
    b = [{"role": "user", "content": "ho"}]
    assert _hash_messages(a) != _hash_messages(b)


def test_hash_messages_is_key_order_insensitive():
    a = [{"role": "user", "content": "hi"}]
    b = [{"content": "hi", "role": "user"}]
    assert _hash_messages(a) == _hash_messages(b)


# ---------------------------------------------------------------------------
# LLMRunner.call
# ---------------------------------------------------------------------------


def test_call_returns_text_and_token_counts_and_cost():
    runner = _patched_runner(
        completion=_fake_completion("hello world", prompt_tokens=10, completion_tokens=20)
    )
    response: LLMResponse = _run(
        runner.call(
            call_id="c-1",
            model_id="m",
            messages=[{"role": "user", "content": "hi"}],
            cost_per_input_token=0.001,
            cost_per_output_token=0.002,
        )
    )
    assert response.text == "hello world"
    assert response.record.tokens_in == 10
    assert response.record.tokens_out == 20
    assert response.record.cost_usd == round(10 * 0.001 + 20 * 0.002, 8)
    assert response.record.success is True
    assert response.record.error is None
    assert response.record.latency_ms >= 0


def test_call_writes_audit_record_on_success(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    runner = _patched_runner(
        completion=_fake_completion("ok", prompt_tokens=1, completion_tokens=2)
    )
    runner.audit_sink = sink

    _run(
        runner.call(
            call_id="c-1",
            model_id="m",
            messages=[{"role": "user", "content": "hi"}],
            prompt_id="qr.result_judge",
            prompt_version="2",
        )
    )

    rows = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["call_id"] == "c-1"
    assert rows[0]["prompt_id"] == "qr.result_judge"
    assert rows[0]["prompt_version"] == "2"
    assert rows[0]["success"] is True


def test_call_writes_audit_record_on_failure_and_re_raises(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    runner = _patched_runner(raise_exc=RuntimeError("upstream 500"))
    runner.audit_sink = sink

    with pytest.raises(RuntimeError, match="upstream 500"):
        _run(
            runner.call(
                call_id="c-fail",
                model_id="m",
                messages=[{"role": "user", "content": "hi"}],
            )
        )

    rows = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["success"] is False
    assert "upstream 500" in rows[0]["error"]


def test_call_uses_default_temperature_zero():
    """Non-determinism would break cost-stable scoring."""
    runner = _patched_runner(
        completion=_fake_completion("ok", prompt_tokens=1, completion_tokens=1)
    )
    _run(
        runner.call(
            call_id="c", model_id="m", messages=[{"role": "user", "content": "hi"}]
        )
    )
    create = runner._client.chat.completions.create
    assert create.call_args.kwargs["temperature"] == 0.0


def test_call_handles_missing_usage_gracefully():
    completion = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=None,
    )
    runner = _patched_runner(completion=completion)
    response = _run(
        runner.call(
            call_id="c",
            model_id="m",
            messages=[{"role": "user", "content": "hi"}],
            cost_per_input_token=0.01,
            cost_per_output_token=0.02,
        )
    )
    assert response.record.tokens_in == 0
    assert response.record.tokens_out == 0
    assert response.record.cost_usd == 0.0


# ---------------------------------------------------------------------------
# default_runner / env wiring
# ---------------------------------------------------------------------------


def test_default_runner_uses_jsonl_when_env_set(tmp_path, monkeypatch):
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("QTB_AUDIT_LOG", str(audit))
    reset_default_runner_for_tests()
    runner = default_runner()
    assert isinstance(runner.audit_sink, JsonlAuditSink)
    assert runner.audit_sink.path == audit


def test_default_runner_uses_null_when_env_unset(monkeypatch):
    monkeypatch.delenv("QTB_AUDIT_LOG", raising=False)
    reset_default_runner_for_tests()
    runner = default_runner()
    assert isinstance(runner.audit_sink, NullAuditSink)


def test_default_runner_caches_instance(monkeypatch):
    monkeypatch.delenv("QTB_AUDIT_LOG", raising=False)
    reset_default_runner_for_tests()
    a = default_runner()
    b = default_runner()
    assert a is b


# ---------------------------------------------------------------------------
# LLMCallRecord shape
# ---------------------------------------------------------------------------


def test_ewan_client_with_api_key_builds_dedicated_runner(monkeypatch):
    """Codex slice-6 round-1 P2: per-client api_key/base_url must reach
    the runner that actually invokes the model — not be silently dropped."""
    from eval.judges.runtime.llm_client import EwanLLMClient

    monkeypatch.delenv("QTB_AUDIT_LOG", raising=False)
    reset_default_runner_for_tests()

    client = EwanLLMClient(
        model="m",
        api_key="caller-key",
        base_url="http://internal.test/openrouter",
    )
    assert client.runner is not default_runner()
    assert client.runner._api_key == "caller-key"
    assert client.runner._base_url == "http://internal.test/openrouter"


def test_ewan_client_without_overrides_uses_default_runner(monkeypatch):
    from eval.judges.runtime.llm_client import EwanLLMClient

    monkeypatch.delenv("QTB_AUDIT_LOG", raising=False)
    reset_default_runner_for_tests()
    client = EwanLLMClient(model="m")
    assert client.runner is default_runner()


def test_ewan_client_dedicated_runner_shares_default_audit_sink(monkeypatch, tmp_path):
    """Caller-supplied api_key/base_url must not silence audit logging."""
    from eval.judges.runtime.llm_client import EwanLLMClient

    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("QTB_AUDIT_LOG", str(audit))
    reset_default_runner_for_tests()

    client = EwanLLMClient(model="m", api_key="caller-key")
    assert client.runner.audit_sink is default_runner().audit_sink


def test_llm_call_record_serializes_to_audit_keys():
    record = _record()
    keys = set(asdict(record).keys())
    assert keys == {
        "call_id",
        "model_id",
        "prompt_id",
        "prompt_version",
        "prompt_hash",
        "tokens_in",
        "tokens_out",
        "cost_usd",
        "latency_ms",
        "ts",
        "success",
        "error",
    }
