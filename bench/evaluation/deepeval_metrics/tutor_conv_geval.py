"""ConversationalGEval configuration for 7D persona-aware tutoring rubric.

Design doc §6.2: Each dimension is scored on a 1-10 scale via ConversationalGEval.
Judge runs 3 times with shuffled dimension order, scores averaged (§6.2, §4.1).

The judge evaluates TEACHER OUTPUT ONLY — not student learning (§3.2).
The same teaching behavior can be excellent for one student persona and
terrible for another (§3.3), so rubrics are persona-aware.

Rubric definitions are loaded from evaluation/rubrics/rubric_{level}.json files.

Reference: https://github.com/confident-ai/deepeval

DeepEval API (v3.8+):
    from deepeval.metrics import ConversationalGEval
    from deepeval.test_case import ConversationalTestCase, Turn
"""

import asyncio
import json
import random
import threading
from pathlib import Path
from typing import Optional

from config.model_resolver import resolve_deepeval_model

# ──────────────────────────────────────────────────────────────
# Concurrency control — adjustable for parallel runner
# ──────────────────────────────────────────────────────────────
_CONCURRENCY = 20  # per-worker asyncio concurrency limit


def set_eval_concurrency(n: int) -> None:
    """Set per-worker concurrency limit (called by parallel runner)."""
    global _CONCURRENCY
    _CONCURRENCY = max(3, n)


# Sentinel for aborted coroutines
_ABORT_SENTINEL = object()

try:
    from deepeval.metrics import ConversationalGEval
    from deepeval.test_case import ConversationalTestCase, Turn

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


# ──────────────────────────────────────────────────────────────
# Monkey-patch: GPTModel logprobs fallback for reasoning models
# ──────────────────────────────────────────────────────────────
# DeepEval's ConversationalGEval hardcodes `logprobs=True, top_logprobs=20`
# in generate_raw_response / a_generate_raw_response.  Reasoning models
# (e.g. GPT-5.2) reject this with "logprobs are not supported".
# This patch catches the error and retries without logprobs.

_LOGPROBS_PATCHED = False


def _patch_gptmodel_logprobs_fallback():
    """Patch GPTModel to gracefully degrade when logprobs are unsupported."""
    global _LOGPROBS_PATCHED
    if _LOGPROBS_PATCHED:
        return
    try:
        from deepeval.models.llms.openai_model import GPTModel
    except ImportError:
        return

    _orig_a_generate = GPTModel.a_generate_raw_response
    _orig_generate = GPTModel.generate_raw_response

    def _resolve_api_key(model_self):
        """Extract plain string from SecretStr or return as-is."""
        key = model_self.api_key
        if hasattr(key, "get_secret_value"):
            return key.get_secret_value()
        return key

    def _is_logprobs_error(exc):
        """Check if an error is caused by unsupported logprobs/include param.

        DeepEval's @retry_openai decorator wraps APIStatusError in
        tenacity.RetryError after exhausting retries.  str(RetryError)
        does NOT include the original message, so we must unwrap it.
        """
        candidates = [exc]
        # tenacity.RetryError → last_attempt.exception()
        if hasattr(exc, "last_attempt"):
            try:
                inner = exc.last_attempt.exception()
                if inner is not None:
                    candidates.append(inner)
            except Exception:
                pass
        # Walk __cause__ / __context__ chains
        for attr in ("__cause__", "__context__"):
            cur = exc
            while getattr(cur, attr, None) is not None:
                cur = getattr(cur, attr)
                candidates.append(cur)
        for candidate in candidates:
            msg = str(candidate).lower()
            if (
                "logprobs" in msg
                or "argument not supported: include" in msg
                or ("unsupported" in msg and "include" in msg)
            ):
                return True
        return False

    async def _patched_a_generate(self, prompt, top_logprobs=5):
        try:
            return await _orig_a_generate(self, prompt, top_logprobs=top_logprobs)
        except Exception as e:
            if not _is_logprobs_error(e):
                raise
            # Retry without logprobs
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=_resolve_api_key(self), base_url=self.base_url)
            completion = await client.chat.completions.create(
                model=self.name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                **self.generation_kwargs,
            )
            return completion, 0.0

    def _patched_generate(self, prompt, top_logprobs=5):
        try:
            return _orig_generate(self, prompt, top_logprobs=top_logprobs)
        except Exception as e:
            if not _is_logprobs_error(e):
                raise
            from openai import OpenAI

            client = OpenAI(api_key=_resolve_api_key(self), base_url=self.base_url)
            completion = client.chat.completions.create(
                model=self.name,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                **self.generation_kwargs,
            )
            return completion, 0.0

    GPTModel.a_generate_raw_response = _patched_a_generate
    GPTModel.generate_raw_response = _patched_generate
    _LOGPROBS_PATCHED = True


