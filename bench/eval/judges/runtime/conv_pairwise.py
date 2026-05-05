"""Pairwise conversational judge — picks the stronger of two tutor responses.

Complements ``conv_geval.EwanConvGEval`` (which scores one transcript on a
rubric dimension). The pairwise judge is used for the Stage 3 backstop in
issue #84: when point-score calibration against humans is noisy, a
"given these two responses on the same question, which better satisfies
the rubric dimension?" judgement is cheaper and does not require the
judge to anchor precisely to a 1-5 integer.

Usage::

    metric = EwanPairwiseGEval(
        name="adv_quant_correctness",
        criteria=rubric_text,
        role="You are an expert Educational Analyst...",
        rules="- Compare the two tutor responses...",
        model=client,
        rubric_metadata={...},
    )
    await metric.a_measure(
        PairwiseTestCase(
            shared_context="Why is my Sharpe negative?",
            response_a=tutor_a_text,
            response_b=tutor_b_text,
        )
    )
    print(metric.preferred, metric.margin, metric.reason)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from eval.judges.runtime.llm_client import (
    EwanLLMClient,
    extract_json_from_response,
)

log = logging.getLogger(__name__)

PROMPT_TEMPLATE_VERSION = "conv_pairwise_prompt_v1"
OUTPUT_SCHEMA_VERSION = "conv_pairwise_json_v1"
OUTPUT_SCHEMA_REQUIRED_FIELDS = ["preferred"]
OUTPUT_SCHEMA_OPTIONAL_FIELDS = ["margin", "reason"]

VALID_PREFERRED = {"A", "B", "tie"}
VALID_MARGIN = {"slight", "clear", "strong"}


@dataclass
class PairwiseTestCase:
    """Two candidate responses on the same shared context.

    For the tutor track, ``shared_context`` is the user opening
    message; for the QR/QP tracks, it is the structured task + required
    capabilities block. ``response_a`` and ``response_b`` hold the two
    candidate outputs to be compared.
    """

    shared_context: str
    response_a: str
    response_b: str


_PAIRWISE_PROMPT = """\
# Role
{role}

# Judge Metadata
Rubric ID: {rubric_id}
Rubric Version: {rubric_version}
Prompt Template Version: {prompt_template_version}
Dimension: {dimension}
Comparison Mode: pairwise (pick the better response)

# Scoring Rubric
{rubric}

# Comparison Process
1. Read the shared context to understand the question or task.
2. Read Response A and Response B in full.
3. Apply the rubric dimension above to each response.
4. Decide which response better satisfies the rubric. If the two are \
effectively equivalent under the rubric, answer "tie".
5. Ignore which slot (A or B) a response is in. Judge only on content.

# Rules
{rules}

# Output
Return ONLY a JSON object with these fields:
{{
    "preferred": "A" | "B" | "tie",
    "margin": "slight" | "clear" | "strong",
    "reason": "One or two sentences referencing the rubric condition \
that decided the comparison."
}}

# Shared Context
{shared_context}

# Response A
{response_a}

# Response B
{response_b}

JSON:
"""


class EwanPairwiseGEval:
    """Single-call pairwise rubric-based evaluation metric.

    Public attributes: ``.preferred``, ``.margin``, ``.reason``,
    ``.evaluation_cost``, ``.name``, ``.criteria``, ``.role``, ``.rules``.
    """

    def __init__(
        self,
        name: str,
        criteria: str,
        role: str = "",
        rules: str = "",
        model: EwanLLMClient | None = None,
        rubric_metadata: dict[str, Any] | None = None,
        prompt_template_version: str = PROMPT_TEMPLATE_VERSION,
        output_schema_version: str = OUTPUT_SCHEMA_VERSION,
    ):
        self.name = name
        self.criteria = criteria
        self.role = role
        self.rules = rules
        self.model = model
        self.rubric_metadata = dict(rubric_metadata or {})
        self.prompt_template_version = prompt_template_version
        self.output_schema_version = output_schema_version

        self.preferred: str = ""
        self.margin: str = ""
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
        metadata = dict(self.rubric_metadata)
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
                "comparison_mode": "pairwise",
            }
        )
        metadata.setdefault("context_fields_included", ["response_a", "response_b"])
        metadata.setdefault("transcript_source", "adversarial_pair")
        return metadata

    def render_prompt(self, test_case: PairwiseTestCase) -> str:
        metadata = self.judge_metadata()
        return _PAIRWISE_PROMPT.format(
            role=self.role,
            rubric=self.criteria,
            rules=self.rules,
            shared_context=test_case.shared_context,
            response_a=test_case.response_a,
            response_b=test_case.response_b,
            rubric_id=metadata["rubric_id"],
            rubric_version=metadata["rubric_version"],
            prompt_template_version=metadata["prompt_template_version"],
            dimension=metadata["dimension"],
        )

    async def a_measure(self, test_case: PairwiseTestCase) -> str:
        """Pick the preferred response in a single LLM call.

        Returns ``"A"``, ``"B"``, or ``"tie"``. Sets ``.preferred``,
        ``.margin``, ``.reason``, ``.evaluation_cost``.
        """
        import uuid

        prompt = self.render_prompt(test_case)
        metadata = self.judge_metadata()
        rubric_id = str(metadata.get("rubric_id") or f"adhoc.{self.name}")
        # prompt_version tracks the pairwise prompt template (see twin
        # rationale in conv_geval.a_measure).
        response_text, cost = await self.model.a_generate(
            prompt,
            call_id=f"{rubric_id}.pairwise.{self.name}-{uuid.uuid4().hex[:8]}",
            prompt_id=f"{rubric_id}.pairwise",
            prompt_version=str(self.prompt_template_version),
        )
        self.evaluation_cost += cost

        parsed = extract_json_from_response(response_text)

        preferred = str(parsed.get("preferred", "")).strip()
        if preferred not in VALID_PREFERRED:
            self._raw_failed_response = response_text
            raise ValueError(
                f"Invalid pairwise preferred='{preferred}' for {self.name}: "
                f"{response_text[:200]}"
            )

        margin = str(parsed.get("margin", "")).strip().lower()
        if margin and margin not in VALID_MARGIN:
            margin = ""

        self.preferred = preferred
        self.margin = margin
        self.reason = str(parsed.get("reason", ""))
        return self.preferred
