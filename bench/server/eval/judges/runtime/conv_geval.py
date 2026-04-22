"""ConversationalGEval — single-call rubric-based evaluation.

Scores a context against a structured rubric in one LLM call.
The rubric (with evaluation process and rules) is injected directly
into the scoring prompt — no intermediate "evaluation steps" generation.

Unified evaluation class for Tutor 6D, QP (task_planning / problem_solving),
and QR (result_judge). Dimension-specific content (role, rules, rubric) is
injected via constructor parameters; the prompt template provides only the
shared evaluation algorithm (ceiling check → baseline → upward) and output format.

Usage:
    metric = EwanConvGEval(
        name="D1_finance_adaptation",
        criteria=rubric_text,
        role="You are an expert Educational Analyst...",
        rules="- Evaluate the tutor (assistant)...",
        model=client,
        max_score=5,
    )
    score = await metric.a_measure(EvalTestCase(context=formatted_text))
    print(metric.score, metric.reason, metric.evaluation_cost)
"""

import json
import logging
from dataclasses import dataclass

from server.eval.judges.runtime.llm_client import (
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
class EvalTestCase:
    """Evaluation context container.

    The ``context`` field is a pre-formatted string containing everything
    the judge needs to evaluate. The caller is responsible for building
    this string using the appropriate context_builder function.

    For Tutor 6D: formatted conversation turns (with optional enrichment).
    For QR: task description + RC + tool outputs + workspace + agent summary.
    For QP: enriched conversation + RC checklist (task_planning) or error summary (problem_solving).
    """

    context: str


# Backward-compatible alias — will be removed after full migration.
ConversationalTestCase = EvalTestCase


# ──────────────────────────────────────────────────────────────
# Prompt template
# ──────────────────────────────────────────────────────────────

_SCORE_PROMPT = """\
# Role
{role}

# Scoring Rubric
{rubric}

# Evaluation Process
1. Evidence: Identify 2-3 key moments in the context relevant to this \
dimension.
2. Ceiling Check: If ANY Score 1 failure behavior is present, score MUST be 1. \
Stop.
3. Baseline Check: If ALL Score 3 baseline requirements are met, score is at \
least 3. If not met, score is 2.
4. Upward Check: If Score 4 conditions are met, score is at least 4. \
If Score 5 conditions are also met, score is 5.

# Rules
{rules}

# Output
Return ONLY a JSON object with these fields:
{{
    "evidence": ["quote or behavior 1", "quote or behavior 2"],
    "reason": "Concise explanation referencing specific rubric conditions.",
    "score": integer (1-{max_score})
}}

# Context
{context}

JSON:
"""


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def format_turns(turns: list[Turn]) -> str:
    """Format conversation turns as numbered JSON dicts.

    Public utility — used by context_builder to format conversation
    sections of the context string.
    """
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
    """Single-call rubric-based evaluation metric.

    Public attributes: ``.score``, ``.reason``, ``.evaluation_cost``,
    ``.name``, ``.criteria`` (full rubric text), ``.role``, ``.rules``.
    """

    def __init__(
        self,
        name: str,
        criteria: str,
        role: str = "",
        rules: str = "",
        threshold: float = 0.5,
        model: EwanLLMClient | None = None,
        max_score: int = 5,
        **kwargs,
    ):
        self.name = name
        self.criteria = criteria
        self.role = role
        self.rules = rules
        self.threshold = threshold
        self.model = model
        self.max_score = max_score

        # Mutable state — set after evaluation
        self.score: float = 0.0
        self.reason: str = ""
        self.evaluation_cost: float = 0.0

    async def a_measure(self, test_case: EvalTestCase) -> float:
        """Score the context against the rubric in a single LLM call.

        Returns normalized score in [0, 1].
        Sets ``.score``, ``.reason``, ``.evaluation_cost``.
        """
        prompt = _SCORE_PROMPT.format(
            role=self.role,
            rubric=self.criteria,
            rules=self.rules,
            context=test_case.context,
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
        # (score - 1) / (max_score - 1) → {0, 0.25, 0.5, 0.75, 1.0} for 1-5 scale
        # Score 1 (Failure) = 0.0, Score 3 (Baseline) = 0.5, Score 5 (Excellent) = 1.0
        if self.max_score <= 1:
            self.score = 1.0 if raw_score >= 1 else 0.0
        else:
            self.score = max(0.0, min(1.0, (raw_score - 1) / float(self.max_score - 1)))
        return self.score
