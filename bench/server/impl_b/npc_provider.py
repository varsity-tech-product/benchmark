"""Trivial Impl B NPC provider."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from platform_api.contracts import (
    EvalItem,
    FileArtifact,
    NPCProvider,
    NPCReply,
    ToolLog,
    TranscriptMessage,
)


class TrivialNPCProvider(NPCProvider):
    """Single-turn NPC that closes after the agent's first response."""

    def initial_message(self, task: EvalItem) -> str:
        return str(task.payload.get("prompt") or "")

    def respond(
        self,
        transcript: Sequence[TranscriptMessage],
        tool_logs: Sequence[ToolLog],
        files: Mapping[str, FileArtifact],
        payload: Mapping[str, object],
    ) -> NPCReply:
        agent_turns = sum(1 for message in transcript if message.role == "assistant")
        return NPCReply(
            message="continue",
            terminate=agent_turns >= 1,
            reason="impl_b_single_turn" if agent_turns >= 1 else "",
            payload={"agent_turns": agent_turns},
            telemetry={"llm_judge_used": False},
        )
