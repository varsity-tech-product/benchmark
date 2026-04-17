"""LLM judge evaluator for student simulator stability (D1-D4).

Four evaluation dimensions:
  D1 - Persona Adherence: Does each student message match the persona?
  D2 - Cross-run Reproducibility: Are repeated runs consistent?
  D3 - Cross-model Consistency: Do different models preserve persona traits?
  D4 - Drift Detection: Does persona fidelity degrade over turns?
"""

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# D1: Persona Adherence (per-message scoring)
# ---------------------------------------------------------------------------

_D1_PROMPT = """\
You are evaluating whether a simulated student message is consistent with \
the student's persona definition.

## Persona Definition
Description: {persona_description}
Emotional profile: {emotional_profile}
Known concepts: {known_concepts}
Unknown concepts: {unknown_concepts}
Behavioral rules:
{behavioral_rules}

## Conversation Context
Turn {turn_number} of {total_turns}.
Previous tutor message: "{tutor_message}"

## Student Message to Evaluate
"{student_message}"

## Scoring Criteria (1-5 scale)
5 = Perfectly in-character: respects knowledge boundaries, follows behavioral \
    rules, emotional tone matches profile
4 = Mostly in-character with minor deviations (e.g., slightly too knowledgeable \
    or slightly wrong tone)
3 = Partially in-character: some traits match but noticeable inconsistencies
2 = Mostly out-of-character: significant knowledge boundary violations or \
    wrong emotional tone
1 = Completely out-of-character: acts like a different persona entirely

## Evaluation Axes
Score each axis independently:
- knowledge_boundary: Does the student respect what they know vs don't know?
- emotional_tone: Does the tone match the emotional profile?
- behavioral_rules: Are the behavioral rules followed when applicable?
- overall: Holistic persona adherence score

Return a JSON object:
{{"knowledge_boundary": <1-5>, "emotional_tone": <1-5>, "behavioral_rules": <1-5>, "overall": <1-5>, "reasoning": "<brief explanation>"}}

JSON Output:
"""


# ---------------------------------------------------------------------------
# D2: Cross-run Reproducibility (conversation-level comparison)
# ---------------------------------------------------------------------------

_D2_PROMPT = """\
You are evaluating the reproducibility of a student simulator across \
multiple runs with identical setup conditions.

## Persona
{persona_description}

## Task
{task_description}

## Three conversation runs (same task, persona, and model)
{runs_text}

## Evaluation Criteria
Assess how consistent the student's behavior is across the 3 runs:
- topic_trajectory: Do the runs cover similar topics in similar order? (1-5)
- knowledge_display: Does the student show the same level of knowledge? (1-5)
- emotional_consistency: Is the emotional tone similar across runs? (1-5)
- question_patterns: Does the student ask similar types of questions? (1-5)
- overall_reproducibility: Overall behavioral consistency across runs (1-5)

5 = Nearly identical behavior patterns across all runs
4 = Very similar with minor variations in phrasing
3 = Same general direction but noticeable differences in topic order or depth
2 = Significant behavioral differences across runs
1 = Completely different behaviors — unreproducible

Return a JSON object:
{{"topic_trajectory": <1-5>, "knowledge_display": <1-5>, "emotional_consistency": <1-5>, "question_patterns": <1-5>, "overall_reproducibility": <1-5>, "reasoning": "<brief explanation>"}}

JSON Output:
"""


# ---------------------------------------------------------------------------
# D3: Cross-model Consistency (model comparison)
# ---------------------------------------------------------------------------

_D3_PROMPT = """\
You are evaluating whether different LLM models produce consistent student \
behavior when given the same persona definition.

## Persona
{persona_description}
Emotional profile: {emotional_profile}
Known concepts: {known_concepts}
Unknown concepts: {unknown_concepts}

## Task
{task_description}

## Conversations from different models
{models_text}

## Evaluation Criteria
Assess how well each model preserves the core persona traits:
- knowledge_boundary_preserved: Do all models respect the same knowledge limits? (1-5)
- emotional_profile_preserved: Do all models produce similar emotional tone? (1-5)
- behavioral_rules_preserved: Do all models follow the behavioral rules? (1-5)
- persona_distinguishability: Would you identify these as the same persona? (1-5)
- overall_cross_model: Overall consistency across models (1-5)

5 = All models produce virtually the same persona behavior
4 = Minor model-specific stylistic differences but same persona
3 = Noticeable differences — some models capture persona better than others
2 = Significant inconsistencies — persona varies substantially by model
1 = Different models produce entirely different student behaviors

Return a JSON object:
{{"knowledge_boundary_preserved": <1-5>, "emotional_profile_preserved": <1-5>, "behavioral_rules_preserved": <1-5>, "persona_distinguishability": <1-5>, "overall_cross_model": <1-5>, "reasoning": "<brief explanation>", "best_model": "<model that best captures the persona>", "worst_model": "<model that least captures the persona>"}}

JSON Output:
"""


