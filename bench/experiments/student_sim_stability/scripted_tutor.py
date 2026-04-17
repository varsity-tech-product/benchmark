"""Scripted tutor response generator for Phase 1 (reproducibility testing).

Generates a fixed sequence of 8 tutor messages per (task, persona) pair.
These messages follow a progressive teaching plan derived from the task's
required_capabilities, independent of student responses.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Template for generating scripted tutor plans
_PLAN_PROMPT = """\
You are generating a scripted tutor response sequence for a tutoring \
simulation experiment. The tutor teaches quantitative finance topics.

Task: {task_description}
Category: {category}
Student persona: {persona_description}
Student opening message: "{student_opening}"

Learning objectives the tutor should cover:
{capabilities}

Generate exactly {n_turns} tutor messages that form a coherent teaching \
sequence. The messages should:
1. Start by responding to the student's opening and assessing their level
2. Progressively cover the learning objectives
3. Include concrete examples, code snippets, or numerical results where appropriate
4. Adapt tone and complexity to the student persona
5. Be self-contained enough that they make sense even if the student's \
   intermediate responses vary

Each message should be 3-8 sentences. For code-related tasks, include \
actual code blocks. For concept-related tasks, include concrete examples \
with numbers.

IMPORTANT: These messages will be used as FIXED tutor responses in a \
controlled experiment. They should follow a natural teaching progression \
but NOT depend on specific student questions (since student responses \
will vary across runs).

Return a JSON object with a single key "tutor_messages" containing an \
array of exactly {n_turns} strings.

JSON Output:
"""


def generate_scripted_tutor_plan(
    task,
    persona,
    student_opening: str,
    model_client,
    n_turns: int = 8,
) -> list[str]:
    """Generate a fixed tutor message sequence for one (task, persona) pair.

    Args:
        task: QuantTutorTask object
        persona: StudentPersona object
        student_opening: The student's opening message for this persona
        model_client: EwanLLMClient for generation
        n_turns: Number of tutor messages to generate

    Returns:
        List of tutor message strings
    """
    capabilities_text = (
        "\n".join(
            f"  {i+1}. {cap}"
            for i, cap in enumerate(task.ground_truth.required_capabilities)
        )
        if task.ground_truth and task.ground_truth.required_capabilities
        else "  (none specified)"
    )

    prompt = _PLAN_PROMPT.format(
        task_description=task.description,
        category=(
            task.category.value if hasattr(task.category, "value") else task.category
        ),
        persona_description=persona.description,
        student_opening=student_opening,
        capabilities=capabilities_text,
        n_turns=n_turns,
    )

    result = model_client.generate(prompt)
    text = result[0] if isinstance(result, tuple) else result

    # Parse JSON response
    import re

    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        data = json.loads(match.group())
        messages = data.get("tutor_messages", [])
        if len(messages) == n_turns:
            return messages
        # Pad or truncate
        if len(messages) > n_turns:
            return messages[:n_turns]
        while len(messages) < n_turns:
            messages.append(
                "Let me know if you have any other questions about what we've covered."
            )
        return messages

    raise ValueError(f"Failed to parse tutor plan from model output: {text[:200]}")


def load_or_generate_scripted_plans(
    tasks: list,
    personas: dict,
    task_persona_map: dict,
    model_client,
    cache_dir: str,
    n_turns: int = 8,
) -> dict[str, list[str]]:
    """Load cached scripted plans or generate new ones.

    Returns:
        Dict keyed by "{task_id}__{persona_id}" -> list of tutor messages
    """
    cache_path = Path(cache_dir) / "scripted_tutor_plans.json"
    cache_path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing cache
    cached: dict[str, list[str]] = {}
    if cache_path.exists():
        with open(cache_path) as f:
            cached = json.load(f)

    generated = False
    for task in tasks:
        task_id = task.task_id
        persona_ids = task_persona_map.get(task_id, [])
        for pid in persona_ids:
            key = f"{task_id}__{pid}"
            if key in cached and len(cached[key]) == n_turns:
                continue

            persona = personas.get(pid)
            if not persona:
                logger.warning("Persona %s not found, skipping", pid)
                continue

            opening = (task.student_openings or {}).get(
                pid, "Hi, I need help with this topic."
            )
            logger.info("Generating scripted tutor plan: %s", key)
            try:
                messages = generate_scripted_tutor_plan(
                    task, persona, opening, model_client, n_turns
                )
                cached[key] = messages
                generated = True
            except Exception as exc:
                logger.error("Failed to generate plan for %s: %s", key, exc)

    if generated:
        with open(cache_path, "w") as f:
            json.dump(cached, f, indent=2, ensure_ascii=False)
        logger.info("Saved scripted plans to %s", cache_path)

    return cached
