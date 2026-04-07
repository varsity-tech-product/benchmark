"""Process-level DeepEval metrics for QuantTutorBench.

Design doc §6.1 (Quant Process Scoring) and §9 (DeepEval Component Mapping).

Metrics implemented:
- Tool Usage: mathematical scoring of tool selection quality (tool_usage.py)
- Step Efficiency: 3-sub-dimension evaluation via direct GPTModel call
  (Action Economy, Redundancy Avoidance, Logical Sequencing)
- Process Reasonableness: tool-agnostic execution quality (process_reasonableness.py)
- Process Alignment: reference-anchored process comparison (process_reasonableness.py)
- Code Process: code development process quality (code_process.py)
- Role Adherence: custom GPTModel direct-call (custom_conv_metrics.py)
- Topic Adherence: custom GPTModel direct-call (custom_conv_metrics.py)

QP aggregate = weighted average of 7 dimensions:
    tool_usage              0.20
    process_reasonableness  0.20
    step_efficiency         0.15
    code_process            0.15
    process_alignment       0.10
    role_adherence          0.10
    topic_adherence         0.10

Reference: https://github.com/confident-ai/deepeval
"""

import asyncio
import json as _json
import threading
from typing import Optional

from config.model_resolver import resolve_deepeval_model

from evaluation.deepeval_metrics._scoring_utils import extract_json_from_response

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
    from deepeval.models.llms.openai_model import GPTModel

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


# Default relevant topics for TopicAdherenceMetric
QUANT_TUTOR_TOPICS = [
    "quantitative finance",
    "algorithmic trading",
    "backtesting",
    "technical indicators",
    "moving averages",
    "risk metrics",
    "Sharpe ratio",
    "portfolio analysis",
    "financial data analysis",
    "Python programming for finance",
    "pandas data manipulation",
    "statistical analysis",
    "strategy development",
    "market data",
    "time series analysis",
    "options and derivatives pricing",
    "volatility modeling",
    "risk management and VaR",
    "factor models and alpha generation",
    "return calculation and attribution",
    "correlation and covariance analysis",
    "machine learning in finance",
    "order execution and transaction costs",
]


# ──────────────────────────────────────────────────────────────
# QP Dimension Weights (7 dimensions)
# ──────────────────────────────────────────────────────────────
# Dimensions with score=None or skipped=True are excluded and
# remaining weights are renormalized to sum to 1.0.

_QP_DIMENSION_WEIGHTS = {
    "tool_usage": 0.20,
    "process_reasonableness": 0.20,
    "step_efficiency": 0.15,
    "code_process": 0.15,
    "process_alignment": 0.10,
    "role_adherence": 0.10,
    "topic_adherence": 0.10,
}


# ──────────────────────────────────────────────────────────────
# Single-turn metrics
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# (Sync wrappers removed — runtime uses async versions exclusively
#  via _build_process_tasks_for_model.)
# ──────────────────────────────────────────────────────────────


# ──────────────────────────────────────────────────────────────
# Async helpers for parallel metric evaluation
# ──────────────────────────────────────────────────────────────


async def _return_hard_zero(metric_name: str, reason: str) -> dict:
    """Return a hard-zero result for a metric that requires missing data."""
    return {
        "score": 0.0,
        "reason": f"{metric_name}: {reason} (hard zero)",
        "passed": False,
    }


from evaluation.deepeval_metrics.async_utils import run_async as _run_async

# ──────────────────────────────────────────────────────────────
# Step Efficiency: 3-sub-dimension evaluation
# ──────────────────────────────────────────────────────────────
#
# Sub-dimensions (Phase 2):
#   Action Economy    (0.4) — step count ratio vs reference (programmatic);
#                            hard zero (0.0) when no reference available
#   Redundancy Avoid. (0.3) — detect wasted/repeated calls (LLM-judged)
#   Logical Sequencing(0.3) — evaluate action order (LLM-judged)

# Tools excluded from substantive step count — benign metadata reads
# and text responses that don't represent analytical work.
_NON_SUBSTANTIVE_TOOLS = frozenset({"get_environment_info"})