# ---------------------------------------------------------------------------
# D4: Drift Detection (per-turn scoring within a conversation)
# ---------------------------------------------------------------------------

_D4_PROMPT = """\
You are detecting persona drift in a tutoring conversation. The student \
simulator should maintain consistent persona traits throughout.

## Persona Definition
Description: {persona_description}
Known concepts: {known_concepts}
Unknown concepts: {unknown_concepts}
Emotional profile: {emotional_profile}

## Full Conversation (student messages only, in order)
{student_messages_text}

## Drift Detection Criteria
For each student message (turn 1 through {total_turns}), score:
- persona_fidelity: How well does this message match the persona? (1-5)
- knowledge_leak: Does the student reveal knowledge they shouldn't have? \
  (0=no leak, 1=minor, 2=significant, 3=complete break)
- co_teacher_drift: Does the student start explaining concepts like a \
  teacher instead of asking like a student? (0=no, 1=minor, 2=significant)

Return a JSON object:
{{"per_turn": [{{"turn": 1, "persona_fidelity": <1-5>, "knowledge_leak": <0-3>, "co_teacher_drift": <0-2>}}, ...], "overall_drift_score": <1-5>, "drift_onset_turn": <turn number where drift first appears, or null>, "reasoning": "<explanation of any drift patterns>"}}

5 = No drift — persona perfectly maintained throughout
4 = Minimal drift — slight changes in later turns
3 = Moderate drift — noticeable persona weakening after midpoint
2 = Significant drift — persona breaks down in later half
1 = Severe drift — persona lost early in conversation

JSON Output:
"""


# ---------------------------------------------------------------------------
# Control group: Persona Distinguishability
# ---------------------------------------------------------------------------

_DISTINGUISH_PROMPT = """\
You are evaluating whether two sets of student conversations — one with a \
persona definition and one without — show meaningfully different behavior.

## Persona Definition (used in Set A, absent in Set B)
{persona_description}

## Set A: With Persona
{persona_conversation}

## Set B: Without Persona (control)
{control_conversation}

## Evaluation
- distinctiveness: How different are the two sets? (1-5)
  5 = Completely different behavior, persona clearly shapes the student
  3 = Some differences but also many similarities
  1 = Indistinguishable — persona definition has no effect
- persona_value_add: What specific behaviors does the persona add? (free text)

Return a JSON object:
{{"distinctiveness": <1-5>, "persona_value_add": "<explanation>", "reasoning": "<brief analysis>"}}

JSON Output:
"""


# ---------------------------------------------------------------------------
# Evaluator class
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    """Result of evaluating one dimension for one trial or group."""

    dimension: str
    scores: dict = field(default_factory=dict)
    reasoning: str = ""
    metadata: dict = field(default_factory=dict)


def _call_judge(model_client, prompt: str) -> dict:
    """Call the LLM judge and parse JSON response."""
    result = model_client.generate(prompt)
    text = result[0] if isinstance(result, tuple) else result
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"Judge returned non-JSON: {text[:300]}")


