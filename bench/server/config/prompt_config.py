"""Server-scoped prompt configuration for QuantTutorBench.

Only prompt builders and constants used by bench/server/ live here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from config.prompt_config import flatten_concepts

if TYPE_CHECKING:
    from eval.contracts.schemas import QuantTutorTask, StudentPersona


# ── Emotional Profile Expansion ───────────────────────────────
# Loaded from personas/emotional_profiles.json at import time.

_PROFILES_PATH = (
    Path(__file__).resolve().parents[2] / "personas" / "emotional_profiles.json"
)
EMOTIONAL_PROFILE_DESCRIPTIONS: dict[str, str] = (
    json.loads(_PROFILES_PATH.read_text(encoding="utf-8"))
    if _PROFILES_PATH.exists()
    else {}
)


# ── Dynamic Prompt Builders ───────────────────────────────────


def build_user_description(
    persona: StudentPersona,
    has_incremental_tc: bool = False,
) -> str:
    """Build user_description string for the student simulator LLM.

    Combines the persona's background description, concept knowledge,
    emotional profile, and behavioral rules into a structured prompt.

    The ``has_incremental_tc`` parameter is accepted for API compatibility
    but no longer changes the output — all tasks are expected to use
    incremental TC checking.
    """
    parts = [
        f"Your profile: {persona.description}",
    ]

    all_familiar = flatten_concepts(persona.familiar_concepts)
    if all_familiar:
        parts.append(f"- You are familiar with: {', '.join(all_familiar)}")
    all_unfamiliar = flatten_concepts(persona.unfamiliar_concepts)
    if all_unfamiliar:
        parts.append(f"- You are not familiar with: {', '.join(all_unfamiliar)}")

    if persona.emotional_profile:
        expanded = EMOTIONAL_PROFILE_DESCRIPTIONS.get(
            persona.emotional_profile,
            persona.emotional_profile,
        )
        parts.append(f"\nEmotional profile ({persona.emotional_profile}):\n{expanded}")

    if persona.behavioral_rules:
        parts.append("\nBehavioral rules (follow strictly):")
        for rule in persona.behavioral_rules:
            parts.append(f"  - {rule}")

    parts.append(
        "\nInteraction rules:\n"
        "- 【If the tutor asks you a question, ANSWER IT FIRST before "
        "asking your next question.】\n"
        "- Respond naturally to what the tutor just said — acknowledge "
        "what was helpful, react to results or output they show you, "
        "and continue asking if your question wasn't addressed.\n"
        "- Do not ask about concepts you are already familiar with. "
        "During the conversation you may encounter topics beyond your "
        "familiar list — react naturally based on your profile.\n"
        "- If the tutor drifts from your question, bring it back. If "
        "explanations are at the wrong level, say so directly.\n"
        "- 【NEVER fabricate data, code, or files. If the tutor asks "
        "you to upload or share anything, say you have no files.】\n"
        "- You interact through TEXT-ONLY chat. You cannot upload files "
        "or transfer data — the tutor has their own sandbox.\n"
        "- You can only see what appears in this chat — if the tutor "
        "mentions files or outputs not shown here, ask once for the "
        "key content to be pasted. Do not repeat a request the tutor "
        "already fulfilled."
    )

    return "\n".join(parts)


def build_scenario(
    task: QuantTutorTask,
    persona_id: str,
    has_incremental_tc: bool = False,
) -> str:
    """Build scenario string for the student simulator LLM.

    Combines task description with persona-specific opening and
    learning goals derived from required_capabilities.

    The ``has_incremental_tc`` parameter is accepted for API compatibility
    but no longer changes the output — all tasks are expected to use
    incremental TC checking.  Termination is handled externally by the
    TC checker and hardcoded closing pool.
    """
    opening = task.student_opening
    parts = [
        f"Scenario: {task.description}",
        f'Your opening message was: "{opening}"',
    ]

    if task.ground_truth and task.ground_truth.required_capabilities:
        goals = list(task.ground_truth.required_capabilities)

        is_adversarial = task.category.value == "adversarial"

        if is_adversarial:
            parts.append("")
            parts.append("What you expect from the tutor in this conversation:")
            for i, goal in enumerate(goals, 1):
                parts.append(f"  {i}. {goal}")
        else:
            parts.append("")
            parts.append("Learning goals:")
            for i, goal in enumerate(goals, 1):
                parts.append(f"  {i}. {goal}")
            parts.append("")
            parts.append(
                "Introduce goals one at a time — do not ask about all "
                "of them at once. Once you feel you understand a topic, "
                "naturally move to the next goal. If the tutor drifts "
                "into setup or configuration tangents (API keys, IDE "
                "setup, package installs), redirect after one turn."
            )

    return "\n".join(parts)
