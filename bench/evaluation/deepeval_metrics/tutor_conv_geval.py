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
    model: Optional[str] = None,
    conversational_test_case=None,
    num_judge_runs: int = NUM_JUDGE_RUNS,
    category: Optional[str] = None,
) -> dict[str, float]:
    """Evaluate tutoring dimensions with shuffled judge runs.

    Design doc §6.2: Judge runs 3 times with shuffled dimension order,
    scores averaged for stability (§4.1: "3x shuffled prompts").

    Dimensions with weight=0 for the given category are skipped entirely
    (no API calls). Weights are stored in CATEGORY_DIMENSION_WEIGHTS.

    Args:
        conversation_turns: List of {"role": str, "content": str} dicts.
        persona_level: Student persona level ('beginner', 'intermediate', 'advanced').
        scenario: Description of the tutoring scenario.
        expected_outcome: Expected learning outcome.
        user_description: Description of the student persona (from build_user_description).
        model: LLM model for judge.
        conversational_test_case: Pre-built ConversationalTestCase from ConversationSimulator.
            If provided, uses this directly instead of building from conversation_turns.
        num_judge_runs: Number of shuffled judge runs (default: 3 per design doc).
        category: TaskCategory.value string for per-category dimension weighting.

    Returns:
        Dict mapping dimension name to averaged score (0-1).
        Skipped dimensions (weight=0) are not included in the dict.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    # Filter out dimensions with weight=0 for this category
    active_dims = [d for d in DIMENSIONS if get_dimension_weight(category, d) > 0.0]
    skipped_dims = [d for d in DIMENSIONS if d not in active_dims]
    if skipped_dims:
        print(
            f"    Skipping dimensions (weight=0 for {category}): "
            f"{', '.join(d.split('_')[0] for d in skipped_dims)}"
        )

    # Build or reuse test case
    if conversational_test_case is not None:
        test_case = conversational_test_case
    else:
        turns = [Turn(role=t["role"], content=t["content"]) for t in conversation_turns]
        test_case = ConversationalTestCase(
            turns=turns,
            scenario=scenario,
            expected_outcome=expected_outcome,
            user_description=user_description,
        )

    # Build metric instances only for active dimensions
    all_metrics = []
    task_keys = []  # (run_idx, dim_name) for each metric

    for run_idx in range(num_judge_runs):
        shuffled_dims = active_dims.copy()
        random.shuffle(shuffled_dims)

        print(
            f"    Judge run {run_idx + 1}/{num_judge_runs} "
            f"(order: {', '.join(d.split('_')[0] for d in shuffled_dims)})"
        )

        metrics = create_tutor_geval_metrics(
            persona_level,
            model=model,
            dimension_order=shuffled_dims,
        )
        for metric in metrics:
            all_metrics.append(metric)
            task_keys.append((run_idx, metric.name))

    # Run all judge calls in parallel via asyncio.gather + a_measure
    total_calls = len(all_metrics)
    print(f"    Running {total_calls} judge calls in parallel...")

    async def _run_all():
        return await asyncio.gather(
            *[m.a_measure(test_case) for m in all_metrics],
            return_exceptions=True,
        )

    results = _run_async(_run_all())

    # Accumulate scores by dimension name
    accumulated_scores: dict[str, list[float]] = {d: [] for d in active_dims}
    for i, (run_idx, dim_name) in enumerate(task_keys):
        if isinstance(results[i], Exception):
            accumulated_scores[dim_name].append(0.5)
            print(f"    Warning: {dim_name} run {run_idx + 1} failed: {results[i]}")
        else:
            raw_score = all_metrics[i].score
            # §6.2: "Each dimension: 1-10 scale, normalized to 0-1."
            # DeepEval may return scores in 0-1 or 0-10 range depending on version.
            # Normalize: if score > 1.0, assume it's on a 1-10 scale.
            if raw_score > 1.0:
                raw_score = raw_score / 10.0
            accumulated_scores[dim_name].append(max(0.0, min(1.0, raw_score)))

    # Average across runs
    final_scores = {}
    for dim_name in active_dims:
        scores = accumulated_scores[dim_name]
        if scores:
            final_scores[dim_name] = round(sum(scores) / len(scores), 4)
        else:
            final_scores[dim_name] = 0.5

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
