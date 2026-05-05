"""Telemetry primitives shared by plugins, loaders, and runtimes."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import TracebackType
from typing import Any, Protocol


@dataclass(frozen=True)
class TelemetryRecord:
    """One platform telemetry event."""

    namespace: str
    event: str
    latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    count: int = 1
    success: bool = True
    error: str | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


class TelemetryHook(Protocol):
    """Push sink for platform telemetry."""

    def emit(self, record: TelemetryRecord) -> None:
        """Receive one telemetry record."""


class NullTelemetryHook:
    """Telemetry sink that drops records."""

    def emit(self, record: TelemetryRecord) -> None:
        return None


class InMemoryTelemetryHook:
    """Telemetry sink for tests and local diagnostics."""

    def __init__(self) -> None:
        self.records: list[TelemetryRecord] = []

    def emit(self, record: TelemetryRecord) -> None:
        self.records.append(record)

    def totals(self) -> dict[str, float]:
        """Return aggregate counters across all records."""

        return {
            "count": float(sum(record.count for record in self.records)),
            "input_tokens": float(sum(record.input_tokens for record in self.records)),
            "output_tokens": float(
                sum(record.output_tokens for record in self.records)
            ),
            "cost_usd": float(sum(record.cost_usd for record in self.records)),
            "latency_ms": float(sum(record.latency_ms for record in self.records)),
            "errors": float(sum(1 for record in self.records if not record.success)),
        }


class TelemetryTimer:
    """Context manager that emits latency and error telemetry."""

    def __init__(
        self,
        hook: TelemetryHook | None,
        namespace: str,
        event: str,
        *,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self._hook = hook or NullTelemetryHook()
        self._namespace = namespace
        self._event = event
        self._attributes = dict(attributes or {})
        self._start = 0.0

    def __enter__(self) -> "TelemetryTimer":
        self._start = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        latency_ms = (time.perf_counter() - self._start) * 1000
        self._hook.emit(
            TelemetryRecord(
                namespace=self._namespace,
                event=self._event,
                latency_ms=latency_ms,
                success=exc is None,
                error=f"{type(exc).__name__}: {exc}" if exc else None,
                attributes=self._attributes,
            )
        )
        return False


def emit_telemetry(
    hook: TelemetryHook | None,
    *,
    namespace: str,
    event: str,
    latency_ms: float = 0.0,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cost_usd: float = 0.0,
    count: int = 1,
    success: bool = True,
    error: str | None = None,
    attributes: dict[str, Any] | None = None,
) -> None:
    """Emit one telemetry record through a possibly empty hook."""

    (hook or NullTelemetryHook()).emit(
        TelemetryRecord(
            namespace=namespace,
            event=event,
            latency_ms=latency_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            count=count,
            success=success,
            error=error,
            attributes=dict(attributes or {}),
        )
    )
