"""7D Persona-aware tutoring rubric evaluation — DeepEval-free.

Standalone replacement for evaluation/deepeval_metrics/tutor_conv_geval.py.
All DeepEval imports replaced with evaluation.ewan_eval.conv_geval.

Preserves the full production logic:
    - 7 dimensions (D1-D7) with per-category weights
    - Phase 1 caching: evaluation_steps computed once per (model, dim)
    - 3x shuffled judge runs per model, scores averaged
    - Multi-model parallel evaluation
    - Two-tier conversation input (original vs enriched)
    - Per-dimension preprocessing (strip code blocks)
    - Abort signaling + fallback layers
    - Per-run / per-model score transparency
"""

import asyncio
import copy
import json
import logging
import random
import re as _re_mod
import threading
import time as _time
from pathlib import Path
from typing import Optional

from evaluation.ewan_eval.async_utils import run_async
from evaluation.ewan_eval.conv_geval import (
    ConversationalTestCase,
    EwanConvGEval,
    Turn,
)
from evaluation.ewan_eval.llm_client import extract_json_from_response
from evaluation.ewan_eval.model_resolver import resolve_ewan_model

_log = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
# Concurrency control
# ──────────────────────────────────────────────────────────────
_CONCURRENCY = 20


def set_eval_concurrency(n: int) -> None:
    global _CONCURRENCY
    _CONCURRENCY = max(3, n)


_ABORT_SENTINEL = object()


# ──────────────────────────────────────────────────────────────
# 7 Dimensions
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

NUM_JUDGE_RUNS = 3

# ──────────────────────────────────────────────────────────────
# Per-category dimension weights
# ──────────────────────────────────────────────────────────────

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
    if not category:
        return 1.0
    weights = CATEGORY_DIMENSION_WEIGHTS.get(category, {})
    dim_key = dimension_name[:2]
    w = weights.get(dim_key, 1.0)
    if w == 0.0 and dim_key == "D5" and category == "adversarial" and requires_code:
        return 1.0
    return w


# ──────────────────────────────────────────────────────────────
# Rubric loading
# ──────────────────────────────────────────────────────────────

_RUBRIC_DIR = Path(__file__).parent.parent / "rubrics"
_rubric_cache: dict[str, dict] = {}


def load_rubric(persona_level: str) -> dict:
    if persona_level in _rubric_cache:
        return _rubric_cache[persona_level]
    rubric_path = _RUBRIC_DIR / f"rubric_{persona_level}.json"
    if not rubric_path.exists():
        raise FileNotFoundError(f"Rubric file not found: {rubric_path}")
    with open(rubric_path) as f:
        rubric = json.load(f)
    _rubric_cache[persona_level] = rubric
    return rubric


def _build_criteria_from_rubric(dimension_name: str, rubric: dict) -> str:
    dim_data = rubric["dimensions"].get(dimension_name)
    if not dim_data:
        raise ValueError(
            f"Dimension '{dimension_name}' not found in rubric for "
            f"persona_level='{rubric.get('persona_level', 'unknown')}'"
        )
    scoring_lines = []
    for score, description in sorted(
        dim_data["scoring_guidance"].items(), key=lambda x: int(x[0])
    ):
        scoring_lines.append(f"Score {score}: {description}")

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


def _build_full_criteria(dimension_name: str, persona_level: str) -> str:
    rubric = load_rubric(persona_level)
    return _build_criteria_from_rubric(dimension_name, rubric)


# ──────────────────────────────────────────────────────────────
# Metric construction
# ──────────────────────────────────────────────────────────────


def create_tutor_geval_metrics(
    persona_level: str,
    model=None,
    dimension_order: Optional[list[str]] = None,
) -> list[EwanConvGEval]:
    dims = dimension_order or DIMENSIONS
    metrics = []
    for dim_name in dims:
        criteria = _build_full_criteria(dim_name, persona_level)
        model_obj = resolve_ewan_model(model)
        metrics.append(
            EwanConvGEval(
                name=dim_name,
                criteria=criteria,
                threshold=0.5,
                model=model_obj,
            )
        )
    return metrics