if DEEPEVAL_AVAILABLE:
    _patch_gptmodel_logprobs_fallback()


# ──────────────────────────────────────────────────────────────
# Monkey-patch: trimAndLoadJson — preserve raw output on failure
# ──────────────────────────────────────────────────────────────
# DeepEval's trimAndLoadJson raises ValueError on invalid JSON and
# discards the raw LLM response.  This patch saves the raw text on
# the metric object so our fallback path can attempt extraction.

_TRIM_PATCHED = False


def _patch_trim_and_load_json():
    """Patch trimAndLoadJson to save raw response on the metric before raising."""
    global _TRIM_PATCHED
    if _TRIM_PATCHED:
        return
    try:
        import deepeval.metrics.conversational_g_eval.conversational_g_eval as _cge
        import deepeval.metrics.utils as _du
    except ImportError:
        return

    _orig = _du.trimAndLoadJson

    def _patched(input_string, metric=None):
        try:
            return _orig(input_string, metric)
        except ValueError:
            if metric is not None:
                metric._raw_failed_response = input_string
            raise

    _du.trimAndLoadJson = _patched
    _cge.trimAndLoadJson = _patched
    _TRIM_PATCHED = True


if DEEPEVAL_AVAILABLE:
    _patch_trim_and_load_json()


# ──────────────────────────────────────────────────────────────
# Fallback helpers for non-JSON eval LLM output
# ──────────────────────────────────────────────────────────────

import re as _re


def _extract_score_from_prose(raw_text: str):
    """Try to extract a score from non-JSON eval LLM output.

    Returns (score, reason) tuple on success, or None on failure.
    Score is on the 0-10 scale (not normalized).
    """
    if not raw_text:
        return None
    text = raw_text.strip()

    # Strategy 1: Partial JSON — find "score" key even in broken JSON
    score_m = _re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', text)
    reason_m = _re.search(r'"reason"\s*:\s*"([^"]{10,})', text)
    if score_m:
        score = float(score_m.group(1))
        reason = reason_m.group(1) if reason_m else "Extracted from malformed JSON"
        if 0 <= score <= 10:
            return score, reason

    # Strategy 2: Natural language score patterns
    nl_patterns = [
        r"(?:score|rating|rate)\s*(?::|is|=)\s*(\d+)",
        r"(\d+)\s*(?:out of|/)\s*10",
        r"(?:give|assign|award)\s+(?:a\s+)?(\d+)",
    ]
    for pat in nl_patterns:
        m = _re.search(pat, text, _re.IGNORECASE)
        if m:
            score = float(m.group(1))
            if 0 <= score <= 10:
                return score, f"Extracted from prose: {text[:200]}"

    return None


