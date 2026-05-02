"""Structured evaluation output models."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class DimensionResult:
    score: float | None
    status: str
    required_for_track_score: bool
    reason: str = ""
    evidence: list[str] = field(default_factory=list)
    per_model: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    skip_reason: str | None = None
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TrackResult:
    track: str
    score: float | None
    status: str
    detail: dict[str, Any]
    blocking_missing: list[dict[str, Any]] = field(default_factory=list)
    eval_cost: float = 0.0
    eval_cost_by_model: dict[str, float] = field(default_factory=dict)
    error: str | None = None
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalOutput:
    score_id: str
    score_status: str
    qr: TrackResult | None
    qp: TrackResult | None
    overall_score: float | None
    eval_mode: str
    eval_model: str | None
    created_at: str
    completed_at: str | None
    duration_seconds: float
    judge_reliability: dict[str, Any] | None = None
    interrupted: bool = False
    blocking_missing: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    preflight: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": "2.0",
            "score_id": self.score_id,
            "score_status": self.score_status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "eval_model": self.eval_model,
            "eval_mode": self.eval_mode,
            "duration_seconds": round(self.duration_seconds, 2),
            "judge_reliability": self.judge_reliability or {},
            "interrupted": self.interrupted,
            "blocking_missing": self.blocking_missing,
            "overall_score": self.overall_score,
            "qr": self.qr.to_dict() if self.qr else None,
            "qp": self.qp.to_dict() if self.qp else None,
            "error": self.error,
            "preflight": self.preflight or {},
        }

    def to_summary(self) -> dict[str, Any]:
        return {
            "score_id": self.score_id,
            "score_status": self.score_status,
            "quant_result": self.qr.score if self.qr else None,
            "quant_process": self.qp.score if self.qp else None,
            "overall_score": self.overall_score,
            "judge_reliability": self.judge_reliability or {},
            "eval_mode": self.eval_mode,
            "blocking_missing": self.blocking_missing,
            "interrupted": self.interrupted,
            "error": self.error,
        }
