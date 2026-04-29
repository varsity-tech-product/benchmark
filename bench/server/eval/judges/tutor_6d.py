"""6D Persona-aware tutoring rubric evaluation — single-call, checklist-based.

Each dimension is scored in one LLM call against a structured rubric
containing checklist conditions and a bottom-up evaluation process.
No Phase 1 "evaluation steps" generation — the rubric IS the evaluation.

Production logic:
    - 6 dimensions (D1-D6) with per-category binary weights
    - Single judge run per model per dimension (temp=0, deterministic)
    - Multi-model parallel evaluation
    - Two-tier conversation input (original vs enriched)
    - Per-dimension preprocessing (strip code blocks)
    - Abort signaling + explicit failure on invalid/missing dimensions
    - Per-run / per-model score transparency
"""

import asyncio
import logging
import threading
import time as _time
from typing import Optional

from server.eval.inputs.context_builder import (
    DIMENSION_PREPROCESS,
    ENRICHED_DIMS,
    build_tutor_context,
)
from server.eval.judges.runtime.async_utils import (
    ABORT_SENTINEL,
    get_eval_concurrency,
    run_async,
    set_eval_concurrency,  # noqa: F401 — re-export for callers
)
from server.eval.judges.runtime.call_policy import llm_call_with_retry
from server.eval.judges.runtime.conv_geval import (
    EvalTestCase,
    EwanConvGEval,
)
from server.eval.judges.runtime.model_resolver import (
    resolve_eval_model_list,
    resolve_ewan_model,
)

_log = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────
# 6 Dimensions (aligned with rubric_6d.json)
# ──────────────────────────────────────────────────────────────

DIMENSIONS = [
    "D1_finance_adaptation",
    "D2_code_adaptation",
    "D3_pedagogical_method",
    "D4_instructional_accuracy",
    "D5_empathetic_response",
    "D6_safety_boundaries",
]

NUM_JUDGE_RUNS = 1  # Each dim is an independent LLM call at temp=0; shuffling order is a no-op (ICC experiment: CV=0.0% on stable tasks)

# ──────────────────────────────────────────────────────────────
# Per-category dimension weights (binary: 0 = skip, 1 = evaluate)
#
# weight=0 → dimension not evaluated, no LLM call, not in Tutor score.
# weight=1 → dimension evaluated; rubric handles "no evidence" cases
#             (e.g. D6 standard: "Score 3 if no safety trigger appears").
# ──────────────────────────────────────────────────────────────

CATEGORY_DIMENSION_WEIGHTS: dict[str, dict[str, int]] = {
    "data_analysis": {
        "D1": 1,
        "D2": 1,
        "D3": 1,
        "D4": 1,
        "D5": 1,
        "D6": 0,
    },
    "strategy": {
        "D1": 1,
        "D2": 1,
        "D3": 1,
        "D4": 1,
        "D5": 1,
        "D6": 1,
    },
    "implementation": {
        "D1": 1,
        "D2": 1,
        "D3": 1,
        "D4": 1,
        "D5": 1,
        "D6": 0,
    },
    "backtest": {
        "D1": 1,
        "D2": 1,
        "D3": 1,
        "D4": 1,
        "D5": 1,
        "D6": 1,
    },
    "debug": {
        "D1": 1,
        "D2": 1,
        "D3": 1,
        "D4": 1,
        "D5": 1,
        "D6": 0,
    },
    "end_to_end": {
        "D1": 1,
        "D2": 1,
        "D3": 1,
        "D4": 1,
        "D5": 1,
        "D6": 1,
    },
    "adversarial": {
        "D1": 1,
        "D2": 0,
        "D3": 1,
        "D4": 1,
        "D5": 1,
        "D6": 1,
    },
}


def get_dimension_weight(
    category: Optional[str],
    dimension_name: str,
    requires_code: bool = False,
) -> float:
    """Return 1.0 (evaluate) or 0.0 (skip) for a dimension in a category."""
    if not category:
        return 1.0
    weights = CATEGORY_DIMENSION_WEIGHTS.get(category, {})
    dim_key = dimension_name[:2]
    w = weights.get(dim_key, 1)
    # Adversarial tasks that require code should evaluate D2 (code adaptation).
    if w == 0 and dim_key == "D2" and category == "adversarial" and requires_code:
        return 1.0
    return float(w)


