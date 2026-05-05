"""Abstract base classes implemented by platform plugins."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Mapping, Sequence

from platform_api.contracts.models import (
    EvalItem,
    EvalSample,
    EvaluatorMetadata,
    FileArtifact,
    NPCReply,
    Score,
    ToolLog,
    TranscriptMessage,
)


class TaskSuite(ABC):
    """Task catalog boundary."""

    @abstractmethod
    def supported_tasks(self) -> set[str]:
        """Return task IDs this suite can serve."""

    @abstractmethod
    def get_task(self, task_id: str) -> EvalItem:
        """Return a task item envelope for *task_id*."""

    @abstractmethod
    def required_bundle_fields(self) -> set[str]:
        """Return bundle fields required by this suite."""


class NPCProvider(ABC):
    """Simulated user/NPC boundary."""

    @abstractmethod
    def initial_message(self, task: EvalItem) -> str:
        """Return the first NPC-visible message for a task."""

    @abstractmethod
    def respond(
        self,
        transcript: Sequence[TranscriptMessage],
        tool_logs: Sequence[ToolLog],
        files: Mapping[str, FileArtifact],
        payload: Mapping[str, object],
    ) -> NPCReply:
        """Return the next NPC reply and optional termination flag."""


class Evaluator(ABC):
    """Scoring boundary."""

    @abstractmethod
    def evaluate(self, item: EvalItem, sample: EvalSample) -> Score:
        """Score one sample for one task item."""

    @abstractmethod
    def metadata(self) -> EvaluatorMetadata:
        """Return static evaluator metadata."""
