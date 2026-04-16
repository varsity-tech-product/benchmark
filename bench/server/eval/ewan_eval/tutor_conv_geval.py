"""7D Persona-aware tutoring rubric evaluation — single-call, checklist-based.

Each dimension is scored in one LLM call against a structured rubric
containing checklist conditions and a bottom-up evaluation process.
No Phase 1 "evaluation steps" generation — the rubric IS the evaluation.

Production logic:
    - 7 dimensions (D1-D7) with per-category weights
    - Single judge run per model per dimension (temp=0, deterministic)
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
import re as _re_mod
import threading
import time as _time
from pathlib import Path
from typing import Optional

from server.eval.ewan_eval.async_utils import run_async
from server.eval.ewan_eval.conv_geval import (
    ConversationalTestCase,
    EwanConvGEval,
    Turn,
)
from server.eval.ewan_eval.llm_client import extract_json_from_response
from server.eval.ewan_eval.model_resolver import resolve_ewan_model

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

NUM_JUDGE_RUNS = 1  # Each dim is an independent LLM call at temp=0; shuffling order is a no-op (ICC experiment: CV=0.0% on stable tasks)

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
    """Build the rubric block injected into the scoring prompt.

    Contains: dimension label + scoring guidance with labels.
    No criteria field, no persona context, no evaluation process
    (process and rules are in _SCORE_PROMPT).
    """
    dim_data = rubric["dimensions"].get(dimension_name)
    if not dim_data:
        raise ValueError(
            f"Dimension '{dimension_name}' not found in rubric for "
            f"persona_level='{rubric.get('persona_level', 'unknown')}'"
        )

    # Score labels for clearer semantic anchoring
    _SCORE_LABELS = {
        "1": "Failure",
        "2": "Below Expectations",
        "3": "Adequate (Baseline)",
        "4": "Good",
        "5": "Excellent",
    }

    scoring_lines = []
    for score, description in sorted(
        dim_data["scoring_guidance"].items(), key=lambda x: int(x[0])
    ):
        label = _SCORE_LABELS.get(score, "")
        prefix = f"Score {score} — {label}" if label else f"Score {score}"
        scoring_lines.append(f"{prefix}: {description}")

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

    score_keys = sorted(dim_data["scoring_guidance"].keys(), key=int)
    max_score = int(score_keys[-1])

    return (
        f"## Dimension: {dim_label} (1-{max_score} scale)\n\n"
        f"{chr(10).join(scoring_lines)}"
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
    rubric = load_rubric(persona_level)
    dims = dimension_order or DIMENSIONS
    metrics = []
    for dim_name in dims:
        criteria = _build_criteria_from_rubric(dim_name, rubric)
        model_obj = resolve_ewan_model(model)
        # Detect scale from rubric keys (5-point or 10-point)
        dim_data = rubric["dimensions"].get(dim_name, {})
        score_keys = dim_data.get("scoring_guidance", {}).keys()
        max_score = max((int(k) for k in score_keys), default=10)
        metrics.append(
            EwanConvGEval(
                name=dim_name,
                criteria=criteria,
                threshold=0.5,
                model=model_obj,
                max_score=max_score,
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
    """Reconstruct scoring prompt and call via a_generate with robust parser."""
    from server.eval.ewan_eval.conv_geval import _SCORE_PROMPT, _format_turns

    turns_text = _format_turns(tc.turns)
    prompt = _SCORE_PROMPT.format(
        rubric=metric.criteria,
        turns=turns_text,
        max_score=metric.max_score,
    )

    model_obj = resolve_ewan_model(model_name)
    response_text, cost = await model_obj.a_generate(prompt)
    parsed = extract_json_from_response(response_text)
    score = parsed.get("score", metric.max_score // 2)
    reason = parsed.get("reason", "Fallback evaluation")
    return float(score), reason


# ──────────────────────────────────────────────────────────────
# Conversation preprocessing
# ──────────────────────────────────────────────────────────────

ENABLE_CONVERSATION_PREPROCESSING = True

# Full enrichment: tool names + truncated args + truncated results
_ENRICHED_DIMS_FULL = {"D4_domain_accuracy", "D5_code_teaching", "D7_safety_boundaries"}
# Lightweight enrichment: tool names + status only (no content)
_ENRICHED_DIMS_LIGHTWEIGHT = {"D3_scaffolding_calibration"}
# Union for source selection
_ENRICHED_DIMS = _ENRICHED_DIMS_FULL | _ENRICHED_DIMS_LIGHTWEIGHT

_CODE_FENCE_RE = _re_mod.compile(
    r"^[ \t]*```[^\n]*\n(.*?)^[ \t]*```[ \t]*$",
    _re_mod.MULTILINE | _re_mod.DOTALL,
)


def _strip_code_blocks(content: str) -> str:
    def _replacement(match):
        code = match.group(1)
        line_count = code.count("\n") + 1
        return f"[code block: {line_count} lines]"

    return _CODE_FENCE_RE.sub(_replacement, content)


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
    enriched_conversation_turns_lightweight: Optional[list[dict]] = None,
    dimension_order: Optional[list[str]] = None,
) -> dict[str, float]:
    """Evaluate tutoring dimensions.

    Each dimension is an independent LLM call at temp=0, so dimension
    ordering has no effect on scores (validated by ICC experiment).

    Args:
        dimension_order: If provided, evaluate only these dimensions
            (e.g. ["D3_scaffolding_calibration", "D4_domain_accuracy"]).
            If None, evaluates all dimensions with non-zero weight.

    Single-call evaluation: the rubric (with checklist conditions and
    evaluation process) is injected directly into the scoring prompt.
    No Phase 1 "evaluation steps" generation.

    Full production logic:
    - Single-call rubric evaluation (no Phase 1)
    - Multi-model parallel execution
    - Two-tier conversation (original vs enriched)
    - Abort + fallback layers
    """
    from server.config.llm_config import EVAL_DEFAULT_MODELS

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

    # ── Filter dimensions ──
    if dimension_order:
        # Explicit dimension subset requested
        active_dims = [d for d in dimension_order if d in DIMENSIONS]
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

    # ── Build per-dimension test cases ──
    _tc_kwargs = dict(
        scenario=scenario,
        expected_outcome=expected_outcome,
        user_description=user_description,
    )
    _enriched_full = enriched_conversation_turns or conversation_turns
    _enriched_light = enriched_conversation_turns_lightweight or conversation_turns

    _dim_test_cases: dict[str, ConversationalTestCase] = {}

    if conversational_test_case is not None:
        for dim in active_dims:
            _dim_test_cases[dim] = conversational_test_case
    else:
        _pp_log_lines: list[str] = []
        for dim in active_dims:
            if dim in _ENRICHED_DIMS_FULL:
                src = _enriched_full
            elif dim in _ENRICHED_DIMS_LIGHTWEIGHT:
                src = _enriched_light
            else:
                src = conversation_turns
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

    # ── Build ALL metric instances (models × runs × dims) ──
    all_metrics = []
    all_test_cases = []
    task_keys: list[tuple[str, int, str]] = []

    for model_idx, current_model in enumerate(eval_models):
        mname = model_names[model_idx]
        for run_idx in range(num_judge_runs):
            metrics = create_tutor_geval_metrics(
                persona_level,
                model=current_model,
                dimension_order=active_dims,
            )
            for metric in metrics:
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
                                metric.score = score / float(metric.max_score)
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
                                return score / float(metric.max_score)
                        # Layer 2: direct call with robust parser
                        try:
                            score, reason = await _fallback_direct_eval(
                                metric, tc, model_name
                            )
                            metric.score = score / float(metric.max_score)
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
                            return score / float(metric.max_score)
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
            raw_score = raw_score / float(all_metrics[i].max_score)
        score = max(0.0, min(1.0, raw_score))
        model_accumulated[mname][dim_name].append(score)
        model_reasons[mname][dim_name].append(reason)
        model_costs[mname].append(cost)

    # ── Per-run raw scores ──
    # Collect max_score per dimension from metrics
    _dim_max_score: dict[str, int] = {}
    for metric, (_, _, dim_name) in zip(all_metrics, task_keys):
        if dim_name not in _dim_max_score:
            _dim_max_score[dim_name] = metric.max_score

    _per_run_scores: dict[str, dict[str, dict[str, dict]]] = {}
    for dim_name in active_dims:
        _per_run_scores[dim_name] = {}
        ms = _dim_max_score.get(dim_name, 10)
        for mname in model_names:
            _per_run_scores[dim_name][mname] = {}
            run_scores = model_accumulated[mname][dim_name]
            for ri in range(len(run_scores)):
                _per_run_scores[dim_name][mname][f"run_{ri}"] = {
                    "score": round(run_scores[ri], 4),
                    "raw_int": int(round(run_scores[ri] * ms)),
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
