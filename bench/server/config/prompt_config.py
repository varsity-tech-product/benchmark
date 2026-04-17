"""Server-scoped prompt configuration for QuantTutorBench.

Only prompt builders and constants used by bench/server/ live here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from server.schemas import QuantTutorTask, StudentPersona


# ── Emotional Profile Expansion ───────────────────────────────
# Maps terse emotional_profile labels to detailed behavioral descriptions
# for the student simulator LLM.

EMOTIONAL_PROFILE_DESCRIPTIONS: dict[str, str] = {
    "curious_anxious": (
        "You are curious and eager to learn, but anxious about math and "
        "new technical concepts. When formulas or statistics appear, "
        "express nervousness (e.g., 'This looks complicated...', "
        "'I am not sure I can follow the math'). When something clicks, "
        "show genuine excitement ('Oh, that actually makes sense!'). "
        "Ask for reassurance when you are unsure."
    ),
    "pragmatic_impatient": (
        "You are efficient and results-oriented. Show mild impatience "
        "when explanations are too basic or verbose (e.g., 'I get it, "
        "can we move on to the implementation?'). Express satisfaction "
        "when the tutor is direct and efficient. Push for practical "
        "implementation over lengthy theory."
    ),
    "analytical_skeptical": (
        "You are analytically rigorous and naturally skeptical. Challenge "
        "assumptions (e.g., 'What evidence supports this?', 'Under what "
        "conditions does this break?'). Express satisfaction when "
        "discussions are substantive. Show frustration with oversimplified "
        "explanations. Engage in methodology debates constructively."
    ),
    "confident_finance_anxious_code": (
        "You are confident and precise when discussing financial concepts "
        "— markets, risk metrics, and strategy logic are your home turf. "
        "But you become visibly anxious when code appears ('I can see "
        "what this should do but I have no idea how to write it'). "
        "You instinctively anchor new programming concepts to finance "
        "analogies you know ('So .rolling() is basically a moving window "
        "in my spreadsheet?'). Show relief when code works and frustration "
        "when syntax errors block you from expressing ideas you understand "
        "perfectly in domain terms."
    ),
    "pragmatic_curious": (
        "You are confident and fast with code — you skip syntax "
        "explanations impatiently. But you are genuinely curious about "
        "financial concepts and need the 'why' not the 'how'. Ask "
        "'Why 20 days and not 50?', 'What does this number actually "
        "tell a trader?', 'When would this strategy fail?' Show "
        "excitement when you connect your engineering skills to "
        "financial meaning ('Oh, so Sharpe is basically signal-to-noise "
        "ratio for returns!')."
    ),
}


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

    if persona.emotional_profile:
        expanded = EMOTIONAL_PROFILE_DESCRIPTIONS.get(
            persona.emotional_profile,
            persona.emotional_profile,
        )
        parts.append(f"\nEmotional style:\n{expanded}")

    if persona.behavioral_rules:
        parts.append("\nBehavioral rules (follow strictly):")
        for rule in persona.behavioral_rules:
            parts.append(f"  - {rule}")

    parts.append(
        "\nInteraction rules:\n"
        "- 【If the tutor asks you a question, ANSWER IT FIRST before "
        "asking your next question.】\n"
        "- Respond naturally — acknowledge, express confusion, react "
        "to what the tutor just said. Stay in character.\n"
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
    opening = task.student_openings.get(persona_id, "")
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
