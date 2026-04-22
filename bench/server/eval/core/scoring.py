"""Score aggregation and KPI computation for QuantTutorBench.

Design doc §6.3: Scoring Architecture
    Task Score = 0.70 * Quant Agent Score + 0.30 * Tutor Score
    Quant Agent Score = 0.50 * Result Sub-score + 0.50 * Process Sub-score
    Tutor Score = average of 6D rubric scores (each 0-1)

Design doc §6.4: Benchmark-Level KPIs
    OAS  = Overall Agent Score (weighted average across all tasks)
    QAI  = Quant Agent Index (average quant scores)
    TEI  = Tutoring Effectiveness Index (average tutor rubric scores)
    AS   = Adaptiveness Score (tutor score variance across persona variants)
    PMS  = Process Mastery Score (average process quality score)

Design doc §6.5: Statistical Reporting
    pass@k and pass^k for each difficulty level
    3 trials per task, best-run selection
    Confidence intervals on all aggregate metrics
"""

import math
import statistics
from collections import defaultdict
from typing import Any, Optional

QUANT_WEIGHT = 0.70
TUTOR_WEIGHT = 0.30
RESULT_WEIGHT = 0.50
PROCESS_WEIGHT = 0.50
LAYER1_RESULT_WEIGHT = 0.40  # λ: Layer 1 contribution to Result Sub-score