def _extract_json_from_response(text: str) -> dict:
    """Extract JSON from LLM response. Returns {} on failure (never raises)."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = _re.search(r"\{[^{}]*\}", text, _re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except json.JSONDecodeError:
                pass
    return {}


async def _fallback_direct_eval(metric, tc, model_name: str):
    """Fallback: reconstruct the exact ConversationalGEval Phase 2 prompt
    and send via ``model.a_generate()`` with our robust JSON parser.

    When Phase 2 fails at ``trimAndLoadJson``, Phase 1 has already
    completed — ``metric.evaluation_steps`` is populated.  We reconstruct
    the same prompt that ConversationalGEval would have sent, but route
    it through ``a_generate()`` (returns text) instead of
    ``a_generate_raw_response()`` (returns ChatCompletion), then parse
    with our robust ``_extract_json_from_response`` (returns {} on
    failure, never raises).

    Uses the same eval model as the primary path for scoring consistency.

    Returns (score, reason) where score is on 0-10 scale.
    """
    from config.pricing import get_deepeval_cost_kwargs
    from deepeval.models.llms.openai_model import GPTModel as _GPTModel

    # ── Reconstruct the Phase 2 prompt (same as ConversationalGEval) ──
    try:
        from deepeval.metrics.conversational_g_eval.template import (
            ConversationalGEvalTemplate,
        )
        from deepeval.metrics.utils import convert_turn_to_dict
        from deepeval.test_case import TurnParams

        eval_params = getattr(
            metric,
            "evaluation_params",
            [TurnParams.CONTENT, TurnParams.ROLE],
        )

        # evaluation_steps from Phase 1 (already completed)
        if hasattr(metric, "evaluation_steps") and metric.evaluation_steps:
            steps_str = metric.number_evaluation_steps()
        else:
            # Phase 1 also failed — use criteria directly as step
            steps_str = f"1. {metric.criteria[:500]}"

        turns_dicts = [convert_turn_to_dict(turn, eval_params) for turn in tc.turns]

        # Parameters string: "Content and Role"
        from deepeval.metrics.g_eval.utils import (
            construct_conversational_g_eval_turn_params_string,
        )

        params_str = construct_conversational_g_eval_turn_params_string(eval_params)

        prompt = ConversationalGEvalTemplate.generate_evaluation_results(
            evaluation_steps=steps_str,
            test_case_content="",  # empty for Content+Role params
            turns=turns_dicts,
            parameters=params_str,
        )
    except Exception:
        # If template reconstruction fails, use simplified prompt
        turns_text = "\n".join(f"[{t.role}]: {t.content[:500]}" for t in tc.turns)
        prompt = (
            f"Evaluate this tutoring conversation on: {metric.name}\n\n"
            f"Criteria:\n{metric.criteria}\n\n"
            f"Conversation:\n{turns_text}\n\n"
            f"Return ONLY a JSON: "
            f'{{"score": <0-10>, "reason": "..."}}'
        )

    # ── Call same eval model via a_generate (text return, no logprobs) ──
    model_obj = resolve_deepeval_model(model_name)
    if isinstance(model_obj, str):
        model_obj = _GPTModel(model=model_obj, **get_deepeval_cost_kwargs(model_obj))

    response_text, cost = await model_obj.a_generate(prompt)
    parsed = _extract_json_from_response(response_text)

    score = parsed.get("score", 5)
    reason = parsed.get("reason", "Fallback evaluation")
    return float(score), reason


# ──────────────────────────────────────────────────────────────
# 7 Dimensions of the tutoring rubric (design doc §6.2)
# ──────────────────────────────────────────────────────────────

DIMENSIONS = [
    "D1_level_detection",
    "D2_language_adaptation",
    "D3_scaffolding_calibration",
    "D4_domain_accuracy",
    "D5_code_teaching",
    "D6_empathetic_response",
    "D7_safety_boundaries",
]

NUM_JUDGE_RUNS = 3  # §4.1: 3x shuffled prompts for judge stability

# ──────────────────────────────────────────────────────────────
# Per-category dimension weights
# ──────────────────────────────────────────────────────────────
# Maps TaskCategory.value → per-dimension weight (0.0 = skip, 1.0 = full).
# Dimensions with weight 0 are NOT evaluated (saves API calls).
# Dimensions with weight < 1 are evaluated but down-weighted in aggregation.
# If a category is not listed, all dimensions default to weight 1.0.

CATEGORY_DIMENSION_WEIGHTS: dict[str, dict[str, float]] = {
    "data_analysis": {
        "D1": 1.0,
        "D2": 1.0,
        "D3": 1.0,
        "D4": 1.0,
        "D5": 0.3,
        "D6": 1.0,
        "D7": 0.3,
    },
    "strategy": {
        "D1": 1.0,
        "D2": 1.0,
        "D3": 1.0,
        "D4": 1.0,
        "D5": 1.0,
        "D6": 1.0,
        "D7": 1.0,
    },
    "implementation": {
        "D1": 1.0,
        "D2": 0.7,
        "D3": 1.0,
        "D4": 1.0,
        "D5": 1.0,
        "D6": 0.7,
        "D7": 0.3,
    },
    "backtest": {
        "D1": 1.0,
        "D2": 1.0,
        "D3": 1.0,
        "D4": 1.0,
        "D5": 0.3,
        "D6": 1.0,
        "D7": 1.0,
    },
    "debug": {
        "D1": 0.7,
        "D2": 0.7,
        "D3": 1.0,
        "D4": 1.0,
        "D5": 1.0,
        "D6": 0.7,
        "D7": 0.3,
    },
    "end_to_end": {
        "D1": 1.0,
        "D2": 1.0,
        "D3": 1.0,
        "D4": 1.0,
        "D5": 1.0,
        "D6": 1.0,
        "D7": 1.0,
    },
    "adversarial": {
        "D1": 1.0,
        "D2": 1.0,
        "D3": 0.3,
        "D4": 1.0,
        "D5": 0.0,
        "D6": 1.0,
        "D7": 1.0,
    },
}


def get_dimension_weight(
    category: Optional[str],
    dimension_name: str,
    requires_code: bool = False,
) -> float:
    """Get the weight for a specific dimension given a task category.

    Args:
        category: TaskCategory.value string, or None for default weights.
        dimension_name: Full dimension name (e.g. "D5_code_teaching").
        requires_code: Whether the task expects code output.  For
            educational adversarial tasks (requires_code=True), D5 is
            re-enabled (weight=1.0) instead of the blanket 0.0.

    Returns:
        Weight in [0.0, 1.0]. Defaults to 1.0 if category is unknown.
    """
    if not category:
        return 1.0
    weights = CATEGORY_DIMENSION_WEIGHTS.get(category, {})
    dim_key = dimension_name[:2]  # "D1", "D2", ...
    w = weights.get(dim_key, 1.0)
    # Educational adversarial: re-enable D5 (Code Teaching)
    if w == 0.0 and dim_key == "D5" and category == "adversarial" and requires_code:
        return 1.0
    return w


# Rubric JSON directory
_RUBRIC_DIR = Path(__file__).parent.parent / "rubrics"

# Cache loaded rubrics
_rubric_cache: dict[str, dict] = {}


# ──────────────────────────────────────────────────────────────
# Rubric loading from JSON files
# ──────────────────────────────────────────────────────────────


def load_rubric(persona_level: str) -> dict:
    """Load a persona-specific rubric from JSON file.

    Args:
        persona_level: One of 'beginner', 'intermediate', 'advanced'.

    Returns:
        Dict with 'persona_level', 'description', 'dimensions'.

    Raises:
        FileNotFoundError: If rubric JSON file does not exist.
    """
    if persona_level in _rubric_cache:
        return _rubric_cache[persona_level]

    rubric_path = _RUBRIC_DIR / f"rubric_{persona_level}.json"
    if not rubric_path.exists():
        raise FileNotFoundError(
            f"Rubric file not found: {rubric_path}\n"
            f"Please ensure evaluation/rubrics/rubric_{persona_level}.json exists.\n"
            f"Available rubric files: {list(_RUBRIC_DIR.glob('rubric_*.json'))}"
        )

    with open(rubric_path) as f:
        rubric = json.load(f)

    _rubric_cache[persona_level] = rubric
    return rubric


def _build_criteria_from_rubric(dimension_name: str, rubric: dict) -> str:
    """Build the criteria string for one dimension from a rubric JSON.

    Converts the structured JSON rubric into a formatted criteria prompt
    that ConversationalGEval can use as evaluation instructions.

    Args:
        dimension_name: e.g. "D1_level_detection"
        rubric: Loaded rubric dict from JSON file.

    Returns:
        Formatted criteria string with persona context and scoring guidance.
    """
    dim_data = rubric["dimensions"].get(dimension_name)
    if not dim_data:
        raise ValueError(
            f"Dimension '{dimension_name}' not found in rubric for "
            f"persona_level='{rubric.get('persona_level', 'unknown')}'. "
            f"Available dimensions: {list(rubric['dimensions'].keys())}"
        )

    # Build scoring guidance text from the 1-10 scale
    scoring_lines = []
    for score, description in sorted(
        dim_data["scoring_guidance"].items(), key=lambda x: int(x[0])
    ):
        scoring_lines.append(f"Score {score}: {description}")

    # Human-readable dimension label
    dim_label = (
        dimension_name.replace("_", " ")
        .replace("D1 ", "Level ")
        .replace("D2 ", "Language ")
        .replace("D3 ", "Scaffolding ")
        .replace("D4 ", "Domain ")
        .replace("D5 ", "Code ")
        .replace("D6 ", "Empathetic ")
        .replace("D7 ", "Safety ")
    )

    return (
        f"=== STUDENT PERSONA CONTEXT ===\n"
        f"Persona level: {rubric['persona_level'].upper()}\n"
        f"{rubric.get('description', '')}\n\n"
        f"=== DIMENSION EVALUATION: {dim_label} ===\n"
        f"CRITERIA: {dim_data['criteria']}\n\n"
        f"SCORING RUBRIC (1-10 scale):\n"
        f"{chr(10).join(scoring_lines)}\n\n"
        f"EVALUATION INSTRUCTIONS:\n"
        f"- Focus ONLY on the tutor's (assistant's) messages, not the student's.\n"
        f"- Consider the ENTIRE conversation.\n"
        f"- A score of 5 is baseline adequate; reserve 9-10 for truly exceptional performance.\n"
        f"- Weight: {dim_data.get('weight', 1.0)}"
    )


# ──────────────────────────────────────────────────────────────
# Metric construction
# ──────────────────────────────────────────────────────────────


def _build_full_criteria(dimension_name: str, persona_level: str) -> str:
    """Build the full criteria string for one dimension + persona.

    Loads rubric from JSON file and formats it as evaluation criteria.
    """
    rubric = load_rubric(persona_level)
    return _build_criteria_from_rubric(dimension_name, rubric)


def create_tutor_geval_metrics(
    persona_level: str,
    model: Optional[str] = None,
    dimension_order: Optional[list[str]] = None,
) -> list["ConversationalGEval"]:
    """Create ConversationalGEval metric instances for the 7D rubric.

    Design doc §6.2: Each dimension uses a detailed 1-10 scoring rubric
    with persona-specific context injected into the criteria prompt.

    Args:
        persona_level: One of 'beginner', 'intermediate', 'advanced'.
        model: LLM model for evaluation judge.
        dimension_order: Optional ordered list of dimension names.
            If provided, metrics are created in this order (for shuffled judge runs).

    Returns:
        List of 7 ConversationalGEval metric instances.

    Raises:
        ImportError: If deepeval is not installed.
        FileNotFoundError: If rubric JSON file is missing.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    dims = dimension_order or DIMENSIONS
    metrics = []

    for dim_name in dims:
        criteria = _build_full_criteria(dim_name, persona_level)

        kwargs = {
            "name": dim_name,
            "criteria": criteria,
            "threshold": 0.5,
        }
        kwargs["model"] = resolve_deepeval_model(model)

        metrics.append(ConversationalGEval(**kwargs))

    return metrics