# ──────────────────────────────────────────────────────────────
# Rubric loading — delegates to rubric_builder (6D)
# ──────────────────────────────────────────────────────────────

from server.eval.inputs.rubric_builder import (
    build_rubric_metadata,
    build_rubric_text,
    get_max_score,
    load_6d_rubric,
)

# ──────────────────────────────────────────────────────────────
# Metric construction
# ──────────────────────────────────────────────────────────────


def create_tutor_geval_metrics(
    persona_id: str,
    category: Optional[str] = None,
    model=None,
    dimension_order: Optional[list[str]] = None,
) -> list[EwanConvGEval]:
    """Create one EwanConvGEval metric per active dimension.

    Uses rubric_builder to select the correct scoring variant
    (per-quadrant / per-category / universal) and inject [FAMILIAR]/[UNFAMILIAR]
    concept lists from the persona file.

    Reads role/rules from rubric_6d.json top-level fields and passes
    them to EwanConvGEval for prompt construction.
    """
    rubric = load_6d_rubric()
    dims = dimension_order or DIMENSIONS

    # Role and rules from rubric JSON (shared across all 6D dimensions)
    rubric_role = rubric.get("role", "")
    rubric_rules_list = rubric.get("rules", [])
    rubric_rules = (
        "\n".join(f"- {r}" for r in rubric_rules_list)
        if isinstance(rubric_rules_list, list)
        else str(rubric_rules_list)
    )

    metrics = []
    for dim_name in dims:
        criteria = build_rubric_text(rubric, dim_name, persona_id, category)
        rubric_metadata = build_rubric_metadata(
            rubric,
            dim_name,
            rubric_name="tutor_6d",
            context_fields=["conversation"],
        )
        model_obj = resolve_ewan_model(model)
        max_score = get_max_score(rubric, dim_name)
        metrics.append(
            EwanConvGEval(
                name=dim_name,
                criteria=criteria,
                role=rubric_role,
                rules=rubric_rules,
                threshold=0.5,
                model=model_obj,
                max_score=max_score,
                rubric_metadata=rubric_metadata,
            )
        )
    return metrics


# ──────────────────────────────────────────────────────────────
# Conversation preprocessing — delegated to context_builder
# ──────────────────────────────────────────────────────────────
# Preprocessing (strip_code, enrichment selection) is now handled by
# context_builder.build_tutor_context(). DIMENSION_PREPROCESS remains imported
# at module scope for external callers.

# ──────────────────────────────────────────────────────────────
# Main evaluation entry point
# ──────────────────────────────────────────────────────────────