def _count_substantive_steps(proxy_logs: list) -> int:
    """Count substantive tool calls, excluding benign metadata reads."""
    return sum(1 for log in proxy_logs if log.name not in _NON_SUBSTANTIVE_TOOLS)


def _compute_action_economy(agent_steps: int, reference_steps: int) -> float:
    """Compute Action Economy score from step count ratio.

    Thresholds calibrated to 27% natural path variance (Amplifying.ai study):
        ratio <= 1.3  →  1.0   (within natural variance)
        ratio <= 1.6  →  0.75  (slightly above variance)
        ratio <= 2.2  →  0.5   (noticeably more steps)
        ratio <= 3.0  →  0.25  (significantly more steps)
        ratio >  3.0  →  0.0   (excessively verbose)
    """
    if reference_steps <= 0:
        return 0.5
    ratio = agent_steps / reference_steps
    if ratio <= 1.3:
        return 1.0
    elif ratio <= 1.6:
        return 0.75
    elif ratio <= 2.2:
        return 0.5
    elif ratio <= 3.0:
        return 0.25
    else:
        return 0.0


def _build_trace_summary_for_prompt(proxy_logs: list) -> str:
    """Build a concise trace summary for inclusion in the LLM prompt."""
    lines = []
    for i, log in enumerate(proxy_logs, 1):
        if log.name in _NON_SUBSTANTIVE_TOOLS:
            continue
        args_preview = _json.dumps(log.args, default=str)
        if len(args_preview) > 200:
            args_preview = args_preview[:200] + "..."
        result_preview = log.result[:150] if log.result else "(no output)"
        status = "OK" if log.success else "FAIL"
        lines.append(f"  {i}. {log.name}({args_preview}) → [{status}] {result_preview}")
    return "\n".join(lines) if lines else "  (no tool calls)"


