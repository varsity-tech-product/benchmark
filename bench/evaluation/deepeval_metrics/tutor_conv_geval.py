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
from pathlib import Path
from typing import Optional

import nest_asyncio
from config.llm_config import resolve_deepeval_model

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


def get_dimension_weight(category: Optional[str], dimension_name: str) -> float:
    """Get the weight for a specific dimension given a task category.

    Args:
        category: TaskCategory.value string, or None for default weights.
        dimension_name: Full dimension name (e.g. "D5_code_teaching").

    Returns:
        Weight in [0.0, 1.0]. Defaults to 1.0 if category is unknown.
    """
    if not category:
        return 1.0
    weights = CATEGORY_DIMENSION_WEIGHTS.get(category, {})
    dim_key = dimension_name[:2]  # "D1", "D2", ...
    return weights.get(dim_key, 1.0)


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


def _run_async(coro):
    """Run an async coroutine from synchronous code, handling existing event loops."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        nest_asyncio.apply()
        return loop.run_until_complete(coro)
    else:
        return asyncio.run(coro)


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
) -> dict[str, float]:
    """Evaluate tutoring dimensions with shuffled judge runs.

    Design doc §6.2: Judge runs 3 times with shuffled dimension order,
    scores averaged for stability (§4.1: "3x shuffled prompts").

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
        conversation_turns: List of {"role": str, "content": str} dicts.
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

    # Filter out dimensions with weight=0 for this category
    active_dims = [d for d in DIMENSIONS if get_dimension_weight(category, d) > 0.0]
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

    # Build base test case
    if conversational_test_case is not None:
        base_test_case = conversational_test_case
    else:
        turns = [Turn(role=t["role"], content=t["content"]) for t in conversation_turns]
        base_test_case = ConversationalTestCase(
            turns=turns,
            scenario=scenario,
            expected_outcome=expected_outcome,
            user_description=user_description,
        )

    # ── Build ALL metric instances (models × runs × dims) ──
    # Each metric gets its own deep-copied test case to avoid state
    # conflicts when DeepEval's a_measure() mutates internal fields.
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
                all_test_cases.append(copy.deepcopy(base_test_case))
                task_keys.append((mname, run_idx, metric.name))

    # ── Run ALL judge calls in parallel with concurrency limit ──
    _CONCURRENCY = 20  # avoid OpenRouter rate-limit throttling
    print(
        f"    Running {len(all_metrics)} judge calls in parallel "
        f"(concurrency={_CONCURRENCY})..."
    )
    t0 = _time.time()

    async def _run_all():
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _limited(metric, tc):
            async with sem:
                return await metric.a_measure(tc)

        return await asyncio.gather(
            *[_limited(m, tc) for m, tc in zip(all_metrics, all_test_cases)],
            return_exceptions=True,
        )

    results = _run_async(_run_all())
    elapsed = _time.time() - t0
    print(f"    Completed {len(all_metrics)} judge calls in {elapsed:.1f}s")

    # ── Accumulate scores by (model, dimension) ──
    # model_accumulated[model_name][dim_name] = list of scores across runs
    model_accumulated: dict[str, dict[str, list[float]]] = {
        name: {d: [] for d in active_dims} for name in model_names
    }

    for i, (mname, run_idx, dim_name) in enumerate(task_keys):
        if isinstance(results[i], Exception):
            score = 0.5
            print(
                f"    Warning: [{mname}] {dim_name} run {run_idx + 1} "
                f"failed: {results[i]}"
            )
        else:
            raw_score = all_metrics[i].score
            # §6.2: "Each dimension: 1-10 scale, normalized to 0-1."
            if raw_score > 1.0:
                raw_score = raw_score / 10.0
            score = max(0.0, min(1.0, raw_score))

        model_accumulated[mname][dim_name].append(score)

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

    if multi_model:
        final_scores["_per_model"] = per_model

    return final_scores


def compute_tutor_score(
    dimension_scores: dict[str, float],
    category: Optional[str] = None,
) -> float:
    """Compute the aggregate tutor score from dimension scores.

    Uses per-category dimension weights for weighted averaging.
    Dimensions not present in dimension_scores (e.g., skipped due to
    weight=0) are excluded from the computation.

    Args:
        dimension_scores: Dict mapping dimension name to score (0-1).
        category: TaskCategory.value for per-category weighting.

    Returns:
        Weighted average score across all evaluated dimensions (0-1).
    """
    if not dimension_scores:
        return 0.0

    weighted_sum = 0.0
    weight_total = 0.0
    for dim_name, score in dimension_scores.items():
        w = get_dimension_weight(category, dim_name)
        weighted_sum += w * score
        weight_total += w

    if weight_total == 0.0:
        return 0.0
    return round(weighted_sum / weight_total, 4)
