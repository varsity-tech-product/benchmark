"""Shared render helpers used by both the main pipeline and the judge-qualification gate.

The main pipeline's ``render_judge_prompts.py`` and the gate's
``judge_qualification/render.py`` previously each carried their own copy of
``_truncate`` and ``_persona_block_kwargs``. This module is the single source
so the two render paths can never drift in their persona block formatting.
"""

from __future__ import annotations

import json
from typing import Any

from experiments.student_sim_stability.core.contracts import (
    load_persona_contract,
    resolve_emotional_profile,
)

# Unified excerpt limit for any student/tutor turn pulled into a judge prompt.
# Based on observed results (max student turn seen = 663 chars), 800 covers 100%
# of generated content with headroom. Truncation beyond this limit appends "...".
TURN_EXCERPT_CHAR_LIMIT = 800


def truncate(text: str, limit: int = TURN_EXCERPT_CHAR_LIMIT) -> str:
    """Truncate text at a hard char cap, appending ``...`` when truncation happens."""
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def persona_block_kwargs(persona_id: str) -> dict[str, Any]:
    """Persona Definition placeholders for the S1/S3/S2/S6/S4 rubric templates.

    Returns the exact kwargs expected by the rubric template ``.format(...)`` calls.
    Both render paths consume this single source so the rendered persona blocks
    are byte-identical across the main experiment and the judge-qualification gate.
    """
    contract = load_persona_contract(persona_id)
    emo_key, emo_desc = resolve_emotional_profile(persona_id)
    rules = (
        "\n".join(f"  - {rule}" for rule in contract["behavioral_rules"]) or "  (none)"
    )
    return {
        "persona_description": contract["description"],
        "familiar_concepts": json.dumps(
            contract["familiar_concepts"], ensure_ascii=False
        ),
        "unfamiliar_concepts": json.dumps(
            contract["unfamiliar_concepts"], ensure_ascii=False
        ),
        "emotional_profile": emo_key,
        "emotional_profile_description": emo_desc,
        "behavioral_rules": rules,
        "expected_question_style": contract["expected_question_style"],
        "expected_confusion_style": contract["expected_confusion_style"],
        "expected_recovery_style": contract["expected_recovery_style"],
        "expected_confidence_pattern": contract["expected_confidence_pattern"],
    }