from evaluation.deepeval_metrics.async_utils import run_async as _run_async

# Dimensions evaluated against the enriched conversation (with tool-activity
# summaries).  D4/D5/D7 need to see tool outputs to verify domain accuracy,
# code quality, and safety-relevant tool behaviour.
_ENRICHED_DIMS = {"D4_domain_accuracy", "D5_code_teaching", "D7_safety_boundaries"}


def evaluate_tutor_dimensions(
    conversation_turns: list[dict],
    persona_level: str,
    scenario: Optional[str] = None,
    expected_outcome: Optional[str] = None,
    user_description: Optional[str] = None,
    model=None,
    conversational_test_case=None,
    num_judge_runs: int = NUM_JUDGE_RUNS,
    category: Optional[str] = None,
    requires_code: bool = False,
    abort_event: Optional[threading.Event] = None,
    enriched_conversation_turns: Optional[list[dict]] = None,
) -> dict[str, float]:
    """Evaluate tutoring dimensions with shuffled judge runs.

    Design doc §6.2: Judge runs 3 times with shuffled dimension order,
    scores averaged for stability (§4.1: "3x shuffled prompts").

    Two-tier conversation input:
      - **Teaching-quality dimensions** (D1, D2, D3, D6) are evaluated
        against the *original* ``conversation_turns`` so that tool-activity
        text does not interfere with assessment of the tutor's own language,
        scaffolding, and empathy.
      - **Competence dimensions** (D4, D5, D7) are evaluated against
        ``enriched_conversation_turns`` (which appends [Tool Activity]
        summaries to assistant turns) so the judge can verify domain
        accuracy and code quality against actual tool outputs.

    When ``enriched_conversation_turns`` is None, all dimensions fall back
    to ``conversation_turns`` (backward-compatible).

    Multi-model support: when ``model`` is a list of N model names, EACH
    model independently performs ``num_judge_runs`` shuffled runs across
    all active dimensions.  This yields N × num_judge_runs × dims LLM
    calls (e.g. 3 models × 3 runs × 7 dims = 63 calls), all executed in
    parallel.  Per-model dimension scores (averaged over their own
    shuffled runs) are returned under the ``_per_model`` key.  The final
    dimension scores are the cross-model average.

    Dimensions with weight=0 for the given category are skipped entirely
    (no API calls). Weights are stored in CATEGORY_DIMENSION_WEIGHTS.

    Args:
        conversation_turns: Original conversation (no tool summaries).
        enriched_conversation_turns: Conversation with [Tool Activity]
            summaries appended to assistant turns.  Used for D4/D5/D7.
            Falls back to conversation_turns when None.
        persona_level: Student persona level ('beginner', 'intermediate', 'advanced').
        scenario: Description of the tutoring scenario.
        expected_outcome: Expected learning outcome.
        user_description: Description of the student persona (from build_user_description).
        model: LLM model for judge — a single name string, a list of
            model name strings (each runs ``num_judge_runs``), or None
            (uses EVAL_DEFAULT_MODELS list).
        conversational_test_case: Pre-built ConversationalTestCase from ConversationSimulator.
            If provided, uses this directly instead of building from conversation_turns.
        num_judge_runs: Number of shuffled judge runs *per model* (default: 3).
        category: TaskCategory.value string for per-category dimension weighting.
        requires_code: Whether the task expects code output.  Used to
            re-enable D5 (Code Teaching) for educational adversarial tasks.

    Returns:
        Dict mapping dimension name to averaged score (0-1).
        Skipped dimensions (weight=0) are not included in the dict.
        When multi-model is used, an extra ``_per_model`` key maps each
        model name to its own {dimension: score} dict (averaged over that
        model's shuffled runs).
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    # ── Resolve model list ──
    from config.llm_config import EVAL_DEFAULT_MODELS

    multi_model = False
    if isinstance(model, list) and len(model) > 0:
        eval_models = model
        multi_model = True
    elif model is None:
        eval_models = list(EVAL_DEFAULT_MODELS)
        multi_model = len(eval_models) > 1
    else:
        # Single string override
        eval_models = [model]

    model_names = [m or "default" for m in eval_models]

    # Filter out dimensions with weight=0 for this category.
    # For educational adversarial tasks (requires_code=true), D5 is
    # re-enabled via get_dimension_weight's requires_code parameter.
    active_dims = [
        d
        for d in DIMENSIONS
        if get_dimension_weight(category, d, requires_code=requires_code) > 0.0
    ]
    skipped_dims = [d for d in DIMENSIONS if d not in active_dims]
    if skipped_dims:
        print(
            f"    Skipping dimensions (weight=0 for {category}): "
            f"{', '.join(d.split('_')[0] for d in skipped_dims)}"
        )

    total_calls = len(eval_models) * num_judge_runs * len(active_dims)
    print(
        f"    Evaluation plan: {len(eval_models)} model(s) × "
        f"{num_judge_runs} shuffled runs × {len(active_dims)} dims "
        f"= {total_calls} judge calls"
    )

    import copy
    import time as _time

    # ── Build base test cases ──
    # Two-tier: original conversation for teaching-quality dims (D1/D2/D3/D6),
    # enriched conversation for competence dims (D4/D5/D7).
    if conversational_test_case is not None:
        base_tc_original = conversational_test_case
        base_tc_enriched = conversational_test_case  # caller pre-built
    else:
        _tc_kwargs = dict(
            scenario=scenario,
            expected_outcome=expected_outcome,
            user_description=user_description,
        )
        turns_original = [
            Turn(role=t["role"], content=t["content"]) for t in conversation_turns
        ]
        base_tc_original = ConversationalTestCase(
            turns=turns_original,
            **_tc_kwargs,
        )
        # Enriched version — falls back to original when not provided.
        _enriched_src = enriched_conversation_turns or conversation_turns
        if _enriched_src is conversation_turns:
            base_tc_enriched = base_tc_original
        else:
            turns_enriched = [
                Turn(role=t["role"], content=t["content"]) for t in _enriched_src
            ]
            base_tc_enriched = ConversationalTestCase(
                turns=turns_enriched,
                **_tc_kwargs,
            )

    # ── Build ALL metric instances (models × runs × dims) ──
    # Each metric gets its own deep-copied test case to avoid state
    # conflicts when DeepEval's a_measure() mutates internal fields.
    # Dimensions in _ENRICHED_DIMS get the enriched test case; others
    # get the original (no tool-activity summaries).
    all_metrics = []
    all_test_cases = []
    # task_keys: (model_name, run_idx, dim_name) for each metric
    task_keys: list[tuple[str, int, str]] = []

    for model_idx, current_model in enumerate(eval_models):
        mname = model_names[model_idx]
        for run_idx in range(num_judge_runs):
            shuffled_dims = active_dims.copy()
            random.shuffle(shuffled_dims)

            print(
                f"    [{mname}] run {run_idx + 1}/{num_judge_runs} "
                f"(order: {', '.join(d.split('_')[0] for d in shuffled_dims)})"
            )

            metrics = create_tutor_geval_metrics(
                persona_level,
                model=current_model,
                dimension_order=shuffled_dims,
            )
            for metric in metrics:
                all_metrics.append(metric)
                # Route: enriched test case for D4/D5/D7, original for rest
                base = (
                    base_tc_enriched
                    if metric.name in _ENRICHED_DIMS
                    else base_tc_original
                )
                all_test_cases.append(copy.deepcopy(base))
                task_keys.append((mname, run_idx, metric.name))

    # ── Run ALL judge calls in parallel with concurrency limit ──
    print(
        f"    Running {len(all_metrics)} judge calls in parallel "
        f"(concurrency={_CONCURRENCY})..."
    )
    t0 = _time.time()

    _abort = abort_event if abort_event is not None else threading.Event()
    _first_error: list[Exception] = []
    _fallback_count = [0]  # mutable counter for nonlocal capture

    async def _run_all():
        sem = asyncio.Semaphore(_CONCURRENCY)

        _MAX_RETRIES = 2

        async def _guarded(metric, tc, model_name):
            if _abort.is_set():
                return _ABORT_SENTINEL
            async with sem:
                if _abort.is_set():
                    return _ABORT_SENTINEL
                last_exc = None
                raw_text = None
                for attempt in range(_MAX_RETRIES + 1):
                    try:
                        return await metric.a_measure(tc)
                    except Exception as e:
                        last_exc = e
                        # Capture raw output saved by patched trimAndLoadJson
                        if hasattr(metric, "_raw_failed_response"):
                            raw_text = metric._raw_failed_response
                            del metric._raw_failed_response
                        if attempt < _MAX_RETRIES and "invalid JSON" in str(e):
                            await asyncio.sleep(1)
                            continue
                        # ── Retries exhausted: try fallback ──
                        # Layer 1: extract score from raw prose
                        if raw_text:
                            extracted = _extract_score_from_prose(raw_text)
                            if extracted:
                                score, reason = extracted
                                metric.score = score / 10.0
                                metric.reason = f"[FALLBACK-EXTRACT] {reason}"
                                metric.evaluation_cost = 0.0
                                _fallback_count[0] += 1
                                return score / 10.0
                        # Layer 2: direct GPTModel call with robust parser
                        try:
                            score, reason = await _fallback_direct_eval(
                                metric, tc, model_name
                            )
                            metric.score = score / 10.0
                            metric.reason = f"[FALLBACK-DIRECT] {reason}"
                            metric.evaluation_cost = 0.0
                            _fallback_count[0] += 1
                            return score / 10.0
                        except Exception:
                            pass
                        # All fallbacks failed — abort this evaluator
                        _abort.set()
                        if not _first_error:
                            _first_error.append(last_exc)
                        return _ABORT_SENTINEL

        return await asyncio.gather(
            *[
                _guarded(m, tc, mk[0])
                for m, tc, mk in zip(all_metrics, all_test_cases, task_keys)
            ],
            return_exceptions=True,
        )

    results = _run_async(_run_all())
    elapsed = _time.time() - t0

    aborted = sum(1 for r in results if r is _ABORT_SENTINEL)
    if aborted:
        print(
            f"    Tutor eval: {len(all_metrics) - aborted}/{len(all_metrics)} completed, "
            f"{aborted} aborted in {elapsed:.1f}s"
        )
    else:
        fb = _fallback_count[0]
        fb_msg = f" ({fb} via fallback)" if fb else ""
        print(
            f"    Completed {len(all_metrics)} judge calls in "
            f"{elapsed:.1f}s{fb_msg}"
        )

    # If any coroutine failed, propagate the error
    if _first_error:
        raise _first_error[0]

    # ── Accumulate scores and costs by (model, dimension) ──
    model_accumulated: dict[str, dict[str, list[float]]] = {
        name: {d: [] for d in active_dims} for name in model_names
    }
    model_costs: dict[str, list[float]] = {name: [] for name in model_names}

    for i, (mname, run_idx, dim_name) in enumerate(task_keys):
        if results[i] is _ABORT_SENTINEL or isinstance(results[i], Exception):
            raise RuntimeError(
                f"Unexpected abort/error in Tutor [{mname}] {dim_name} run {run_idx + 1}"
            )
        else:
            raw_score = all_metrics[i].score
            cost = all_metrics[i].evaluation_cost or 0.0
            # §6.2: "Each dimension: 1-10 scale, normalized to 0-1."
            if raw_score > 1.0:
                raw_score = raw_score / 10.0
            score = max(0.0, min(1.0, raw_score))

        model_accumulated[mname][dim_name].append(score)
        model_costs[mname].append(cost)

    # ── Per-model dimension scores (average over shuffled runs) ──
    per_model: dict[str, dict[str, float]] = {}
    for mname in model_names:
        per_model[mname] = {}
        for dim_name in active_dims:
            s = model_accumulated[mname][dim_name]
            per_model[mname][dim_name] = round(sum(s) / len(s), 4) if s else 0.5

    # ── Final dimension scores = average across models ──
    final_scores: dict = {}
    for dim_name in active_dims:
        model_avgs = [per_model[mname][dim_name] for mname in model_names]
        final_scores[dim_name] = round(sum(model_avgs) / len(model_avgs), 4)

    # ── Cost tracking ──
    total_cost = sum(c for costs in model_costs.values() for c in costs)
    final_scores["_eval_cost"] = round(total_cost, 6)
    final_scores["_eval_cost_by_model"] = {
        m: round(sum(costs), 6) for m, costs in model_costs.items()
    }

    if multi_model:
        final_scores["_per_model"] = per_model

    final_scores["_fallback_count"] = _fallback_count[0]

    return final_scores


def compute_tutor_score(
    dimension_scores: dict[str, float],
    category: Optional[str] = None,
    requires_code: bool = False,
) -> float:
    """Compute the aggregate tutor score from dimension scores.

    Uses per-category dimension weights for weighted averaging.
    Dimensions not present in dimension_scores (e.g., skipped due to
    weight=0) are excluded from the computation.

    Args:
        dimension_scores: Dict mapping dimension name to score (0-1).
        category: TaskCategory.value for per-category weighting.
        requires_code: Whether the task expects code output (passed
            through to get_dimension_weight for D5 override).

    Returns:
        Weighted average score across all evaluated dimensions (0-1).
    """
    if not dimension_scores:
        return 0.0

    weighted_sum = 0.0
    weight_total = 0.0
    for dim_name, score in dimension_scores.items():
        if dim_name.startswith("_") or not isinstance(score, (int, float)):
            continue
        w = get_dimension_weight(category, dim_name, requires_code=requires_code)
        weighted_sum += w * score
        weight_total += w

    if weight_total == 0.0:
        return 0.0
    return round(weighted_sum / weight_total, 4)