def _score_value(value: Any) -> float | None:
    """Return a real numeric score, preserving missing/non-computable as None."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _round_score(value: Any) -> float | None:
    score = _score_value(value)
    return round(score, 4) if score is not None else None


def _mean_score(values: list[Any]) -> float | None:
    scores = [_score_value(v) for v in values]
    clean = [v for v in scores if v is not None]
    return round(statistics.mean(clean), 4) if clean else None


def _track_score(track: Any) -> float | None:
    if track is None:
        return None
    if isinstance(track, dict):
        return _score_value(track.get("score"))
    return _score_value(getattr(track, "score", None))


def _normalized_mode(eval_mode: str) -> str:
    return {
        "qr_only": "qr",
        "qp_only": "qp",
        "tutor_only": "tutor",
    }.get(eval_mode, eval_mode)


def compute_overall(
    qr: Any | None,
    qp: Any | None,
    tutor: Any | None,
    eval_mode: str = "full",
) -> float | None:
    """Compute overall score from requested tracks.

    Missing requested tracks are not treated as zero. They make the aggregate
    not computable, matching the v6 eval architecture.
    """

    mode = _normalized_mode(eval_mode)
    qr_score = _track_score(qr)
    qp_score = _track_score(qp)
    tutor_score = _track_score(tutor)

    if mode == "qr":
        return _round_score(qr_score)
    if mode == "qp":
        return _round_score(qp_score)
    if mode == "tutor":
        return _round_score(tutor_score)

    if qr_score is None or qp_score is None or tutor_score is None:
        return None
    quant_score = RESULT_WEIGHT * qr_score + PROCESS_WEIGHT * qp_score
    return round(QUANT_WEIGHT * quant_score + TUTOR_WEIGHT * tutor_score, 4)


def compute_task_score(
    quant_result_score: float | None,
    quant_process_score: float | None,
    tutor_dimension_scores: dict[str, float],
    category: Optional[str] = None,
    requires_code: bool = False,
    eval_mode: str = "full",
) -> dict:
    """Compute the overall task score.

    Design doc §6.3:
    Task Score = 0.70 × Quant Agent Score + 0.30 × Tutor Score
    Quant Agent Score = 0.50 × Result + 0.50 × Process

    When a requested component is missing, the aggregate is not computable
    and returns ``None``. There is deliberately no "missing track = 0" rule.

    Tutor Score uses per-category binary dimension weights (0=skip, 1=evaluate)
    for averaging (see CATEGORY_DIMENSION_WEIGHTS in tutor_conv_geval.py).

    Args:
        quant_result_score: Score from eval scripts (0-1).
        quant_process_score: Score from reformed process metrics (0-1).
        tutor_dimension_scores: Dict of dimension_name -> score (0-1).
        category: TaskCategory.value for per-category tutor dimension weighting.
        requires_code: Whether the task expects code output (passed
            through to compute_tutor_score for D2 weight override in adversarial).
        eval_mode: "full" (default), "qr", "qp", or "tutor".

    Returns:
        Dict with all sub-scores and overall score.
    """
    from server.eval.judges.tutor_6d import compute_tutor_score

    mode = _normalized_mode(eval_mode)
    qr_score = _score_value(quant_result_score)
    qp_score = _score_value(quant_process_score)

    if mode == "qr":
        quant_score = qr_score
    elif mode == "qp":
        quant_score = qp_score
    elif mode == "tutor":
        quant_score = None
    else:
        quant_score = (
            RESULT_WEIGHT * qr_score + PROCESS_WEIGHT * qp_score
            if qr_score is not None and qp_score is not None
            else None
        )

    # Filter out internal metadata keys (_eval_cost, _eval_cost_by_model)
    clean_tutor = {
        k: v
        for k, v in tutor_dimension_scores.items()
        if not k.startswith("_") and isinstance(v, (int, float))
    }

    tutor_score = None
    if clean_tutor:
        tutor_score = compute_tutor_score(
            clean_tutor, category=category, requires_code=requires_code
        )

    if mode == "tutor":
        overall = tutor_score
    elif mode == "qr":
        overall = qr_score
    elif mode == "qp":
        overall = qp_score
    elif quant_score is None or tutor_score is None:
        overall = None
    else:
        overall = QUANT_WEIGHT * quant_score + TUTOR_WEIGHT * tutor_score

    return {
        "quant_result_score": _round_score(qr_score),
        "quant_process_score": _round_score(qp_score),
        "quant_agent_score": _round_score(quant_score),
        "tutor_score": _round_score(tutor_score),
        "tutor_dimension_scores": {k: round(v, 4) for k, v in clean_tutor.items()},
        "overall_score": _round_score(overall),
    }


def compute_benchmark_kpis(
    task_results: list[dict],
    task_result_objects: Optional[list] = None,
) -> dict:
    """Compute benchmark-level KPIs from all task results.

    Design doc §6.4: OAS, QAI, TEI, AS, PMS, Difficulty Curve.

    Args:
        task_results: List of task score dicts from compute_task_score.
        task_result_objects: Optional list of TaskResult objects for extended metrics.

    Returns:
        Dict with all benchmark-level KPIs.
    """
    if not task_results:
        return {"error": "No results to aggregate"}

    computable = [
        r for r in task_results if _score_value(r.get("overall_score")) is not None
    ]
    computable_objects = None
    if task_result_objects:
        computable_objects = [
            obj
            for obj, score in zip(task_result_objects, task_results)
            if _score_value(score.get("overall_score")) is not None
        ]
    if not computable:
        return {
            "error": "No computable results to aggregate",
            "total_tasks_evaluated": 0,
            "total_tasks_not_computable": len(task_results),
        }

    overall_scores = [r["overall_score"] for r in computable]
    quant_scores = [
        r["quant_agent_score"]
        for r in computable
        if _score_value(r.get("quant_agent_score")) is not None
    ]
    tutor_scores = [
        r["tutor_score"]
        for r in computable
        if _score_value(r.get("tutor_score")) is not None
    ]

    kpis = {
        # §6.4: Core KPIs
        "overall_agent_score": _mean_score(overall_scores),
        "quant_agent_index": _mean_score(quant_scores),
        "tutoring_effectiveness_index": _mean_score(tutor_scores),
        "total_tasks_evaluated": len(computable),
        "total_tasks_not_computable": len(task_results) - len(computable),
    }

    # Confidence intervals (95%)
    if len(overall_scores) > 1:
        kpis["oas_std"] = round(statistics.stdev(overall_scores), 4)
        kpis["oas_ci_95"] = round(
            1.96 * statistics.stdev(overall_scores) / math.sqrt(len(overall_scores)), 4
        )
        if len(quant_scores) > 1:
            kpis["qai_std"] = round(statistics.stdev(quant_scores), 4)
        if len(tutor_scores) > 1:
            kpis["tei_std"] = round(statistics.stdev(tutor_scores), 4)

    # §6.4: Adaptiveness Score (AS) — per-task tutor score variance across personas
    # Low variance = agent doesn't adapt (bad)
    if computable_objects:
        as_score = _compute_adaptiveness_score(computable_objects)
        if as_score is not None:
            kpis["adaptiveness_score"] = as_score

    # §6.4: Process Mastery Score (PMS) — average process quality score
    if computable_objects:
        pms = _compute_process_mastery_score(computable_objects)
        if pms is not None:
            kpis["process_mastery_score"] = pms

    # §6.4: Difficulty Curve — performance by difficulty level
    if computable_objects:
        difficulty_curve = _compute_difficulty_curve(computable_objects, computable)
        if difficulty_curve:
            kpis["difficulty_curve"] = difficulty_curve

    # §6.5: pass@k and pass^k aggregated
    if computable_objects:
        pass_metrics = _compute_pass_metrics(computable_objects, computable)
        if pass_metrics:
            kpis["pass_metrics"] = pass_metrics

    # §6.5: Total cost estimate
    if task_result_objects:
        total_cost = sum(getattr(r, "cost_usd", 0) for r in task_result_objects)
        if total_cost > 0:
            kpis["total_cost_usd"] = round(total_cost, 4)
            kpis["avg_cost_per_task_usd"] = round(
                total_cost / len(task_result_objects), 4
            )

    return kpis


def compute_combined_benchmark_kpis(
    layer2_task_results: list[dict] = None,
    layer2_result_objects: Optional[list] = None,
    layer1_mean_score: float = 0.0,
    layer1_summary: Optional[dict] = None,
    layers_evaluated: Optional[list[str]] = None,
) -> dict:
    """Compute benchmark KPIs with Layer 1 + Layer 2 blending.

    Design doc §5.0: Layer 1 feeds into the Quant Agent axis — specifically
    the result-based scoring component.

    Formula:
        OAS = 0.70 × QAI + 0.30 × TEI
        QAI = 0.50 × Result_Sub + 0.50 × Process_Sub
        Result_Sub = λ × Layer1_Result + (1-λ) × Layer2_Result  (λ = 0.40)
        Process_Sub = Layer 2 only
        TEI = Layer 2 only

    When only one layer is evaluated, the missing layer's contribution is
    omitted (Result_Sub uses whichever layer is available).
    """
    layers = layers_evaluated or []
    layer2_task_results = layer2_task_results or []
    layer2_computable = [
        r
        for r in layer2_task_results
        if _score_value(r.get("overall_score")) is not None
    ]
    layer2_computable_objects = None
    if layer2_result_objects:
        layer2_computable_objects = [
            obj
            for obj, score in zip(layer2_result_objects, layer2_task_results)
            if _score_value(score.get("overall_score")) is not None
        ]

    # Get Layer 2 base KPIs (AS, PMS, difficulty curve, pass@k)
    if layer2_computable:
        base_kpis = compute_benchmark_kpis(
            layer2_computable,
            task_result_objects=layer2_computable_objects,
        )
    else:
        base_kpis = {
            "overall_agent_score": None,
            "quant_agent_index": None,
            "tutoring_effectiveness_index": None,
            "total_tasks_evaluated": 0,
            "total_tasks_not_computable": len(layer2_task_results),
        }

    # Extract Layer 2 sub-score means
    l2_result_scores = (
        [r["quant_result_score"] for r in layer2_computable]
        if layer2_computable
        else []
    )
    l2_result_mean = _mean_score(l2_result_scores)
    l2_process_scores = (
        [r["quant_process_score"] for r in layer2_computable]
        if layer2_computable
        else []
    )
    l2_process_mean = _mean_score(l2_process_scores)
    tutor_scores = (
        [r["tutor_score"] for r in layer2_computable] if layer2_computable else []
    )
    tei = _mean_score(tutor_scores)
    layer1_score = _score_value(layer1_mean_score)

    # Blended Result Sub-score
    # Formula: Result_Sub = λ × Layer1 + (1-λ) × Layer2, where λ = 0.40
    # When a layer is absent, its contribution counts as 0 (not omitted).
    if "layer1" in layers and "layer2" in layers:
        if layer1_score is not None and l2_result_mean is not None:
            result_sub = (
                LAYER1_RESULT_WEIGHT * layer1_score
                + (1 - LAYER1_RESULT_WEIGHT) * l2_result_mean
            )
        else:
            result_sub = None
    elif "layer1" in layers:
        result_sub = layer1_score
    else:
        result_sub = l2_result_mean

    # Process Sub-score is Layer 2 only
    process_sub = l2_process_mean

    # Recompute QAI and OAS with blended result
    qai = (
        RESULT_WEIGHT * result_sub + PROCESS_WEIGHT * process_sub
        if result_sub is not None and process_sub is not None
        else None
    )
    oas = (
        QUANT_WEIGHT * qai + TUTOR_WEIGHT * tei
        if qai is not None and tei is not None
        else None
    )

    combined = {**base_kpis}
    combined["overall_agent_score"] = _round_score(oas)
    combined["quant_agent_index"] = _round_score(qai)
    combined["tutoring_effectiveness_index"] = _round_score(tei)
    combined["combined_result_subscore"] = _round_score(result_sub)
    combined["process_subscore"] = _round_score(process_sub)
    combined["layers_evaluated"] = layers

    if layer1_summary:
        combined["layer1_mean_score"] = _round_score(layer1_summary.get("mean_score"))
        combined["layer1_total_items"] = layer1_summary.get("total_items", 0)
        combined["layer1_by_category"] = layer1_summary.get("by_category", {})
        combined["layer1_by_difficulty"] = layer1_summary.get("by_difficulty", {})

    return combined


def _compute_adaptiveness_score(task_result_objects: list) -> Optional[float]:
    """Compute Adaptiveness Score: tutor score variance across persona variants.

    For each task, we compute the variance of tutor scores across different personas.
    High variance means the agent adapts its tutoring to different students (good).
    Low variance means it treats everyone the same (bad).

    Returns:
        Average per-task tutor score standard deviation, or None.
    """
    # Group by task_id
    by_task = defaultdict(list)
    for r in task_result_objects:
        if hasattr(r, "tutor_scores") and r.tutor_scores:
            clean_vals = [
                v
                for k, v in r.tutor_scores.items()
                if not k.startswith("_") and isinstance(v, (int, float))
            ]
            if not clean_vals:
                continue
            tutor_score = statistics.mean(clean_vals)
            by_task[r.task_id].append(tutor_score)

    # Compute per-task variance
    variances = []
    for task_id, scores in by_task.items():
        if len(scores) >= 2:
            variances.append(statistics.stdev(scores))

    if variances:
        return round(statistics.mean(variances), 4)
    return None


def _compute_process_mastery_score(task_result_objects: list) -> Optional[float]:
    """Compute Process Mastery Score: average process quality across tasks.

    Design doc §6.4: PMS = Average quant_process_score (reformed process aggregate).

    Returns:
        Average quant_process_score across all tasks, or None.
    """
    scores = []
    for r in task_result_objects:
        if hasattr(r, "quant_process_score"):
            score = _score_value(r.quant_process_score)
            if score is not None:
                scores.append(score)

    if scores:
        return round(statistics.mean(scores), 4)
    return None


def _compute_difficulty_curve(
    task_result_objects: list, task_results: list[dict]
) -> dict:
    """Compute performance by difficulty level.

    Design doc §6.4: Should decrease monotonically (easy > medium > hard).

    Returns:
        Dict mapping difficulty -> average overall score.
    """
    by_difficulty = defaultdict(list)
    for r_obj, r_score in zip(task_result_objects, task_results):
        difficulty = getattr(r_obj, "difficulty", "")
        if not difficulty:
            continue
        score = _score_value(r_score.get("overall_score"))
        if score is not None:
            by_difficulty[difficulty].append(score)

    if not by_difficulty:
        return {}

    return {k: round(statistics.mean(v), 4) for k, v in sorted(by_difficulty.items())}


def _compute_pass_metrics(task_result_objects: list, task_results: list[dict]) -> dict:
    """Compute pass@k and pass^k metrics.

    Design doc §6.5: pass@k and pass^k for each difficulty level.
    3 trials per task, best-run selection.

    Returns:
        Dict with pass@1, pass@3, pass^3, by difficulty.
    """
    # Group by task_id+persona_id (trials have different run_index)
    by_key = defaultdict(list)
    for r_obj, r_score in zip(task_result_objects, task_results):
        score = _score_value(r_score.get("overall_score"))
        if score is None:
            continue
        key = f"{r_obj.task_id}_{r_obj.persona_id}"
        by_key[key].append(score)

    if not by_key:
        return {}

    threshold = 0.5
    pass_at_1_list = []
    pass_at_3_list = []
    pass_power_3_list = []

    for key, scores in by_key.items():
        pass_at_1_list.append(compute_pass_at_k(scores, threshold, k=1))
        pass_at_3_list.append(compute_pass_at_k(scores, threshold, k=3))
        pass_power_3_list.append(compute_pass_power_k(scores, threshold))

    return {
        "pass_at_1": (
            round(statistics.mean(pass_at_1_list), 4) if pass_at_1_list else 0.0
        ),
        "pass_at_3": (
            round(statistics.mean(pass_at_3_list), 4) if pass_at_3_list else 0.0
        ),
        "pass_power_3": (
            round(statistics.mean(pass_power_3_list), 4) if pass_power_3_list else 0.0
        ),
    }


def compute_pass_at_k(scores: list[float], threshold: float = 0.5, k: int = 1) -> float:
    """Compute pass@k: did the agent pass at least once in k trials?

    Design doc §6.5: pass@k metric.

    Args:
        scores: List of scores from multiple trials.
        threshold: Minimum score to count as passing.
        k: Number of attempts allowed.

    Returns:
        1.0 if passed at least k times, else fraction.
    """
    if not scores:
        return 0.0
    passed = sum(1 for s in scores if s >= threshold)
    if passed >= k:
        return 1.0
    return passed / k


def compute_pass_power_k(scores: list[float], threshold: float = 0.5) -> float:
    """Compute pass^k: did the agent pass every single time?

    Design doc §6.5: pass^k metric (all trials must pass).

    Args:
        scores: List of scores from multiple trials.
        threshold: Minimum score to count as passing.

    Returns:
        1.0 if all trials passed, else 0.0.
    """
    if not scores:
        return 0.0
    return 1.0 if all(s >= threshold for s in scores) else 0.0
