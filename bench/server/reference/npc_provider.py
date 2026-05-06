"""Reference NPCProvider adapter backed by the existing user simulator."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from eval.contracts.schemas import QuantTutorTask, UserPersona
from platform_api.contracts import (
    EvalItem,
    FileArtifact,
    NPCProvider,
    NPCReply,
    ToolLog,
    TranscriptMessage,
)
from server.core.user_sim import UserSimulator
from server.reference.prompts import (
    build_reference_scenario,
    build_reference_user_description,
)


def _task_from_payload(payload: Mapping[str, Any]) -> QuantTutorTask:
    raw = payload.get("quant_tutor_task")
    if isinstance(raw, QuantTutorTask):
        return raw
    if isinstance(raw, Mapping):
        return QuantTutorTask(**raw)
    raise ValueError("Reference NPC payload is missing quant_tutor_task")


def _conversation_from_transcript(
    transcript: Sequence[TranscriptMessage],
) -> list[dict[str, Any]]:
    conversation: list[dict[str, Any]] = []
    for message in transcript:
        entry = {
            "role": message.role,
            "content": message.content,
            **({"ts": message.ts} if message.ts is not None else {}),
        }
        entry.update(message.metadata)
        conversation.append(entry)
    return conversation


def _file_ledger_from_artifacts(
    files: Mapping[str, FileArtifact],
) -> dict[str, dict[str, Any]]:
    ledger: dict[str, dict[str, Any]] = {}
    for name, artifact in files.items():
        metadata = dict(artifact.metadata or {})
        ledger[str(name)] = {
            "prev_content": metadata.get("prev_content"),
            "prev_turn": metadata.get("prev_turn"),
            "current_content": artifact.content,
            "current_turn": metadata.get("current_turn"),
            "is_image": bool(metadata.get("is_image")),
            "media_type": artifact.media_type,
            "path": artifact.path,
        }
    return ledger


class ReferenceNPCProvider(NPCProvider):
    """Thin NPCProvider wrapper around UserSimulator and reference prompts."""

    def initial_message(self, task: EvalItem) -> str:
        business_task = _task_from_payload(task.payload)
        return business_task.user_opening or "Hi, I need help with this topic."

    def build_user_simulator(
        self,
        task: QuantTutorTask,
        persona: UserPersona,
        *,
        model=None,
    ) -> UserSimulator:
        return UserSimulator(
            scenario=build_reference_scenario(
                task,
                persona.persona_id,
                has_incremental_tc=False,
            ),
            user_description=build_reference_user_description(
                persona,
                has_incremental_tc=False,
            ),
            model=model,
        )

    def respond(
        self,
        transcript: Sequence[TranscriptMessage],
        tool_logs: Sequence[ToolLog],
        files: Mapping[str, FileArtifact],
        payload: Mapping[str, object],
    ) -> NPCReply:
        task = payload.get("task")
        persona = payload.get("persona")
        user_sim = payload.get("user_sim")
        if not isinstance(task, QuantTutorTask):
            task = _task_from_payload(payload)
        if not hasattr(user_sim, "generate_message"):
            if not isinstance(persona, UserPersona):
                raise ValueError("Reference NPC payload is missing persona")
            user_sim = self.build_user_simulator(
                task,
                persona,
                model=payload.get("model"),
            )

        reply, task_end = user_sim.generate_message(
            _conversation_from_transcript(transcript),
            runtime_guidance=str(payload.get("runtime_guidance") or ""),
            file_ledger=_file_ledger_from_artifacts(files),
            tool_logs=list(tool_logs),
            workspace_path=str(payload.get("workspace_path") or "") or None,
        )
        return NPCReply(
            message=reply,
            terminate=task_end,
            reason="user_satisfied" if task_end else "",
            payload={"task_end": task_end},
            telemetry={"simulator_cost": getattr(user_sim, "total_cost", 0.0)},
        )