def _build_step_efficiency_prompt(
    task: str,
    agent_trace: str,
    *,
    has_reference: bool,
    ref_step_count: int,
    ref_trace_summary: str,
    agent_steps: int,
    action_economy_precomputed: float | None,
) -> str:
    """Build the step efficiency evaluation prompt.

    When reference is available and Action Economy is pre-computed,
    the LLM only judges Redundancy Avoidance and Logical Sequencing.
    When no reference, the LLM judges all three dimensions.
    """
    header = """You are evaluating the STEP EFFICIENCY of a tool-augmented tutoring agent.

CONTEXT: This agent teaches quantitative finance using tools that fetch market data,
execute code, compute indicators, create charts, and run backtests. Tool calls that
serve teaching purposes (demonstrate with real data, verify code, compute metrics,
create visualizations) are pedagogically valuable.

SCORING SCALE: Use ONLY these values: {0.0, 0.25, 0.5, 0.75, 1.0}.
When in doubt between two levels, select the LOWER score."""

    task_section = f"\nTASK: {task}"

    # Reference section (only when available)
    ref_section = ""
    if has_reference:
        ref_section = f"""
REFERENCE EXECUTION (expert baseline):
- Substantive steps: {ref_step_count}
- Trace:
{ref_trace_summary}"""

    agent_section = f"""
AGENT EXECUTION:
- Substantive steps: {agent_steps}
- Trace:
{agent_trace}"""

    tool_tier_note = """
NOTE ON TOOL TIERS:
The agent had access to convenience tools (compute_indicator, run_backtest, etc.) that
bundle multi-step operations into one call.
- Using convenience tools is efficient and should be recognized positively.
- Building equivalent functionality with shell_exec + file_write is equally valid.
  Judge the step count relative to the approach taken, not against the shortcut."""

    if action_economy_precomputed is not None:
        # Reference available — LLM judges 2 dimensions only
        ratio = agent_steps / ref_step_count if ref_step_count > 0 else 0
        dimensions = f"""
ACTION ECONOMY (pre-computed): {action_economy_precomputed} (ratio: {ratio:.2f})
This score is already calculated. Do NOT re-evaluate it.

Evaluate the following TWO dimensions:

1. REDUNDANCY AVOIDANCE (0.0-1.0):
   Red flags: Same tool called with identical arguments, fetching data never used,
   re-computing values already obtained, calling tools after the answer is known.
   Acceptable: Retrying after an error with different parameters, fetching different
   data for comparison, progressive refinement.
   - 1.0:  No redundant calls
   - 0.75: Minor redundancy (1-2 repeated calls but with some purpose)
   - 0.5:  Some redundancy (repeated calls or unused data fetches)
   - 0.25: Significant redundancy
   - 0.0:  Pervasive waste

2. LOGICAL SEQUENCING (0.0-1.0):
   Evaluate whether actions follow a logical data-dependency order.
   Good: fetch data → compute indicator → analyze → visualize
   Bad: visualize before data exists, compute before dependencies ready,
   backtracking to fix ordering errors.
   - 1.0:  Perfect logical flow
   - 0.75: Minor sequencing issues (one action slightly out of order)
   - 0.5:  Some out-of-order actions
   - 0.25: Significant ordering problems
   - 0.0:  Chaotic/random ordering

Return ONLY a JSON object (no markdown, no extra text):
{{"redundancy_avoidance": <float>, "logical_sequencing": <float>, "reason": "<brief explanation>"}}"""
    else:
        # No reference — LLM judges all 3 dimensions
        dimensions = """
Evaluate the following THREE dimensions:

1. ACTION ECONOMY (0.0-1.0):
   Given the task complexity, did the agent use a reasonable number of steps?
   - 1.0:  Minimal steps, every call essential
   - 0.75: Mostly efficient, 1-2 extra calls
   - 0.5:  Moderate excess steps
   - 0.25: Many unnecessary steps
   - 0.0:  Excessively verbose

2. REDUNDANCY AVOIDANCE (0.0-1.0):
   Red flags: Same tool called with identical arguments, fetching data never used,
   re-computing values already obtained, calling tools after the answer is known.
   Acceptable: Retrying after an error with different parameters, fetching different
   data for comparison, progressive refinement.
   - 1.0:  No redundant calls
   - 0.75: Minor redundancy (1-2 repeated calls but with some purpose)
   - 0.5:  Some redundancy (repeated calls or unused data fetches)
   - 0.25: Significant redundancy
   - 0.0:  Pervasive waste

3. LOGICAL SEQUENCING (0.0-1.0):
   Evaluate whether actions follow a logical data-dependency order.
   Good: fetch data → compute indicator → analyze → visualize
   Bad: visualize before data exists, compute before dependencies ready,
   backtracking to fix ordering errors.
   - 1.0:  Perfect logical flow
   - 0.75: Minor sequencing issues (one action slightly out of order)
   - 0.5:  Some out-of-order actions
   - 0.25: Significant ordering problems
   - 0.0:  Chaotic/random ordering

Return ONLY a JSON object (no markdown, no extra text):
{{"action_economy": <float>, "redundancy_avoidance": <float>, "logical_sequencing": <float>, "reason": "<brief explanation>"}}"""

    return (
        header
        + task_section
        + ref_section
        + agent_section
        + tool_tier_note
        + dimensions
    )


