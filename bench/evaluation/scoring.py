"""Score aggregation and KPI computation for QuantTutorBench.

Design doc §6.3: Scoring Architecture
    Task Score = 0.70 * Quant Agent Score + 0.30 * Tutor Score
    Quant Agent Score = 0.50 * Result Sub-score + 0.50 * Process Sub-score
    Tutor Score = average of 7D rubric scores (each 0-1)

Design doc §6.4: Benchmark-Level KPIs
    OAS  = Overall Agent Score (weighted average across all tasks)
    QAI  = Quant Agent Index (average quant scores)
    TEI  = Tutoring Effectiveness Index (average tutor rubric scores)
    AS   = Adaptiveness Score (tutor score variance across persona variants)
    TMS  = Tool Mastery Score (average tool precision × recall)

Design doc §6.5: Statistical Reporting
    pass@k and pass^k for each difficulty level
    3 trials per task, best-run selection
    Confidence intervals on all aggregate metrics
"""

import math
import statistics
from collections import defaultdict
from typing import Optional

QUANT_WEIGHT = 0.70
TUTOR_WEIGHT = 0.30
RESULT_WEIGHT = 0.50
PROCESS_WEIGHT = 0.50
LAYER1_RESULT_WEIGHT = 0.40  # λ: Layer 1 contribution to Result Sub-score


def compute_task_score(
    quant_result_score: float,
    quant_process_score: float,
    tutor_dimension_scores: dict[str, float],
    category: Optional[str] = None,
    dimension_relevance: Optional[dict[str, bool]] = None,
) -> dict:
    """Compute the overall task score.

    Design doc §6.3:
    Task Score = 0.70 × Quant Agent Score + 0.30 × Tutor Score
    Quant Agent Score = 0.50 × Result + 0.50 × Process

    Tutor Score uses per-category dimension weights for weighted averaging
    (see CATEGORY_DIMENSION_WEIGHTS in tutor_conv_geval.py).

    Args:
        quant_result_score: Score from eval scripts (0-1).
        quant_process_score: Score from DeepEval MCP metrics + tool precision/recall (0-1).
        tutor_dimension_scores: Dict of dimension_name -> score (0-1).
        category: TaskCategory.value for per-category tutor dimension weighting.
        dimension_relevance: Optional per-task dimension relevance mask that
            overrides category defaults.

    Returns:
        Dict with all sub-scores and overall score.
    """
    from evaluation.deepeval_metrics.tutor_conv_geval import compute_tutor_score

    quant_score = (
        RESULT_WEIGHT * quant_result_score + PROCESS_WEIGHT * quant_process_score
    )

    tutor_score = 0.0
    if tutor_dimension_scores:
        tutor_score = compute_tutor_score(
            tutor_dimension_scores,
            category=category,
            dimension_relevance=dimension_relevance,
        )

    overall = QUANT_WEIGHT * quant_score + TUTOR_WEIGHT * tutor_score

    return {
        "quant_result_score": round(quant_result_score, 4),
        "quant_process_score": round(quant_process_score, 4),
        "quant_agent_score": round(quant_score, 4),
        "tutor_score": round(tutor_score, 4),
        "tutor_dimension_scores": {
            k: round(v, 4) for k, v in tutor_dimension_scores.items()
        },
        "overall_score": round(overall, 4),
    }