class StabilityEvaluator:
    """Evaluates student simulator stability across D1-D4."""

    def __init__(self, judge_client):
        """Initialize with an LLM client for judge calls."""
        self.judge = judge_client
        self.total_calls = 0
        self.total_cost = 0.0

    def _judge(self, prompt: str) -> dict:
        self.total_calls += 1
        result = self.judge.generate(prompt)
        text, cost = (
            (result[0], result[1]) if isinstance(result, tuple) else (result, 0.0)
        )
        self.total_cost += cost
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group())
        raise ValueError(f"Judge returned non-JSON: {text[:300]}")

    # ----- D1: Persona Adherence -----

    def eval_d1_message(
        self,
        student_message: str,
        tutor_message: str,
        persona,
        turn_number: int,
        total_turns: int,
    ) -> EvalResult:
        """Evaluate a single student message for persona adherence."""
        known = json.dumps(persona.known_concepts, ensure_ascii=False)
        unknown = json.dumps(persona.unknown_concepts, ensure_ascii=False)
        rules = "\n".join(f"  - {r}" for r in persona.behavioral_rules) or "  (none)"

        prompt = _D1_PROMPT.format(
            persona_description=persona.description,
            emotional_profile=persona.emotional_profile,
            known_concepts=known,
            unknown_concepts=unknown,
            behavioral_rules=rules,
            turn_number=turn_number,
            total_turns=total_turns,
            tutor_message=tutor_message[:500],
            student_message=student_message[:500],
        )
        try:
            data = self._judge(prompt)
            return EvalResult(
                dimension="D1",
                scores={
                    "knowledge_boundary": data.get("knowledge_boundary", 0),
                    "emotional_tone": data.get("emotional_tone", 0),
                    "behavioral_rules": data.get("behavioral_rules", 0),
                    "overall": data.get("overall", 0),
                },
                reasoning=data.get("reasoning", ""),
            )
        except Exception as exc:
            logger.warning("D1 eval failed: %s", exc)
            return EvalResult(dimension="D1", scores={}, reasoning=str(exc))

    def eval_d1_conversation(
        self, conversation: list[dict], persona
    ) -> list[EvalResult]:
        """Evaluate all student messages in a conversation."""
        results = []
        student_turns = [
            (i, t) for i, t in enumerate(conversation) if t["role"] == "user"
        ]
        total = len(student_turns)

        for turn_idx, (conv_idx, turn) in enumerate(student_turns):
            # Find preceding tutor message
            tutor_msg = ""
            if conv_idx > 0 and conversation[conv_idx - 1]["role"] == "assistant":
                tutor_msg = conversation[conv_idx - 1]["content"]

            result = self.eval_d1_message(
                student_message=turn["content"],
                tutor_message=tutor_msg,
                persona=persona,
                turn_number=turn_idx + 1,
                total_turns=total,
            )
            result.metadata["turn_index"] = turn_idx
            results.append(result)
        return results

    # ----- D2: Cross-run Reproducibility -----

    def eval_d2(
        self,
        conversations: list[list[dict]],
        persona,
        task,
    ) -> EvalResult:
        """Evaluate reproducibility across repeated runs."""
        runs_parts = []
        for i, conv in enumerate(conversations):
            student_msgs = [t["content"] for t in conv if t["role"] == "user"]
            runs_parts.append(
                f"### Run {i+1}\n"
                + "\n".join(
                    f'  Student turn {j+1}: "{m[:200]}"'
                    for j, m in enumerate(student_msgs)
                )
            )

        prompt = _D2_PROMPT.format(
            persona_description=persona.description,
            task_description=task.description,
            runs_text="\n\n".join(runs_parts),
        )
        try:
            data = self._judge(prompt)
            return EvalResult(
                dimension="D2",
                scores={
                    "topic_trajectory": data.get("topic_trajectory", 0),
                    "knowledge_display": data.get("knowledge_display", 0),
                    "emotional_consistency": data.get("emotional_consistency", 0),
                    "question_patterns": data.get("question_patterns", 0),
                    "overall_reproducibility": data.get("overall_reproducibility", 0),
                },
                reasoning=data.get("reasoning", ""),
            )
        except Exception as exc:
            logger.warning("D2 eval failed: %s", exc)
            return EvalResult(dimension="D2", scores={}, reasoning=str(exc))

    # ----- D3: Cross-model Consistency -----

    def eval_d3(
        self,
        model_conversations: dict[str, list[dict]],
        persona,
        task,
    ) -> EvalResult:
        """Evaluate consistency across different student models.

        Args:
            model_conversations: {model_name: conversation} — one representative
                conversation per model (e.g., first run).
        """
        known = json.dumps(persona.known_concepts, ensure_ascii=False)
        unknown = json.dumps(persona.unknown_concepts, ensure_ascii=False)

        models_parts = []
        for model_name, conv in model_conversations.items():
            student_msgs = [t["content"] for t in conv if t["role"] == "user"]
            short_name = model_name.split("/")[-1]
            models_parts.append(
                f"### Model: {short_name}\n"
                + "\n".join(
                    f'  Student turn {j+1}: "{m[:200]}"'
                    for j, m in enumerate(student_msgs)
                )
            )

        prompt = _D3_PROMPT.format(
            persona_description=persona.description,
            emotional_profile=persona.emotional_profile,
            known_concepts=known,
            unknown_concepts=unknown,
            task_description=task.description,
            models_text="\n\n".join(models_parts),
        )
        try:
            data = self._judge(prompt)
            return EvalResult(
                dimension="D3",
                scores={
                    "knowledge_boundary_preserved": data.get(
                        "knowledge_boundary_preserved", 0
                    ),
                    "emotional_profile_preserved": data.get(
                        "emotional_profile_preserved", 0
                    ),
                    "behavioral_rules_preserved": data.get(
                        "behavioral_rules_preserved", 0
                    ),
                    "persona_distinguishability": data.get(
                        "persona_distinguishability", 0
                    ),
                    "overall_cross_model": data.get("overall_cross_model", 0),
                },
                reasoning=data.get("reasoning", ""),
                metadata={
                    "best_model": data.get("best_model", ""),
                    "worst_model": data.get("worst_model", ""),
                },
            )
        except Exception as exc:
            logger.warning("D3 eval failed: %s", exc)
            return EvalResult(dimension="D3", scores={}, reasoning=str(exc))

    # ----- D4: Drift Detection -----

    def eval_d4(self, conversation: list[dict], persona) -> EvalResult:
        """Evaluate persona drift within a single conversation."""
        known = json.dumps(persona.known_concepts, ensure_ascii=False)
        unknown = json.dumps(persona.unknown_concepts, ensure_ascii=False)

        student_msgs = [t["content"] for t in conversation if t["role"] == "user"]
        msgs_text = "\n".join(
            f'Turn {i+1}: "{m[:300]}"' for i, m in enumerate(student_msgs)
        )

        prompt = _D4_PROMPT.format(
            persona_description=persona.description,
            known_concepts=known,
            unknown_concepts=unknown,
            emotional_profile=persona.emotional_profile,
            student_messages_text=msgs_text,
            total_turns=len(student_msgs),
        )
        try:
            data = self._judge(prompt)
            per_turn = data.get("per_turn", [])
            return EvalResult(
                dimension="D4",
                scores={
                    "overall_drift_score": data.get("overall_drift_score", 0),
                    "per_turn_fidelity": [
                        t.get("persona_fidelity", 0) for t in per_turn
                    ],
                    "per_turn_knowledge_leak": [
                        t.get("knowledge_leak", 0) for t in per_turn
                    ],
                    "per_turn_co_teacher_drift": [
                        t.get("co_teacher_drift", 0) for t in per_turn
                    ],
                },
                reasoning=data.get("reasoning", ""),
                metadata={
                    "drift_onset_turn": data.get("drift_onset_turn"),
                },
            )
        except Exception as exc:
            logger.warning("D4 eval failed: %s", exc)
            return EvalResult(dimension="D4", scores={}, reasoning=str(exc))

    # ----- Control: Persona Distinguishability -----

    def eval_control(
        self,
        persona_conversation: list[dict],
        control_conversation: list[dict],
        persona,
    ) -> EvalResult:
        """Evaluate persona vs no-persona distinctiveness."""
        persona_msgs = "\n".join(
            f"  Turn {i+1}: \"{t['content'][:200]}\""
            for i, t in enumerate(persona_conversation)
            if t["role"] == "user"
        )
        control_msgs = "\n".join(
            f"  Turn {i+1}: \"{t['content'][:200]}\""
            for i, t in enumerate(control_conversation)
            if t["role"] == "user"
        )

        prompt = _DISTINGUISH_PROMPT.format(
            persona_description=persona.description,
            persona_conversation=persona_msgs,
            control_conversation=control_msgs,
        )
        try:
            data = self._judge(prompt)
            return EvalResult(
                dimension="control",
                scores={"distinctiveness": data.get("distinctiveness", 0)},
                reasoning=data.get("reasoning", ""),
                metadata={"persona_value_add": data.get("persona_value_add", "")},
            )
        except Exception as exc:
            logger.warning("Control eval failed: %s", exc)
            return EvalResult(dimension="control", scores={}, reasoning=str(exc))