# ──────────────────────────────────────────────────────────────
# Fallback helpers
# ──────────────────────────────────────────────────────────────

import re as _re


def _extract_score_from_prose(raw_text: str):
    if not raw_text:
        return None
    text = raw_text.strip()
    score_m = _re.search(r'"score"\s*:\s*(\d+(?:\.\d+)?)', text)
    reason_m = _re.search(r'"reason"\s*:\s*"([^"]{10,})', text)
    if score_m:
        score = float(score_m.group(1))
        reason = reason_m.group(1) if reason_m else "Extracted from malformed JSON"
        if 0 <= score <= 10:
            return score, reason
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


async def _fallback_direct_eval(metric, tc, model_name: str):
    """Reconstruct Phase 2 prompt and call via a_generate with robust parser."""
    if hasattr(metric, "evaluation_steps") and metric.evaluation_steps:
        steps_str = metric.number_evaluation_steps()
    else:
        steps_str = f"1. {metric.criteria[:500]}"

    from evaluation.ewan_eval.conv_geval import _SCORE_PROMPT, _format_turns

    turns_text = _format_turns(tc.turns)
    prompt = _SCORE_PROMPT.format(
        evaluation_steps=steps_str,
        turns=turns_text,
    )

    model_obj = resolve_ewan_model(model_name)
    response_text, cost = await model_obj.a_generate(prompt)
    parsed = extract_json_from_response(response_text)
    score = parsed.get("score", 5)
    reason = parsed.get("reason", "Fallback evaluation")
    return float(score), reason


# ──────────────────────────────────────────────────────────────
# Conversation preprocessing
# ──────────────────────────────────────────────────────────────

ENABLE_CONVERSATION_PREPROCESSING = True

_ENRICHED_DIMS = {"D4_domain_accuracy", "D5_code_teaching", "D7_safety_boundaries"}

_CODE_FENCE_RE = _re_mod.compile(
    r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$",
    _re_mod.MULTILINE | _re_mod.DOTALL,
)


def _strip_code_blocks(content: str) -> str:
    return _CODE_FENCE_RE.sub("[code snippet]", content)


_DIMENSION_PREPROCESS: dict[str, str] = {
    "D1_level_detection": "strip_code",
    "D2_language_adaptation": "strip_code",
    "D3_scaffolding_calibration": "strip_code",
    "D4_domain_accuracy": "none",
    "D5_code_teaching": "none",
    "D6_empathetic_response": "strip_code",
    "D7_safety_boundaries": "strip_code",
}


def preprocess_turns(turns: list[dict], dimension_name: str) -> list[dict]:
    if not ENABLE_CONVERSATION_PREPROCESSING:
        return turns
    mode = _DIMENSION_PREPROCESS.get(dimension_name, "none")
    if mode == "none":
        return turns
    fn = _strip_code_blocks
    processed: list[dict] = []
    for t in turns:
        if t["role"] != "assistant":
            processed.append(t)
        else:
            processed.append({**t, "content": fn(t["content"])})
    return processed


