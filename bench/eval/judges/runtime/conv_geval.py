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
from typing import Any

from eval.judges.runtime.llm_client import (
    EwanLLMClient,
    extract_json_from_response,
)

log = logging.getLogger(__name__)

PROMPT_TEMPLATE_VERSION = "conv_geval_score_prompt_v1"
OUTPUT_SCHEMA_VERSION = "conv_geval_score_json_v1"
OUTPUT_SCHEMA_REQUIRED_FIELDS = ["score"]
OUTPUT_SCHEMA_OPTIONAL_FIELDS = ["evidence", "reason"]


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

# Judge Metadata
Rubric ID: {rubric_id}
Rubric Version: {rubric_version}
Prompt Template Version: {prompt_template_version}
Dimension: {dimension}
Score Interpretation: {score_interpretation}

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
        rubric_metadata: dict[str, Any] | None = None,
        prompt_template_version: str = PROMPT_TEMPLATE_VERSION,
        output_schema_version: str = OUTPUT_SCHEMA_VERSION,
        **kwargs,
    ):
        self.name = name
        self.criteria = criteria
        self.role = role
        self.rules = rules
        self.threshold = threshold
        self.model = model
        self.max_score = max_score
        self.rubric_metadata = dict(rubric_metadata or {})
        self.prompt_template_version = prompt_template_version
        self.output_schema_version = output_schema_version

        # Mutable state — set after evaluation
        self.score: float = 0.0
        self.reason: str = ""
        self.evaluation_cost: float = 0.0

    def _model_name(self) -> str:
        get_name = getattr(self.model, "get_model_name", None)
        if callable(get_name):
            name = get_name()
            if name:
                return str(name)
        name = getattr(self.model, "model", None)
        return str(name) if name else str(self.model)

    def judge_metadata(self) -> dict[str, Any]:
        """Return stable metadata for persisted judge prompt/output records."""

        metadata = dict(self.rubric_metadata)
        scale = dict(metadata.get("score_scale") or {})
        scale.setdefault("min", 1)
        scale.setdefault("max", self.max_score)
        scale.setdefault("type", "integer")
        metadata.update(
            {
                "dimension": metadata.get("dimension") or self.name,
                "rubric_id": metadata.get("rubric_id") or f"adhoc.{self.name}",
                "rubric_version": metadata.get("rubric_version") or "unversioned",
                "prompt_template_version": self.prompt_template_version,
                "judge_model": self._model_name(),
                "judge_temperature": getattr(self.model, "temperature", None),
                "output_schema": {
                    "version": self.output_schema_version,
                    "required_fields": list(OUTPUT_SCHEMA_REQUIRED_FIELDS),
                    "optional_fields": list(OUTPUT_SCHEMA_OPTIONAL_FIELDS),
                },
                "score_scale": scale,
                "score_interpretation": metadata.get("score_interpretation")
                or (
                    "Judge returns a raw integer score on the rubric scale; "
                    "runtime stores a normalized 0-1 score for aggregation."
                ),
            }
        )
        metadata.setdefault("context_fields_included", ["context"])
        metadata.setdefault("transcript_source", "evaluation_context")
        return metadata

    def render_prompt(self, test_case: EvalTestCase) -> str:
        metadata = self.judge_metadata()
        return _SCORE_PROMPT.format(
            role=self.role,
            rubric=self.criteria,
            rules=self.rules,
            context=test_case.context,
            max_score=self.max_score,
            rubric_id=metadata["rubric_id"],
            rubric_version=metadata["rubric_version"],
            prompt_template_version=metadata["prompt_template_version"],
            dimension=metadata["dimension"],
            score_interpretation=metadata["score_interpretation"],
        )

    async def a_measure(self, test_case: EvalTestCase) -> float:
        """Score the context against the rubric in a single LLM call.

        Returns normalized score in [0, 1].
        Sets ``.score``, ``.reason``, ``.evaluation_cost``.
        """
        prompt = self.render_prompt(test_case)

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