def compute_benchmark_kpis(
    task_results: list[dict],
    task_result_objects: Optional[list] = None,
) -> dict:
    """Compute benchmark-level KPIs from all task results.

    Design doc §6.4: OAS, QAI, TEI, AS, TMS, Difficulty Curve.

    Args:
        task_results: List of task score dicts from compute_task_score.
        task_result_objects: Optional list of TaskResult objects for extended metrics.

    Returns:
        Dict with all benchmark-level KPIs.
    """
    if not task_results:
        return {"error": "No results to aggregate"}

    overall_scores = [r["overall_score"] for r in task_results]
    quant_scores = [r["quant_agent_score"] for r in task_results]
    tutor_scores = [r["tutor_score"] for r in task_results]

    kpis = {
        # §6.4: Core KPIs
        "overall_agent_score": round(statistics.mean(overall_scores), 4),
        "quant_agent_index": round(statistics.mean(quant_scores), 4),
        "tutoring_effectiveness_index": round(statistics.mean(tutor_scores), 4),
        "total_tasks_evaluated": len(task_results),
    }

    # Confidence intervals (95%)
    if len(overall_scores) > 1:
        kpis["oas_std"] = round(statistics.stdev(overall_scores), 4)
        kpis["oas_ci_95"] = round(
            1.96 * statistics.stdev(overall_scores) / math.sqrt(len(overall_scores)), 4
        )
        kpis["qai_std"] = round(statistics.stdev(quant_scores), 4)
        kpis["tei_std"] = round(statistics.stdev(tutor_scores), 4)

    # §6.4: Adaptiveness Score (AS) — per-task tutor score variance across personas
    # Low variance = agent doesn't adapt (bad)
    if task_result_objects:
        as_score = _compute_adaptiveness_score(task_result_objects)
        if as_score is not None:
            kpis["adaptiveness_score"] = as_score

    # §6.4: Tool Mastery Score (TMS) — average tool precision × recall
    if task_result_objects:
        tms = _compute_tool_mastery_score(task_result_objects)
        if tms is not None:
            kpis["tool_mastery_score"] = tms

    # §6.4: Difficulty Curve — performance by difficulty level
    if task_result_objects:
        difficulty_curve = _compute_difficulty_curve(task_result_objects, task_results)
        if difficulty_curve:
            kpis["difficulty_curve"] = difficulty_curve

    # §6.5: pass@k and pass^k aggregated
    if task_result_objects:
        pass_metrics = _compute_pass_metrics(task_result_objects, task_results)
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

    # Get Layer 2 base KPIs (AS, TMS, difficulty curve, pass@k)
    if layer2_task_results:
        base_kpis = compute_benchmark_kpis(
            layer2_task_results,
            task_result_objects=layer2_result_objects,
        )
    else:
        base_kpis = {
            "overall_agent_score": 0.0,
            "quant_agent_index": 0.0,
            "tutoring_effectiveness_index": 0.0,
            "total_tasks_evaluated": 0,
        }

    # Extract Layer 2 sub-score means
    l2_result_scores = (
        [r["quant_result_score"] for r in layer2_task_results]
        if layer2_task_results
        else []
    )
    l2_result_mean = statistics.mean(l2_result_scores) if l2_result_scores else 0.0
    l2_process_scores = (
        [r["quant_process_score"] for r in layer2_task_results]
        if layer2_task_results
        else []
    )
    l2_process_mean = statistics.mean(l2_process_scores) if l2_process_scores else 0.0
    tutor_scores = (
        [r["tutor_score"] for r in layer2_task_results] if layer2_task_results else []
    )
    tei = statistics.mean(tutor_scores) if tutor_scores else 0.0

    # Blended Result Sub-score
    # Formula: Result_Sub = λ × Layer1 + (1-λ) × Layer2, where λ = 0.40
    # When a layer is absent, its contribution counts as 0 (not omitted).
    if "layer1" in layers and "layer2" in layers:
        result_sub = (
            LAYER1_RESULT_WEIGHT * layer1_mean_score
            + (1 - LAYER1_RESULT_WEIGHT) * l2_result_mean
        )
    elif "layer1" in layers:
        # Only Layer 1: Layer 2 portion (0.6) is 0
        result_sub = LAYER1_RESULT_WEIGHT * layer1_mean_score
    else:
        # Only Layer 2: Layer 1 portion (0.4) is 0
        result_sub = (1 - LAYER1_RESULT_WEIGHT) * l2_result_mean

    # Process Sub-score is Layer 2 only
    process_sub = l2_process_mean

    # Recompute QAI and OAS with blended result
    qai = RESULT_WEIGHT * result_sub + PROCESS_WEIGHT * process_sub
    oas = QUANT_WEIGHT * qai + TUTOR_WEIGHT * tei

    combined = {**base_kpis}
    combined["overall_agent_score"] = round(oas, 4)
    combined["quant_agent_index"] = round(qai, 4)
    combined["tutoring_effectiveness_index"] = round(tei, 4)
    combined["combined_result_subscore"] = round(result_sub, 4)
    combined["process_subscore"] = round(process_sub, 4)
    combined["layers_evaluated"] = layers

    if layer1_summary:
        combined["layer1_mean_score"] = layer1_summary.get("mean_score", 0.0)
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
            tutor_score = statistics.mean(r.tutor_scores.values())
            by_task[r.task_id].append(tutor_score)

    # Compute per-task variance
    variances = []
    for task_id, scores in by_task.items():
        if len(scores) >= 2:
            variances.append(statistics.stdev(scores))

    if variances:
        return round(statistics.mean(variances), 4)
    return None


def _compute_tool_mastery_score(task_result_objects: list) -> Optional[float]:
    """Compute Tool Mastery Score: average tool precision × recall.

    Design doc §6.4: TMS = Average tool precision × recall.

    Returns:
        Average precision × recall across all tasks, or None.
    """
    pr_products = []
    for r in task_result_objects:
        if hasattr(r, "tool_metrics") and r.tool_metrics:
            # Track A optional-tools mode does not use expected-tool matching.
            # Use a neutral 0.5 baseline plus optional bonus/penalty signal.
            if r.tool_metrics.get("optional_tool_mode"):
                bonus = float(r.tool_metrics.get("tool_bonus", 0.0))
                penalty = float(r.tool_metrics.get("tool_penalty", 0.0))
                score = max(0.0, min(1.0, 0.5 + bonus - penalty))
                pr_products.append(score)
            else:
                p = r.tool_metrics.get("precision", 0)
                rec = r.tool_metrics.get("recall", 0)
                pr_products.append(p * rec)

    if pr_products:
        return round(statistics.mean(pr_products), 4)
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
        by_difficulty[difficulty].append(r_score["overall_score"])

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
        key = f"{r_obj.task_id}_{r_obj.persona_id}"
        by_key[key].append(r_score["overall_score"])

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