# ──────────────────────────────────────────────────────────────
# Main evaluation entry point
# ──────────────────────────────────────────────────────────────


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

    Full production logic:
    - Phase 1 caching per (model, dimension)
    - 3x shuffled runs per model
    - Multi-model parallel execution
    - Two-tier conversation (original vs enriched)
    - Abort + fallback layers
    """
    from config.llm_config import EVAL_DEFAULT_MODELS

    # ── Resolve model list ──
    multi_model = False
    if isinstance(model, list) and len(model) > 0:
        eval_models = model
        multi_model = True
    elif model is None:
        eval_models = list(EVAL_DEFAULT_MODELS)
        multi_model = len(eval_models) > 1
    else:
        eval_models = [model]

    model_names = [m or "default" for m in eval_models]

    # ── Filter dimensions by category weight ──
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

    # ── Build per-dimension test cases ──
    _tc_kwargs = dict(
        scenario=scenario,
        expected_outcome=expected_outcome,
        user_description=user_description,
    )
    _enriched_src = enriched_conversation_turns or conversation_turns

    _dim_test_cases: dict[str, ConversationalTestCase] = {}

    if conversational_test_case is not None:
        for dim in active_dims:
            _dim_test_cases[dim] = conversational_test_case
    else:
        _pp_log_lines: list[str] = []
        for dim in active_dims:
            src = _enriched_src if dim in _ENRICHED_DIMS else conversation_turns
            processed = preprocess_turns(src, dim)
            tc_turns = [Turn(role=t["role"], content=t["content"]) for t in processed]
            _dim_test_cases[dim] = ConversationalTestCase(
                turns=tc_turns,
                **_tc_kwargs,
            )
            src_chars = sum(len(t["content"]) for t in src)
            out_chars = sum(len(t["content"]) for t in processed)
            mode = _DIMENSION_PREPROCESS.get(dim, "none")
            if ENABLE_CONVERSATION_PREPROCESSING and mode != "none":
                _pp_log_lines.append(
                    f"      {dim[:2]}: {src_chars:,} → {out_chars:,} chars "
                    f"(-{(1 - out_chars / max(src_chars, 1)) * 100:.0f}%, {mode})"
                )
        if _pp_log_lines:
            print("    Conversation preprocessing:")
            for ln in _pp_log_lines:
                print(ln)

    # ── Phase 1: cache evaluation_steps per (model, dim) ──
    _phase1_cache: dict[tuple[str, str], list[str]] = {}
    _p1_t0 = _time.time()

    async def _precompute_phase1():
        sem = asyncio.Semaphore(_CONCURRENCY)
        p1_tasks = []
        p1_keys = []
        for mi, cur_model in enumerate(eval_models):
            mn = model_names[mi]
            for dim_name in active_dims:
                tmp = EwanConvGEval(
                    name=dim_name,
                    criteria=_build_full_criteria(dim_name, persona_level),
                    threshold=0.5,
                    model=resolve_ewan_model(cur_model),
                )

                async def _gen(m=tmp):
                    async with sem:
                        return await m._a_generate_evaluation_steps()

                p1_tasks.append(_gen())
                p1_keys.append((mn, dim_name))
        results = await asyncio.gather(*p1_tasks)
        for key, steps in zip(p1_keys, results):
            _phase1_cache[key] = steps

    run_async(_precompute_phase1())
    _p1_saved = len(eval_models) * len(active_dims) * (num_judge_runs - 1)
    print(
        f"    Phase 1 cached: {len(_phase1_cache)} combinations in "
        f"{_time.time() - _p1_t0:.1f}s (saving {_p1_saved} LLM calls)"
    )

    # ── Build ALL metric instances (models × runs × dims) ──
    all_metrics = []
    all_test_cases = []
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
                cache_key = (mname, metric.name)
                if cache_key in _phase1_cache:
                    metric.evaluation_steps = _phase1_cache[cache_key]
                all_metrics.append(metric)
                all_test_cases.append(copy.deepcopy(_dim_test_cases[metric.name]))
                task_keys.append((mname, run_idx, metric.name))

    # ── Run ALL judge calls in parallel ──
    print(
        f"    Running {len(all_metrics)} judge calls in parallel "
        f"(concurrency={_CONCURRENCY})..."
    )
    t0 = _time.time()

    _abort = abort_event if abort_event is not None else threading.Event()
    _first_error: list[Exception] = []
    _fallback_count = [0]
    _fallback_details: list[dict] = []

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
                        if hasattr(metric, "_raw_failed_response"):
                            raw_text = metric._raw_failed_response
                            del metric._raw_failed_response
                        if attempt < _MAX_RETRIES and "invalid JSON" in str(e):
                            await asyncio.sleep(1)
                            continue
                        _dim = getattr(metric, "name", "?")
                        _key = f"[{model_name}] {_dim}"
                        _log.warning(
                            "Tutor fallback triggered for %s — exc: %s",
                            _key,
                            last_exc,
                        )
                        # Layer 1: extract score from raw prose
                        if raw_text:
                            extracted = _extract_score_from_prose(raw_text)
                            if extracted:
                                score, reason = extracted
                                metric.score = score / 10.0
                                metric.reason = f"[FALLBACK-EXTRACT] {reason}"
                                metric.evaluation_cost = 0.0
                                _fallback_count[0] += 1
                                _fallback_details.append(
                                    {
                                        "dim": _dim,
                                        "model": model_name,
                                        "layer": "EXTRACT",
                                        "score": score,
                                    }
                                )
                                return score / 10.0
                        # Layer 2: direct call with robust parser
                        try:
                            score, reason = await _fallback_direct_eval(
                                metric, tc, model_name
                            )
                            metric.score = score / 10.0
                            metric.reason = f"[FALLBACK-DIRECT] {reason}"
                            metric.evaluation_cost = 0.0
                            _fallback_count[0] += 1
                            _fallback_details.append(
                                {
                                    "dim": _dim,
                                    "model": model_name,
                                    "layer": "DIRECT",
                                    "score": score,
                                }
                            )
                            return score / 10.0
                        except Exception:
                            pass
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

    results = run_async(_run_all())
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

    if _first_error:
        raise _first_error[0]

    # ── Accumulate scores by (model, dimension) ──
    model_accumulated: dict[str, dict[str, list[float]]] = {
        name: {d: [] for d in active_dims} for name in model_names
    }
    model_reasons: dict[str, dict[str, list[str]]] = {
        name: {d: [] for d in active_dims} for name in model_names
    }
    model_costs: dict[str, list[float]] = {name: [] for name in model_names}

    for i, (mname, run_idx, dim_name) in enumerate(task_keys):
        if results[i] is _ABORT_SENTINEL or isinstance(results[i], Exception):
            raise RuntimeError(
                f"Unexpected abort/error in Tutor [{mname}] {dim_name} run {run_idx + 1}"
            )
        raw_score = all_metrics[i].score
        cost = all_metrics[i].evaluation_cost or 0.0
        reason = getattr(all_metrics[i], "reason", None) or ""
        if raw_score > 1.0:
            raw_score = raw_score / 10.0
        score = max(0.0, min(1.0, raw_score))
        model_accumulated[mname][dim_name].append(score)
        model_reasons[mname][dim_name].append(reason)
        model_costs[mname].append(cost)

    # ── Per-run raw scores ──
    _per_run_scores: dict[str, dict[str, dict[str, dict]]] = {}
    for dim_name in active_dims:
        _per_run_scores[dim_name] = {}
        for mname in model_names:
            _per_run_scores[dim_name][mname] = {}
            run_scores = model_accumulated[mname][dim_name]
            for ri in range(len(run_scores)):
                _per_run_scores[dim_name][mname][f"run_{ri}"] = {
                    "score": round(run_scores[ri], 4),
                    "raw_int": int(round(run_scores[ri] * 10)),
                }

    # ── Per-model dimension scores ──
    per_model: dict[str, dict[str, float]] = {}
    for mname in model_names:
        per_model[mname] = {}
        for dim_name in active_dims:
            s = model_accumulated[mname][dim_name]
            per_model[mname][dim_name] = round(sum(s) / len(s), 4) if s else 0.5

    # ── Final dimension scores = cross-model average ──
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
    if _fallback_details:
        final_scores["_fallback_details"] = _fallback_details
    final_scores["_per_run_scores"] = _per_run_scores

    # ── Per-dimension reasons ──
    dim_reasons: dict[str, str] = {}
    for dim_name in active_dims:
        all_reasons_for_dim: list[str] = []
        all_scores_for_dim: list[float] = []
        for mname in model_names:
            all_reasons_for_dim.extend(model_reasons[mname][dim_name])
            all_scores_for_dim.extend(model_accumulated[mname][dim_name])
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
    if dim_reasons:
        final_scores["_dim_reasons"] = dim_reasons

    return final_scores


# ──────────────────────────────────────────────────────────────
# Aggregate score
# ──────────────────────────────────────────────────────────────


def compute_tutor_score(
    dimension_scores: dict[str, float],
    category: Optional[str] = None,
    requires_code: bool = False,
) -> float:
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