def evaluate_tutor_dimensions(
    conversation_turns: list[dict],
    persona_id: str,
    scenario: Optional[str] = None,
    user_description: Optional[str] = None,
    model=None,
    num_judge_runs: int = NUM_JUDGE_RUNS,
    category: Optional[str] = None,
    requires_code: bool = False,
    abort_event: Optional[threading.Event] = None,
    enriched_conversation_turns: Optional[list[dict]] = None,
    dimension_order: Optional[list[str]] = None,
    conversational_test_case=None,
) -> dict[str, float]:
    """Evaluate tutoring dimensions (6D rubric).

    Each dimension is an independent LLM call at temp=0, so dimension
    ordering has no effect on scores (validated by ICC experiment).

    Args:
        persona_id: Persona identifier (e.g. "developer_crossover") for
            rubric variant selection and [FAMILIAR]/[UNFAMILIAR] injection.
        dimension_order: If provided, evaluate only these dimensions
            (e.g. ["D3_pedagogical_method", "D4_instructional_accuracy"]).
            If None, evaluates all dimensions with non-zero weight.
    """
    # ── Resolve model list ──
    eval_models, multi_model = resolve_eval_model_list(model)
    model_names = [m or "default" for m in eval_models]

    # ── Filter dimensions ──
    if dimension_order:
        # Explicit dimension subset requested
        invalid_dims = [d for d in dimension_order if d not in DIMENSIONS]
        if invalid_dims:
            raise ValueError(f"Unknown Tutor dimension(s): {', '.join(invalid_dims)}")
        active_dims = list(dimension_order)
        skipped_dims = [d for d in DIMENSIONS if d not in active_dims]
        if skipped_dims:
            print(
                f"    Evaluating subset: {', '.join(d.split('_')[0] for d in active_dims)} "
                f"(skipping {', '.join(d.split('_')[0] for d in skipped_dims)})"
            )
    else:
        # Default: filter by category weight
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
        f"{num_judge_runs} run(s) × {len(active_dims)} dims "
        f"= {total_calls} judge calls"
    )

    # ── Build per-dimension test cases via context_builder ──
    _enriched_full = enriched_conversation_turns or conversation_turns
    _dim_test_cases: dict[str, EvalTestCase] = {}

    _context_cache: dict = {}
    _pp_log_lines: list[str] = []
    for dim in active_dims:
        # build_tutor_context handles enrichment selection + preprocessing
        context_str = build_tutor_context(
            conversation=conversation_turns,
            dimension_name=dim,
            enriched_conversation=_enriched_full,
            _cache=_context_cache,
        )
        _dim_test_cases[dim] = EvalTestCase(context=context_str)

        # Log preprocessing stats
        src = _enriched_full if dim in ENRICHED_DIMS else conversation_turns
        src_chars = sum(len(t["content"]) for t in src)
        out_chars = len(context_str)
        mode = DIMENSION_PREPROCESS.get(dim, "none")
        if mode != "none":
            _pp_log_lines.append(
                f"      {dim[:2]}: {src_chars:,} → {out_chars:,} chars ({mode})"
            )
    if _pp_log_lines:
        print("    Conversation preprocessing:")
        for ln in _pp_log_lines:
            print(ln)

    # ── Pre-compute rubric params once (shared across all models/runs) ──
    rubric = load_6d_rubric()
    rubric_role = rubric.get("role", "")
    rubric_rules_list = rubric.get("rules", [])
    rubric_rules = (
        "\n".join(f"- {r}" for r in rubric_rules_list)
        if isinstance(rubric_rules_list, list)
        else str(rubric_rules_list)
    )
    # Pre-build criteria and max_score per dimension
    _dim_criteria: dict[str, str] = {}
    _dim_max_score: dict[str, int] = {}
    for dim_name in active_dims:
        _dim_criteria[dim_name] = build_rubric_text(
            rubric, dim_name, persona_id, category
        )
        _dim_max_score[dim_name] = get_max_score(rubric, dim_name)

    # ── Build ALL metric instances (models × runs × dims) ──
    all_metrics = []
    all_test_cases = []
    task_keys: list[tuple[str, int, str]] = []

    for model_idx, current_model in enumerate(eval_models):
        mname = model_names[model_idx]
        model_obj = resolve_ewan_model(current_model)
        for run_idx in range(num_judge_runs):
            for dim_name in active_dims:
                metric = EwanConvGEval(
                    name=dim_name,
                    criteria=_dim_criteria[dim_name],
                    role=rubric_role,
                    rules=rubric_rules,
                    threshold=0.5,
                    model=model_obj,
                    max_score=_dim_max_score[dim_name],
                    rubric_metadata=build_rubric_metadata(
                        rubric,
                        dim_name,
                        rubric_name="tutor_6d",
                        context_fields=(
                            ["conversation", "tool_enriched_conversation"]
                            if dim_name in ENRICHED_DIMS
                            else ["conversation"]
                        ),
                    ),
                )
                all_metrics.append(metric)
                all_test_cases.append(_dim_test_cases[dim_name])
                task_keys.append((mname, run_idx, dim_name))

    # ── Run ALL judge calls in parallel ──
    print(
        f"    Running {len(all_metrics)} judge calls in parallel "
        f"(concurrency={get_eval_concurrency()})..."
    )
    t0 = _time.time()

    _abort = abort_event if abort_event is not None else threading.Event()

    async def _run_all():
        sem = asyncio.Semaphore(get_eval_concurrency())

        async def _guarded(metric, tc, model_name):
            if _abort.is_set():
                return ABORT_SENTINEL
            async with sem:
                if _abort.is_set():
                    return ABORT_SENTINEL
                result = await llm_call_with_retry(
                    lambda: metric,
                    tc,
                    dimension_name=getattr(metric, "name", "?"),
                )
                if result.get("status") == "failed":
                    _dim = getattr(metric, "name", "?")
                    _log.warning(
                        "Tutor dimension failed for [%s] %s — exc: %s",
                        model_name,
                        _dim,
                        result.get("error") or result.get("reason"),
                    )
                return result

        return await asyncio.gather(
            *[
                _guarded(m, tc, mk[0])
                for m, tc, mk in zip(all_metrics, all_test_cases, task_keys)
            ],
            return_exceptions=True,
        )

    results = run_async(_run_all())
    elapsed = _time.time() - t0

    aborted = sum(1 for r in results if r is ABORT_SENTINEL)
    if aborted:
        print(
            f"    Tutor eval: {len(all_metrics) - aborted}/{len(all_metrics)} completed, "
            f"{aborted} aborted in {elapsed:.1f}s"
        )
    else:
        print(f"    Completed {len(all_metrics)} judge calls in " f"{elapsed:.1f}s")

    # ── Accumulate scores by (model, dimension) ──
    model_accumulated: dict[str, dict[str, list[float]]] = {
        name: {d: [] for d in active_dims} for name in model_names
    }
    model_reasons: dict[str, dict[str, list[str]]] = {
        name: {d: [] for d in active_dims} for name in model_names
    }
    model_evidences: dict[str, dict[str, list[list[str]]]] = {
        name: {d: [] for d in active_dims} for name in model_names
    }
    model_run_indices: dict[str, dict[str, list[int]]] = {
        name: {d: [] for d in active_dims} for name in model_names
    }
    model_costs: dict[str, list[float]] = {name: [] for name in model_names}
    dim_errors: dict[str, list[str]] = {d: [] for d in active_dims}

    for i, (mname, run_idx, dim_name) in enumerate(task_keys):
        if results[i] is ABORT_SENTINEL:
            dim_errors[dim_name].append(
                f"[{mname}] run_{run_idx}: aborted before evaluation"
            )
            continue
        if isinstance(results[i], Exception):
            dim_errors[dim_name].append(f"[{mname}] run_{run_idx}: {results[i]}")
            continue
        if isinstance(results[i], dict) and results[i].get("score") is None:
            dim_errors[dim_name].append(
                f"[{mname}] run_{run_idx}: "
                f"{results[i].get('error') or results[i].get('reason')}"
            )
            model_costs[mname].append(results[i].get("_eval_cost", 0.0))
            continue
        raw_score = (
            results[i].get("score")
            if isinstance(results[i], dict)
            else all_metrics[i].score
        )
        cost = (
            results[i].get("_eval_cost", 0.0)
            if isinstance(results[i], dict)
            else all_metrics[i].evaluation_cost or 0.0
        )
        reason = (
            results[i].get("reason", "")
            if isinstance(results[i], dict)
            else getattr(all_metrics[i], "reason", None) or ""
        )
        evidence = (
            results[i].get("evidence", [])
            if isinstance(results[i], dict)
            else getattr(all_metrics[i], "evidence", []) or []
        )
        score = max(0.0, min(1.0, raw_score))
        model_accumulated[mname][dim_name].append(score)
        model_reasons[mname][dim_name].append(reason)
        model_evidences[mname][dim_name].append(list(evidence or []))
        model_run_indices[mname][dim_name].append(run_idx)
        model_costs[mname].append(cost)

    metadata_by_task: dict[tuple[str, int, str], dict] = {}
    for idx, key in enumerate(task_keys):
        item = results[idx]
        if isinstance(item, dict):
            metadata_by_task[key] = item.get("judge_metadata", {})

    # ── Per-run raw scores ──
    _per_run_scores: dict[str, dict[str, dict[str, dict]]] = {}
    for dim_name in active_dims:
        _per_run_scores[dim_name] = {}
        ms = _dim_max_score.get(dim_name, 10)
        for mname in model_names:
            _per_run_scores[dim_name][mname] = {}
            run_scores = model_accumulated[mname][dim_name]
            run_indices = model_run_indices[mname][dim_name]
            for pos, run_score in enumerate(run_scores):
                original_run_idx = (
                    run_indices[pos] if pos < len(run_indices) else pos
                )
                raw_int = (
                    int(round(run_score * (ms - 1) + 1))
                    if ms > 1
                    else int(round(run_score))
                )
                _per_run_scores[dim_name][mname][f"run_{original_run_idx}"] = {
                    "score": round(run_score, 4),
                    "raw_int": max(1, min(ms, raw_int)),
                    "reason": (
                        model_reasons[mname][dim_name][pos]
                        if pos < len(model_reasons[mname][dim_name])
                        else ""
                    ),
                    "evidence": (
                        model_evidences[mname][dim_name][pos]
                        if pos < len(model_evidences[mname][dim_name])
                        else []
                    ),
                    "judge_metadata": metadata_by_task.get(
                        (mname, original_run_idx, dim_name),
                        {},
                    ),
                }

    # ── Per-model dimension scores ──
    per_model: dict[str, dict[str, dict]] = {}
    for mname in model_names:
        per_model[mname] = {}
        for dim_name in active_dims:
            s = model_accumulated[mname][dim_name]
            if s:
                per_model[mname][dim_name] = {
                    "score": round(sum(s) / len(s), 4),
                    "reason": (
                        model_reasons[mname][dim_name][0]
                        if model_reasons[mname][dim_name]
                        else ""
                    ),
                    "evidence": (
                        model_evidences[mname][dim_name][0]
                        if model_evidences[mname][dim_name]
                        else []
                    ),
                    "judge_metadata": (
                        metadata_by_task.get(
                            (mname, model_run_indices[mname][dim_name][0], dim_name),
                            {},
                        )
                        if model_run_indices[mname][dim_name]
                        else {}
                    ),
                }

    # ── Final dimension scores = cross-model average ──
    final_scores: dict = {}
    for dim_name in active_dims:
        model_avgs = [
            per_model[mname][dim_name]["score"]
            for mname in model_names
            if dim_name in per_model[mname]
        ]
        if not model_avgs:
            continue
        final_scores[dim_name] = round(sum(model_avgs) / len(model_avgs), 4)

    # ── Cost tracking ──
    total_cost = sum(c for costs in model_costs.values() for c in costs)
    final_scores["_eval_cost"] = round(total_cost, 6)
    final_scores["_eval_cost_by_model"] = {
        m: round(sum(costs), 6) for m, costs in model_costs.items()
    }
    final_scores["_weights_used"] = {
        dim[:2]: (1 if dim in active_dims else 0) for dim in DIMENSIONS
    }
    final_scores["_per_model"] = per_model
    final_scores["_per_run_scores"] = _per_run_scores

    # ── Per-dimension reasons ──
    dim_reasons: dict[str, str] = {}
    dim_evidence: dict[str, list[str]] = {}
    for dim_name in active_dims:
        all_reasons_for_dim: list[str] = []
        all_evidence_for_dim: list[list[str]] = []
        all_scores_for_dim: list[float] = []
        for mname in model_names:
            all_reasons_for_dim.extend(model_reasons[mname][dim_name])
            all_evidence_for_dim.extend(model_evidences[mname][dim_name])
            all_scores_for_dim.extend(model_accumulated[mname][dim_name])
        if dim_name not in final_scores:
            continue
        target = final_scores[dim_name]
        best_idx = 0
        best_dist = float("inf")
        for idx, s in enumerate(all_scores_for_dim):
            dist = abs(s - target)
            if dist < best_dist:
                best_dist = dist
                best_idx = idx
        if best_idx < len(all_reasons_for_dim) and all_reasons_for_dim[best_idx]:
            dim_reasons[dim_name] = all_reasons_for_dim[best_idx]
        if best_idx < len(all_evidence_for_dim):
            dim_evidence[dim_name] = all_evidence_for_dim[best_idx]
    if dim_reasons:
        final_scores["_dim_reasons"] = dim_reasons
    if dim_evidence:
        final_scores["_dim_evidence"] = dim_evidence
    clean_errors = {dim: errs for dim, errs in dim_errors.items() if errs}
    if clean_errors:
        final_scores["_dim_errors"] = clean_errors
    final_scores["_blocking_missing"] = [
        {
            "track": "tutor",
            "dimension": dim,
            "reason": "; ".join(str(err) for err in errs),
        }
        for dim, errs in clean_errors.items()
    ]

    return final_scores


# ──────────────────────────────────────────────────────────────
# Aggregate score
# ──────────────────────────────────────────────────────────────


def compute_tutor_score(
    dimension_scores: dict[str, float],
    category: Optional[str] = None,
    requires_code: bool = False,
) -> Optional[float]:
    if not dimension_scores:
        return None
    weighted_sum = 0.0
    weight_total = 0.0
    for dim_name, score in dimension_scores.items():
        if dim_name.startswith("_") or not isinstance(score, (int, float)):
            continue
        w = get_dimension_weight(category, dim_name, requires_code=requires_code)
        weighted_sum += w * score
        weight_total += w
    if weight_total == 0.0:
        return None
    return round(weighted_sum / weight_total, 4)