async def _async_eval_step_efficiency(
    input_text,
    actual_output,
    proxy_logs,
    model,
    reference_trace=None,
    threshold=0.5,
):
    """Evaluate step efficiency with 3 sub-dimensions.

    Uses direct LLM call (via GPTModel) instead of DeepEval's
    StepEfficiencyMetric to support structured multi-score output.

    Sub-dimensions:
        Action Economy (0.4): programmatic when reference available
        Redundancy Avoidance (0.3): LLM-judged
        Logical Sequencing (0.3): LLM-judged
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    # Count substantive steps
    agent_steps = _count_substantive_steps(proxy_logs)

    # Reference info
    has_reference = reference_trace is not None
    ref_step_count = reference_trace.get("step_count", 0) if has_reference else 0
    ref_trace_summary = ""
    if has_reference:
        # Use trace_summary from reference (list of step descriptions)
        summary_lines = reference_trace.get("trace_summary", [])
        if isinstance(summary_lines, list):
            ref_trace_summary = "\n".join(
                f"  {i+1}. {s}" for i, s in enumerate(summary_lines)
            )
        else:
            ref_trace_summary = str(summary_lines)

    # Compute Action Economy programmatically when reference available.
    # Hard zero: without reference, Action Economy = 0.0 (not LLM-judged).
    if has_reference and ref_step_count > 0:
        action_economy = _compute_action_economy(agent_steps, ref_step_count)
    else:
        action_economy = 0.0

    # Build prompt
    agent_trace = _build_trace_summary_for_prompt(proxy_logs)
    prompt = _build_step_efficiency_prompt(
        task=input_text,
        agent_trace=agent_trace,
        has_reference=has_reference,
        ref_step_count=ref_step_count,
        ref_trace_summary=ref_trace_summary,
        agent_steps=agent_steps,
        action_economy_precomputed=action_economy,
    )

    # Get LLM response via GPTModel
    try:
        model_obj = resolve_deepeval_model(model)
        if isinstance(model_obj, str):
            from config.pricing import get_deepeval_cost_kwargs

            model_obj = GPTModel(model=model_obj, **get_deepeval_cost_kwargs(model_obj))
        response_text, call_cost = await model_obj.a_generate(prompt)
        result = extract_json_from_response(response_text)
    except Exception:
        raise  # propagate to abort handler

    # Parse sub-scores (clamp to 5-point ordinal)
    def _clamp_ordinal(val, default=0.5):
        """Snap to nearest 5-point ordinal value."""
        try:
            v = float(val)
        except (TypeError, ValueError):
            return default
        ordinals = [0.0, 0.25, 0.5, 0.75, 1.0]
        return min(ordinals, key=lambda x: abs(x - v))

    redundancy = _clamp_ordinal(result.get("redundancy_avoidance", 0.5))
    sequencing = _clamp_ordinal(result.get("logical_sequencing", 0.5))

    # Action Economy is always pre-computed: programmatic from reference,
    # or hard zero (0.0) when no reference is available.
    overall = 0.4 * action_economy + 0.3 * redundancy + 0.3 * sequencing

    return {
        "score": round(overall, 4),
        "reason": result.get("reason", ""),
        "passed": overall >= threshold,
        "sub_scores": {
            "action_economy": action_economy,
            "redundancy_avoidance": redundancy,
            "logical_sequencing": sequencing,
        },
        "agent_substantive_steps": agent_steps,
        "reference_step_count": ref_step_count if has_reference else None,
        "_eval_cost": float(call_cost) if call_cost else 0.0,
    }


async def _async_eval_role_adherence(
    turns: list[dict],
    model,
    threshold=0.5,
):
    """Evaluate role adherence via custom GPTModel direct call.

    Args:
        turns: Conversation as list of {"role": ..., "content": ...} dicts.
    """
    from evaluation.deepeval_metrics.custom_conv_metrics import eval_role_adherence

    return await eval_role_adherence(turns, model, threshold=threshold)


async def _async_eval_topic_adherence(
    turns: list[dict],
    model,
    task_description="",
    threshold=0.5,
):
    """Evaluate topic adherence via custom GPTModel direct call.

    Args:
        turns: Conversation as list of {"role": ..., "content": ...} dicts.
    """
    from evaluation.deepeval_metrics.custom_conv_metrics import eval_topic_adherence

    return await eval_topic_adherence(
        turns,
        model,
        task_description=task_description,
        threshold=threshold,
    )


# ──────────────────────────────────────────────────────────────
# Aggregate evaluation entry point
# ──────────────────────────────────────────────────────────────


def _build_process_tasks_for_model(
    single_model,
    task_description: str,
    actual_output: str,
    proxy_logs: list,
    category: str,
    conversation: list[dict] | None,
    is_adversarial: bool,
    reference_trace: Optional[dict] = None,
    task_requires_code: bool = False,
) -> dict[str, object]:
    """Build async metric coroutines for a single model.

    Args:
        conversation: List of {"role": ..., "content": ...} dicts for
            role/topic adherence. None to skip those metrics.

    Returns:
        Dict mapping metric_name -> coroutine.
    """
    from evaluation.deepeval_metrics.code_process import (
        async_eval_code_process,
    )
    from evaluation.deepeval_metrics.process_reasonableness import (
        async_eval_process_alignment,
        async_eval_process_reasonableness,
    )

    tasks: dict[str, object] = {}

    # Step efficiency (Phase 2) — always evaluated
    tasks["step_efficiency"] = _async_eval_step_efficiency(
        task_description,
        actual_output,
        proxy_logs,
        single_model,
        reference_trace=reference_trace,
    )

    # Process reasonableness (Phase 4) — always evaluated (tool-agnostic)
    # For code tasks, Error Handling is narrowed to non-code errors
    # (code-specific debugging evaluated separately by Code Process).
    _code_categories = ("implementation", "debug", "end_to_end", "data_analysis")
    tasks["process_reasonableness"] = async_eval_process_reasonableness(
        task_description=task_description,
        category=category,
        proxy_logs=proxy_logs,
        model=single_model,
        is_code_task=(category in _code_categories),
    )

    # Process alignment (Phase 4) — skip for pure-refusal adversarial only.
    # Educational adversarial (requires_code=true) may have reference traces.
    # Hard zero: score 0.0 when no reference (not skipped from aggregate).
    if not (is_adversarial and not task_requires_code):
        if reference_trace is not None:
            tasks["process_alignment"] = async_eval_process_alignment(
                task_description=task_description,
                category=category,
                proxy_logs=proxy_logs,
                reference_trace=reference_trace,
                model=single_model,
            )
        else:
            tasks["process_alignment"] = _return_hard_zero(
                "process_alignment", "no reference trace available"
            )

    # Code process (Phase 5) — auto-detects applicability from logs;
    # returns score=None when no code activity, excluded from QP aggregate.
    # Skip for conceptual_qa and pure-refusal adversarial (requires_code=false).
    # Educational adversarial (requires_code=true) is allowed through.
    if category not in ("conceptual_qa",) and not (
        is_adversarial and not task_requires_code
    ):
        tasks["code_process"] = async_eval_code_process(
            task_description=task_description,
            proxy_logs=proxy_logs,
            actual_output=actual_output,
            model=single_model,
        )

    # Conversational metrics — custom GPTModel direct-call
    if conversation:
        tasks["role_adherence"] = _async_eval_role_adherence(
            conversation,
            single_model,
        )
        tasks["topic_adherence"] = _async_eval_topic_adherence(
            conversation,
            single_model,
            task_description=task_description,
        )

    return tasks


def evaluate_all_process_metrics(
    task_description: str,
    actual_output: str,
    proxy_logs: list,
    category: str = "",
    conversation: list[dict] | None = None,
    model=None,
    reference_trace: Optional[dict] = None,
    is_adversarial: bool = False,
    tool_usage_result: Optional[dict] = None,
    task_requires_code: bool = False,
    abort_event: Optional[threading.Event] = None,
) -> dict:
    """Run all process-level metrics in parallel and return consolidated results.

    Args:
        task_description: Text description of the task.
        actual_output: Agent's combined text output.
        proxy_logs: Tool call logs from MCPProxy (list of ToolCallLog objects).
        category: Task category (e.g. "implementation", "data_analysis").
        conversation: List of {"role": ..., "content": ...} dicts for
            role/topic adherence evaluation. None to skip those metrics.
        model: LLM judge model — single string, list of strings, or None.
        reference_trace: Reference execution data (from ReferenceStore) for step
            efficiency and process alignment anchoring.
        is_adversarial: Whether this is an adversarial task (skips alignment).
        tool_usage_result: Pre-computed tool usage score (from tool_usage.py).
        task_requires_code: Whether the task expects code output (allows
            code_process evaluation for educational adversarial tasks).

    Returns:
        Dict with per-metric scores (cross-model average), an aggregate
        process score, and ``_per_model`` breakdown when multi-model.
    """
    import time as _time

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

    # ── Build tasks for ALL models ──
    # flat_tasks: list of (model_name, metric_name, coroutine)
    flat_tasks: list[tuple[str, str, object]] = []
    for model_idx, single_model in enumerate(eval_models):
        mname = model_names[model_idx]
        tasks_for_model = _build_process_tasks_for_model(
            single_model=single_model,
            task_description=task_description,
            actual_output=actual_output,
            proxy_logs=proxy_logs,
            category=category,
            conversation=conversation,
            is_adversarial=is_adversarial,
            reference_trace=reference_trace,
            task_requires_code=task_requires_code,
        )
        for metric_name, coro in tasks_for_model.items():
            flat_tasks.append((mname, metric_name, coro))

    total_calls = len(flat_tasks)
    print(
        f"    Running {total_calls} process metric calls "
        f"({len(eval_models)} model(s) × metrics) in parallel "
        f"(concurrency={_CONCURRENCY})..."
    )
    t0 = _time.time()

    coros = [coro for _, _, coro in flat_tasks]

    # abort_event is a threading.Event shared with the orchestrator.
    # When set (by this evaluator or another parallel thread), queued
    # coroutines are skipped to stop wasting tokens.
    _abort = abort_event if abort_event is not None else threading.Event()
    _first_error: list[Exception] = []  # mutable container for nonlocal capture

    async def _run_all():
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _guarded(c):
            if _abort.is_set():
                return _ABORT_SENTINEL
            async with sem:
                if _abort.is_set():
                    return _ABORT_SENTINEL
                try:
                    return await c
                except Exception as e:
                    _abort.set()
                    if not _first_error:
                        _first_error.append(e)
                    return _ABORT_SENTINEL

        return await asyncio.gather(
            *[_guarded(c) for c in coros], return_exceptions=True
        )

    raw_results = _run_async(_run_all())
    elapsed = _time.time() - t0

    aborted = sum(1 for r in raw_results if r is _ABORT_SENTINEL)
    if aborted:
        print(
            f"    Process metrics: {total_calls - aborted}/{total_calls} completed, "
            f"{aborted} aborted in {elapsed:.1f}s"
        )
    else:
        print(f"    Completed {total_calls} process metric calls in {elapsed:.1f}s")

    # If any coroutine failed, propagate the error to the thread level
    if _first_error:
        raise _first_error[0]

    # ── Collect per-model results ──
    # model_results[model_name][metric_name] = {score, reason, ...}
    model_results: dict[str, dict[str, dict]] = {mname: {} for mname in model_names}

    for i, (mname, metric_name, _) in enumerate(flat_tasks):
        raw = raw_results[i]
        if raw is _ABORT_SENTINEL or isinstance(raw, Exception):
            # Should not reach here — errors are propagated above
            raise RuntimeError(f"Unexpected abort/error in {metric_name}")
        else:
            model_results[mname][metric_name] = raw

    # ── Determine metric names (from first model) ──
    metric_names = list(model_results[model_names[0]].keys())

    # ── Cross-model average per metric ──
    results: dict = {}
    for metric_name in metric_names:
        scores_across_models = []
        for mname in model_names:
            r = model_results[mname].get(metric_name, {})
            s = r.get("score")
            if s is not None:
                scores_across_models.append(s)
        if scores_across_models:
            avg_score = round(sum(scores_across_models) / len(scores_across_models), 4)
        else:
            avg_score = None
        # Use the first model's result as base, override score with average
        base = dict(model_results[model_names[0]].get(metric_name, {}))
        base["score"] = avg_score
        # Average sub_scores across models (if present)
        first_sub = base.get("sub_scores")
        if isinstance(first_sub, dict) and multi_model:
            avg_sub: dict[str, float | None] = {}
            for sub_key in first_sub:
                sub_vals = []
                for mname in model_names:
                    ms = model_results[mname].get(metric_name, {})
                    sv = (ms.get("sub_scores") or {}).get(sub_key)
                    if sv is not None:
                        sub_vals.append(sv)
                avg_sub[sub_key] = (
                    round(sum(sub_vals) / len(sub_vals), 4) if sub_vals else None
                )
            base["sub_scores"] = avg_sub
        results[metric_name] = base

    # ── Log per-metric scores ──
    for metric_name in metric_names:
        r = results.get(metric_name, {})
        score = r.get("score", "?")
        reason = r.get("reason", "")
        reason_lower = reason.lower() if isinstance(reason, str) else ""
        is_fallback = reason_lower.startswith(f"{metric_name} error:") or (
            "not available" in reason_lower and len(reason_lower) < 60
        )
        tag = " [FALLBACK]" if is_fallback else ""
        # Show per-model breakdown inline
        per_model_str = ""
        if multi_model:
            parts = []
            for mname in model_names:
                ms = model_results[mname].get(metric_name, {}).get("score", "?")
                short_name = mname.split("/")[-1] if "/" in mname else mname
                parts.append(f"{short_name}={ms}")
            per_model_str = f"  ({', '.join(parts)})"
        print(f"      {metric_name}: {score}{tag}{per_model_str}")

    # ── Inject pre-computed tool_usage score ──
    if tool_usage_result is not None:
        results["tool_usage"] = tool_usage_result
        tu_score = tool_usage_result.get("score", "?")
        print(f"      tool_usage: {tu_score}")

    # ── Compute weighted aggregate process score ──
    available_dims: dict[str, float] = {}
    for dim, weight in _QP_DIMENSION_WEIGHTS.items():
        v = results.get(dim)
        if (
            isinstance(v, dict)
            and v.get("score") is not None
            and not v.get("skipped", False)
        ):
            available_dims[dim] = v["score"]

    if available_dims:
        total_weight = sum(_QP_DIMENSION_WEIGHTS[d] for d in available_dims)
        aggregate = sum(
            _QP_DIMENSION_WEIGHTS[d] * available_dims[d] / total_weight
            for d in available_dims
        )
    else:
        aggregate = 0.5

    results["aggregate_process_score"] = round(aggregate, 4)
    print(f"      aggregate_process_score: {results['aggregate_process_score']}")

    # ── Per-model aggregate breakdown ──
    if multi_model:
        per_model_agg: dict[str, dict] = {}
        for mname in model_names:
            m_avail: dict[str, float] = {}
            for dim, weight in _QP_DIMENSION_WEIGHTS.items():
                # tool_usage is model-independent — use the same score
                if dim == "tool_usage" and tool_usage_result is not None:
                    tu_s = tool_usage_result.get("score")
                    if tu_s is not None:
                        m_avail[dim] = tu_s
                    continue
                v = model_results[mname].get(dim, {})
                if (
                    isinstance(v, dict)
                    and v.get("score") is not None
                    and not v.get("skipped", False)
                ):
                    m_avail[dim] = v["score"]
            if m_avail:
                m_total_w = sum(_QP_DIMENSION_WEIGHTS[d] for d in m_avail)
                m_agg = sum(
                    _QP_DIMENSION_WEIGHTS[d] * m_avail[d] / m_total_w for d in m_avail
                )
            else:
                m_agg = 0.5
            per_model_agg[mname] = {
                "aggregate_process_score": round(m_agg, 4),
                **{
                    k: v.get("score")
                    for k, v in model_results[mname].items()
                    if isinstance(v, dict) and not k.startswith("_")
                },
                "_sub_scores": {
                    k: v.get("sub_scores")
                    for k, v in model_results[mname].items()
                    if isinstance(v, dict) and isinstance(v.get("sub_scores"), dict)
                },
            }
        results["_per_model"] = per_model_agg
        print("      Per-model aggregate process scores:")
        for mname in model_names:
            agg = per_model_agg[mname]["aggregate_process_score"]
            print(f"        {mname}: {agg}")

    if is_adversarial and not task_requires_code:
        print(
            "      (pure-refusal adversarial: process_alignment + code_process skipped)"
        )

    # ── Aggregate eval cost from all metric results ──
    total_eval_cost = 0.0
    cost_by_model: dict[str, float] = {}
    for mname in model_names:
        m_cost = 0.0
        for metric_name, r in model_results[mname].items():
            if isinstance(r, dict):
                m_cost += r.get("_eval_cost", 0.0)
        cost_by_model[mname] = round(m_cost, 6)
        total_eval_cost += m_cost
    results["_eval_cost"] = total_eval_cost
    results["_eval_cost_by_model"] = cost_by_model

    return results
