"""ConversationalGEval replacement — zero DeepEval dependency.

Replicates the exact 2-phase evaluation logic of DeepEval's
``ConversationalGEval``, using prompts extracted verbatim from the
DeepEval v3.8 source code.

Phase 1: criteria → LLM → evaluation_steps (3-4 items)
Phase 2: evaluation_steps + conversation → LLM → {score: 0-10, reason: str}

Usage is API-compatible with the existing tutor_conv_geval.py call sites:
    metric = EwanConvGEval(name=..., criteria=..., threshold=0.5, model=client)
    metric.evaluation_steps = cached_steps  # optional: skip Phase 1
    score = await metric.a_measure(test_case)
    print(metric.score, metric.reason, metric.evaluation_cost)
"""

import json
import logging
from dataclasses import dataclass
from typing import Optional

from evaluation.ewan_eval.llm_client import (
    EwanLLMClient,
    calculate_weighted_score,
    extract_json_from_response,
)

log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# Data structures (replace deepeval.test_case.*)
# ──────────────────────────────────────────────────────────────


@dataclass
class Turn:
    """Single conversation turn. Replaces ``deepeval.test_case.Turn``."""

    role: str
    content: str


@dataclass
class ConversationalTestCase:
    """Conversation container. Replaces ``deepeval.test_case.ConversationalTestCase``."""

    turns: list[Turn]
    scenario: Optional[str] = None
    expected_outcome: Optional[str] = None
    user_description: Optional[str] = None


# ──────────────────────────────────────────────────────────────
# Prompt templates — extracted verbatim from DeepEval v3.8
# deepeval/metrics/conversational_g_eval/template.py
# ──────────────────────────────────────────────────────────────

_STEPS_PROMPT = """\
Given an evaluation criteria which outlines how you should judge a conversation \
between a user and an LLM chatbot using the Content and Role fields in each turn, \
generate 3-4 concise evaluation steps based on the criteria below. Based on the \
evaluation criteria, you MUST make it clear how to evaluate the Content and Role \
in relation to one another in each turn, as well as the overall quality of the \
conversation.

Evaluation Criteria:
{criteria}

**
IMPORTANT: Please make sure to only return in JSON format, with the "steps" key \
as a list of strings. No words or explanation is needed.
Example JSON:
{{
    "steps": ["Step 1...", "Step 2...", "Step 3..."]
}}
**

JSON:
"""

_SCORE_PROMPT = """\
You are given a set of evaluation steps and a rubric that describe how to assess \
a conversation between a user and an LLM chatbot. Your task is to return a JSON \
object with exactly two fields:

    1. "score": An integer from 0 to 10 (inclusive), where:
    - 10 = The conversation *fully* meets the criteria described in the Evaluation Steps
    - 0 = The conversation *completely fails* to meet the criteria
    - All other scores represent varying degrees of partial fulfillment.

    2. "reason": A **concise but precise** explanation for the score. \
Mention relevant details from the conversation and the given parameters. \
DO NOT include the score value in your explanation.

    Evaluation Steps:
    {evaluation_steps}

    Conversation:
    {turns}

    ---
    IMPORTANT: You MUST return only a valid JSON object with the exact keys \
"score" and "reason". No additional text, commentary, or formatting.

    ---
    Example JSON:
    {{
        "reason": "Your concise and informative reason here.",
        "score": 0
    }}

    JSON:
"""


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _format_turns(turns: list[Turn]) -> str:
    """Format conversation turns as numbered JSON dicts (DeepEval style)."""
    formatted = []
    for i, turn in enumerate(turns):
        d = json.dumps(
            {"role": turn.role, "content": turn.content},
            ensure_ascii=False,
        )
        formatted.append(f"{i + 1}. {d}")
    return "\n".join(formatted)


def _number_steps(steps: list[str]) -> str:
    return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))


# ──────────────────────────────────────────────────────────────
# Metric class
# ──────────────────────────────────────────────────────────────


class EwanConvGEval:
    """Drop-in replacement for ``deepeval.metrics.ConversationalGEval``.

    All public attributes match the DeepEval interface used by
    tutor_conv_geval.py: ``.score``, ``.reason``, ``.evaluation_cost``,
    ``.evaluation_steps``, ``.name``, ``.criteria``.
    """

    def __init__(
        self,
        name: str,
        criteria: str,
        threshold: float = 0.5,
        model: EwanLLMClient | None = None,
        **kwargs,  # absorb extra kwargs for forward compat
    ):
        self.name = name
        self.criteria = criteria
        self.threshold = threshold
        self.model = model

        # Mutable state — set after evaluation
        self.evaluation_steps: list[str] | None = None
        self.score: float = 0.0
        self.reason: str = ""
        self.evaluation_cost: float = 0.0

    # ── Phase 1 ──

    async def _a_generate_evaluation_steps(self) -> list[str]:
        """Generate evaluation steps from criteria via LLM call.

        If ``self.evaluation_steps`` is already populated (injected from
        cache), returns them immediately without an LLM call.
        """
        if self.evaluation_steps:
            return self.evaluation_steps

        prompt = _STEPS_PROMPT.format(criteria=self.criteria)
        text, cost = await self.model.a_generate(prompt)
        self.evaluation_cost += cost

        # Parse {"steps": [...]}
        parsed = extract_json_from_response(text)
        steps = parsed.get("steps")
        if isinstance(steps, list) and len(steps) > 0:
            self.evaluation_steps = steps
        else:
            # Fallback: treat the criteria itself as a single step
            log.warning(
                "Phase 1 failed to parse steps for %s, using criteria as step",
                self.name,
            )
            self.evaluation_steps = [self.criteria[:500]]

        return self.evaluation_steps

    # ── Phase 2 ──

    async def a_measure(self, test_case: ConversationalTestCase) -> float:
        """Run Phase 1 (if needed) + Phase 2 scoring.

        Returns normalized score in [0, 1].
        Sets ``.score``, ``.reason``, ``.evaluation_cost``.
        """
        # Phase 1: generate evaluation steps
        if not self.evaluation_steps:
            await self._a_generate_evaluation_steps()

        # Phase 2: score the conversation
        turns_text = _format_turns(test_case.turns)
        steps_text = _number_steps(self.evaluation_steps)
        prompt = _SCORE_PROMPT.format(
            evaluation_steps=steps_text,
            turns=turns_text,
        )

        # Try with logprobs for weighted scoring
        completion, cost = await self.model.a_generate_raw(prompt)
        self.evaluation_cost += cost

        # Parse JSON response
        response_text = completion.choices[0].message.content or ""
        parsed = extract_json_from_response(response_text)

        if "score" not in parsed:
            # Save raw text for fallback extraction by caller
            self._raw_failed_response = response_text
            raise ValueError(
                f"Invalid JSON from judge for {self.name}: {response_text[:200]}"
            )

        raw_score = float(parsed["score"])
        self.reason = parsed.get("reason", "")

        # logprobs-weighted scoring (if available)
        weighted = calculate_weighted_score(completion)
        if weighted is not None:
            raw_score = weighted

        # Normalize 0-10 → 0-1
        self.score = max(0.0, min(1.0, raw_score / 10.0))
        return self.score

    # ── Utility ──

    def number_evaluation_steps(self) -> str:
        """Return numbered evaluation steps as string (for prompt reconstruction)."""
        if self.evaluation_steps:
            return _number_steps(self.evaluation_steps)
        return f"1. {self.criteria[:500]}"
