"""ConversationalGEval — single-call rubric-based evaluation.

Scores a conversation against a structured rubric in one LLM call.
The rubric (with evaluation process and rules) is injected directly
into the scoring prompt — no intermediate "evaluation steps" generation.

Usage:
    metric = EwanConvGEval(name=..., criteria=rubric_text, model=client, max_score=5)
    score = await metric.a_measure(test_case)
    print(metric.score, metric.reason, metric.evaluation_cost)
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from server.eval.ewan_eval.llm_client import (
    EwanLLMClient,
    extract_json_from_response,
)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Data structures
# ──────────────────────────────────────────────────────────────


@dataclass
class Turn:
    """Single conversation turn."""

    role: str
    content: str


@dataclass
class ConversationalTestCase:
    """Conversation container."""

    turns: list[Turn]
    scenario: Optional[str] = None
    expected_outcome: Optional[str] = None
    user_description: Optional[str] = None


# ──────────────────────────────────────────────────────────────
# Prompt template
# ──────────────────────────────────────────────────────────────

_SCORE_PROMPT = """\
# Role
You are an expert Educational Analyst. Your goal is to evaluate a tutor's \
performance on a specific dimension by strictly following the rubric below.

# Scoring Rubric
{rubric}

# Evaluation Process
1. Evidence: Identify 2-3 key moments in the conversation relevant to this \
dimension.
2. Ceiling Check: If ANY Score 1 failure behavior is present, score MUST be 1. \
Stop.
3. Baseline Check: If ALL Score 3 baseline requirements are met, score is at \
least 3. If not met, score is 2.
4. Upward Check: If Score 4 conditions are met, score is at least 4. \
If Score 5 conditions are also met, score is 5.

# Rules
- Evaluate the tutor (assistant). Use student messages as context only.
- Consider ALL turns in the conversation.
- Score strictly against the rubric. Do not infer unobservable behaviors.

# Output
Return ONLY a JSON object with these fields:
{{
    "evidence": ["quote or behavior 1", "quote or behavior 2"],
    "reason": "Concise explanation referencing specific rubric conditions.",
    "score": integer (1-{max_score})
}}

# Conversation
{turns}

JSON:
"""


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _format_turns(turns: list[Turn]) -> str:
    """Format conversation turns as numbered JSON dicts."""
    formatted = []
    for i, turn in enumerate(turns):
        d = json.dumps(
            {"role": turn.role, "content": turn.content},
            ensure_ascii=False,
        )
        formatted.append(f"{i + 1}. {d}")
    return "\n".join(formatted)


# ──────────────────────────────────────────────────────────────
# Metric class
# ──────────────────────────────────────────────────────────────


class EwanConvGEval:
    """Single-call rubric-based conversational evaluation metric.

    Public attributes: ``.score``, ``.reason``, ``.evaluation_cost``,
    ``.name``, ``.criteria`` (full rubric text).
    """

    def __init__(
        self,
        name: str,
        criteria: str,
        threshold: float = 0.5,
        model: EwanLLMClient | None = None,
        max_score: int = 5,
        **kwargs,
    ):
        self.name = name
        self.criteria = (
            criteria  # Full rubric text (scoring guidance + eval process + rules)
        )
        self.threshold = threshold
        self.model = model
        self.max_score = max_score

        # Mutable state — set after evaluation
        self.score: float = 0.0
        self.reason: str = ""
        self.evaluation_cost: float = 0.0

    async def a_measure(self, test_case: ConversationalTestCase) -> float:
        """Score the conversation against the rubric in a single LLM call.

        Returns normalized score in [0, 1].
        Sets ``.score``, ``.reason``, ``.evaluation_cost``.
        """
        turns_text = _format_turns(test_case.turns)
        prompt = _SCORE_PROMPT.format(
            rubric=self.criteria,
            turns=turns_text,
            max_score=self.max_score,
        )

        response_text, cost = await self.model.a_generate(prompt)
        self.evaluation_cost += cost

        parsed = extract_json_from_response(response_text)

        if "score" not in parsed:
            self._raw_failed_response = response_text
            raise ValueError(
                f"Invalid JSON from judge for {self.name}: {response_text[:200]}"
            )

        raw_score = float(parsed["score"])
        self.reason = parsed.get("reason", "")
        self.evidence = parsed.get("evidence", [])

        # Normalize 1-{max_score} → 0-1
        self.score = max(0.0, min(1.0, raw_score / float(self.max_score)))
        return self.score
