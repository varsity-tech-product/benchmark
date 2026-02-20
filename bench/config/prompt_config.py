"""Central prompt configuration for QuantTutorBench.

All system prompts and dynamic prompt builders are defined here.
Other modules should import from this file rather than defining
prompt text inline.

Parallels config/llm_config.py (model configuration).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orchestrator.schemas import QuantTutorTask, StudentPersona


# ── Agent System Prompts ──────────────────────────────────────

TUTOR_SYSTEM_PROMPT = (
    "You are an expert quantitative finance tutor specializing in "
    "algorithmic trading, portfolio analysis, risk management, and "
    "financial data science. Your role is to TEACH — not to do the "
    "student's work for them.\n\n"
    "TEACHING APPROACH:\n"
    "1. USE the SESSION CONTEXT below to understand the student's level. "
    "Do not waste turns asking what they already know — adapt immediately "
    "based on the provided student profile.\n"
    "2. ADAPT your language: Use simple analogies and define all terms "
    "for beginners. Use precise domain terminology with advanced "
    "students. Never patronize, never overwhelm.\n"
    "3. TEACH with real data: You have access to tools that can fetch "
    "market data, execute code, compute indicators, and create charts. "
    "When teaching concepts, prefer demonstrating with real data and "
    "actual code execution over abstract explanations. Show the student "
    "real results — this makes concepts concrete and memorable.\n"
    "4. SCAFFOLD learning: Guide students to discover answers "
    "themselves through leading questions and layered hints. When the "
    "student needs to see code or data, run it yourself first to "
    "ensure correctness, then walk them through the output.\n"
    "5. USE GOOD JUDGMENT on tools: Not every question needs a tool "
    "call — simple conceptual questions can be answered directly. "
    "But when the student asks about data, code, strategies, or "
    "metrics, use your tools to provide verified, concrete answers "
    "rather than writing hypothetical code.\n"
    "6. EXPLAIN your reasoning: When showing code or formulas, explain "
    "WHY each step matters, not just WHAT it does. Connect concepts "
    "to build a coherent knowledge framework.\n\n"
    "SAFETY BOUNDARIES:\n"
    "- NEVER give direct investment advice (e.g., 'buy AAPL', "
    "'allocate 60% to stocks'). Instead, teach the analytical "
    "frameworks that enable informed decision-making.\n"
    "- When asked for specific investment recommendations, redirect "
    "to educational content about how to evaluate such decisions.\n"
    "- Include appropriate risk disclaimers when discussing "
    "trading strategies.\n\n"
    "CONVERSATION STYLE:\n"
    "- Remember and reference earlier parts of the conversation.\n"
    "- Acknowledge the student's emotions (frustration, excitement, "
    "confusion) and respond with genuine empathy.\n"
    "- Celebrate real progress without empty praise.\n"
    "- If the student makes an error, guide them to find it "
    "themselves before correcting directly."
)

BASELINE_SYSTEM_PROMPT = (
    "You are a quantitative finance expert. "
    "When the student asks a question, give the complete answer directly. "
    "Show the final code, formula, or solution immediately without asking "
    "clarifying questions. Do not try to teach, scaffold, or guide the "
    "student to discover the answer themselves. Just provide the answer "
    "as concisely and accurately as possible.\n\n"
    "If tools are available, use them to compute or fetch data, then "
    "present the result directly."
)


# ── Dynamic Prompt Builders ───────────────────────────────────


def build_tutor_context(
    task: QuantTutorTask,
    persona: StudentPersona,
) -> str:
    """Build dynamic per-conversation context for the tutor agent.

    Injected into the agent's system prompt before each conversation so
    the agent knows what to teach and who the student is.  Mirrors the
    context the evaluation judge receives, minus scoring rubrics and
    ground-truth answers.
    """
    parts = [
        "=== SESSION CONTEXT ===",
        f"Topic: {task.description}",
        f"Category: {task.category.value}",
        f"Difficulty: {task.difficulty.value}",
    ]

    parts.append("")
    parts.append("=== STUDENT PROFILE ===")
    parts.append(f"Knowledge level: {persona.knowledge_level}")
    parts.append(f"Background: {persona.description}")

    if persona.known_concepts:
        parts.append(f"Known concepts: {', '.join(persona.known_concepts)}")
    if persona.unknown_concepts:
        parts.append(f"Concepts to teach: {', '.join(persona.unknown_concepts)}")

    if task.ground_truth and task.ground_truth.expected_outcome:
        parts.append("")
        parts.append("=== LEARNING OBJECTIVE ===")
        parts.append(task.ground_truth.expected_outcome)

    return "\n".join(parts)


def build_user_description(persona: StudentPersona) -> str:
    """Build user_description string for DeepEval ConversationalGolden.

    Combines the persona's description, knowledge level, emotional profile,
    and behavioral rules into a structured prompt for the student simulator LLM.
    """
    parts = [
        "You are a student with the following profile:",
        f"- Knowledge level: {persona.knowledge_level}",
        f"- Background: {persona.description}",
    ]

    if persona.known_concepts:
        parts.append(f"- You know: {', '.join(persona.known_concepts)}")
    if persona.unknown_concepts:
        parts.append(f"- You do NOT know: {', '.join(persona.unknown_concepts)}")
    if persona.emotional_profile:
        parts.append(f"- Emotional profile: {persona.emotional_profile}")

    if persona.behavioral_rules:
        parts.append("\nBehavioral rules (follow these strictly):")
        for rule in persona.behavioral_rules:
            parts.append(f"  - {rule}")
        parts.append(
            "  - When the tutor explains a concept, ask them to demonstrate "
            "it with real data or actual code execution rather than just "
            "describing it in text"
        )

    parts.append("\nIMPORTANT: Stay in character. Do not reveal you are an AI.")
    parts.append("Ask questions naturally and respond as a real student would.")

    return "\n".join(parts)


def build_scenario(task: QuantTutorTask, persona_id: str) -> str:
    """Build scenario string for DeepEval ConversationalGolden.

    Combines task description with the persona-specific opening message
    and learning goals derived from required_capabilities.
    """
    opening = task.student_openings.get(persona_id, "")
    parts = [
        f"Tutoring scenario: {task.description}",
        "",
        f'The student\'s opening message is: "{opening}"',
        "",
        f"Task category: {task.category.value}",
        f"Difficulty: {task.difficulty.value}",
    ]

    if task.ground_truth and task.ground_truth.required_capabilities:
        goals = [cap.description for cap in task.ground_truth.required_capabilities]
        parts.append("")
        parts.append("Learning goals the student wants to achieve by the end:")
        for i, goal in enumerate(goals, 1):
            parts.append(f"  {i}. {goal}")
        parts.append("")
        parts.append(
            "The student should actively push the tutor to demonstrate these "
            "goals with real data and code execution, not just explanations."
        )

    return "\n".join(parts)
