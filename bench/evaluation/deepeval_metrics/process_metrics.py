"""Process-level DeepEval metrics for QuantTutorBench.

Design doc §6.1 (Quant Process Scoring) and §9 (DeepEval Component Mapping).

Metrics implemented:
- Step Efficiency: 3-sub-dimension evaluation via direct GPTModel call
  (Action Economy, Redundancy Avoidance, Logical Sequencing)
- Process Reasonableness: tool-agnostic execution quality (process_reasonableness.py)
- Process Alignment: reference-anchored process comparison (process_reasonableness.py)
- RoleAdherenceMetric: stays in "tutor" role? (ConversationalTestCase)
- KnowledgeRetentionMetric: remembers earlier context? (ConversationalTestCase)
- TopicAdherenceMetric: stays on quant finance topics? (ConversationalTestCase)

Reference: https://github.com/confident-ai/deepeval
"""

import asyncio
import json as _json
from typing import Optional

import nest_asyncio
from config.llm_config import resolve_deepeval_model

try:
    from deepeval.metrics import (
        KnowledgeRetentionMetric,
        RoleAdherenceMetric,
        TopicAdherenceMetric,
    )
    from deepeval.models.llms.openai_model import GPTModel
    from deepeval.test_case import ConversationalTestCase

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False

# Patch Knowledge schema: DeepEval's Knowledge model uses extra="forbid" but the
# LLM template asks for flat key-value JSON (e.g. {"name": "Bob"}) rather than
# {"data": {"name": "Bob"}}.  Structured-output models hit a Pydantic validation
# error before the extract_json fallback can wrap the dict.  Relaxing to "allow"
# lets the flat form pass through so the fallback works correctly.
if DEEPEVAL_AVAILABLE:
    try:
        from deepeval.metrics.knowledge_retention.schema import (
            Knowledge as _KnowledgeSchema,
        )
        from pydantic import ConfigDict as _ConfigDict

        _KnowledgeSchema.model_config = _ConfigDict(extra="allow")
        _KnowledgeSchema.model_rebuild(force=True)
    except Exception:
        pass  # non-critical: metric will fall back to 0.5


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
# Single-turn metrics
# ──────────────────────────────────────────────────────────────


def evaluate_step_efficiency(
    input_text: str,
    actual_output: str,
    proxy_logs: list,
    model: Optional[str] = None,
    threshold: float = 0.5,
    reference_trace: Optional[dict] = None,
) -> dict:
    """Evaluate step efficiency of tool usage (synchronous wrapper).

    Delegates to the async 3-sub-dimension implementation.
    """
    return _run_async(
        _async_eval_step_efficiency(
            input_text,
            actual_output,
            proxy_logs,
            model,
            reference_trace=reference_trace,
            threshold=threshold,
        )
    )


# ──────────────────────────────────────────────────────────────
# Multi-turn metrics (ConversationalTestCase based)
# ──────────────────────────────────────────────────────────────


def evaluate_role_adherence(
    conversational_test_case: "ConversationalTestCase",
    chatbot_role: str = "quantitative finance tutor",
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate whether the agent stays in its designated role.

    Design doc §9: Does agent stay in "tutor" role?

    Args:
        conversational_test_case: The ConversationalTestCase.
        chatbot_role: The role the agent should adhere to.
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    # Ensure chatbot_role is set
    if conversational_test_case.chatbot_role is None:
        conversational_test_case.chatbot_role = chatbot_role

    kwargs = {"threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = RoleAdherenceMetric(**kwargs)

    try:
        metric.measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"RoleAdherenceMetric error: {e}",
            "passed": True,
        }


def evaluate_knowledge_retention(
    conversational_test_case: "ConversationalTestCase",
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate whether the agent remembers earlier context.

    Design doc §9: Does agent remember earlier context?

    Args:
        conversational_test_case: The ConversationalTestCase.
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    kwargs = {"threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = KnowledgeRetentionMetric(**kwargs)

    try:
        metric.measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"KnowledgeRetentionMetric error: {e}",
            "passed": True,
        }


def evaluate_topic_adherence(
    conversational_test_case: "ConversationalTestCase",
    relevant_topics: Optional[list[str]] = None,
    model: Optional[str] = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate whether the agent stays on quant finance topics.

    Design doc §9: Does agent stay on quant finance topics?

    Args:
        conversational_test_case: The ConversationalTestCase.
        relevant_topics: List of relevant topic strings (defaults to QUANT_TUTOR_TOPICS).
        model: LLM judge model.
        threshold: Minimum passing score.

    Returns:
        Dict with score, reason, passed.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    topics = relevant_topics or QUANT_TUTOR_TOPICS

    kwargs = {"relevant_topics": topics, "threshold": threshold}
    kwargs["model"] = resolve_deepeval_model(model)

    metric = TopicAdherenceMetric(**kwargs)

    try:
        metric.measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"TopicAdherenceMetric error: {e}",
            "passed": True,
        }


# ──────────────────────────────────────────────────────────────
# Async helpers for parallel metric evaluation
# ──────────────────────────────────────────────────────────────


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


# ──────────────────────────────────────────────────────────────
# Step Efficiency: 3-sub-dimension evaluation
# ──────────────────────────────────────────────────────────────
#
# Sub-dimensions (Phase 2):
#   Action Economy    (0.4) — step count ratio vs reference (programmatic)
#   Redundancy Avoid. (0.3) — detect wasted/repeated calls (LLM-judged)
#   Logical Sequencing(0.3) — evaluate action order (LLM-judged)
#
# When reference is available, Action Economy is computed programmatically
# from agent_steps / reference_steps ratio.  When unavailable, all three
# dimensions are judged by the LLM.

# Tools excluded from substantive step count — benign metadata reads
# and text responses that don't represent analytical work.
_NON_SUBSTANTIVE_TOOLS = frozenset({"send_message", "get_environment_info"})


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


def _extract_json_from_response(text: str) -> dict:
    """Extract JSON object from LLM response, handling markdown fences."""
    text = text.strip()
    # Strip markdown code fences
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last fence lines
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        # Try to find JSON object in the text
        import re

        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                return _json.loads(match.group())
            except _json.JSONDecodeError:
                pass
    return {}


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

    # Compute Action Economy programmatically when reference available
    if has_reference and ref_step_count > 0:
        action_economy = _compute_action_economy(agent_steps, ref_step_count)
    else:
        action_economy = None  # LLM will judge

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
            model_obj = GPTModel(model=model_obj)
        response_text, _ = await model_obj.a_generate(prompt)
        result = _extract_json_from_response(response_text)
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"StepEfficiency LLM error: {e}",
            "passed": True,
        }

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

    if action_economy is not None:
        # Programmatic Action Economy + LLM-judged Redundancy/Sequencing
        overall = 0.4 * action_economy + 0.3 * redundancy + 0.3 * sequencing
    else:
        # All LLM-judged (no reference)
        ae_from_llm = _clamp_ordinal(result.get("action_economy", 0.5))
        overall = 0.4 * ae_from_llm + 0.3 * redundancy + 0.3 * sequencing
        action_economy = ae_from_llm

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
    }


async def _async_eval_role_adherence(
    conversational_test_case,
    model,
    chatbot_role="quantitative finance tutor",
    threshold=0.5,
):
    """Async wrapper for RoleAdherence evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    if conversational_test_case.chatbot_role is None:
        conversational_test_case.chatbot_role = chatbot_role
    metric = RoleAdherenceMetric(
        threshold=threshold, model=resolve_deepeval_model(model)
    )
    try:
        await metric.a_measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"RoleAdherenceMetric error: {e}",
            "passed": True,
        }


async def _async_eval_knowledge_retention(
    conversational_test_case, model, threshold=0.5
):
    """Async wrapper for KnowledgeRetention evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    metric = KnowledgeRetentionMetric(
        threshold=threshold, model=resolve_deepeval_model(model)
    )
    try:
        await metric.a_measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"KnowledgeRetentionMetric error: {e}",
            "passed": True,
        }


async def _async_eval_topic_adherence(
    conversational_test_case, model, relevant_topics=None, threshold=0.5
):
    """Async wrapper for TopicAdherence evaluation."""
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}
    topics = relevant_topics or QUANT_TUTOR_TOPICS
    metric = TopicAdherenceMetric(
        relevant_topics=topics,
        threshold=threshold,
        model=resolve_deepeval_model(model),
    )
    try:
        await metric.a_measure(conversational_test_case)
        return {
            "score": metric.score,
            "reason": getattr(metric, "reason", ""),
            "passed": metric.score >= threshold,
        }
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"TopicAdherenceMetric error: {e}",
            "passed": True,
        }


# ──────────────────────────────────────────────────────────────
# Aggregate evaluation entry point
# ──────────────────────────────────────────────────────────────


def _build_process_tasks_for_model(
    single_model,
    task_description: str,
    actual_output: str,
    proxy_logs: list,
    category: str,
    conversational_test_case,
    is_adversarial: bool,
    reference_trace: Optional[dict] = None,
) -> dict[str, object]:
    """Build async metric coroutines for a single model.

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
    tasks["process_reasonableness"] = async_eval_process_reasonableness(
        task_description=task_description,
        category=category,
        proxy_logs=proxy_logs,
        model=single_model,
    )

    # Process alignment (Phase 4) — requires reference, skip for adversarial
    if not is_adversarial and reference_trace is not None:
        tasks["process_alignment"] = async_eval_process_alignment(
            task_description=task_description,
            category=category,
            proxy_logs=proxy_logs,
            reference_trace=reference_trace,
            model=single_model,
        )

    # Code process (Phase 5) — auto-detects applicability from logs;
    # returns score=None when no code activity, excluded from QP aggregate.
    if not is_adversarial and category not in ("conceptual_qa",):
        tasks["code_process"] = async_eval_code_process(
            task_description=task_description,
            proxy_logs=proxy_logs,
            actual_output=actual_output,
            model=single_model,
        )

    # Conversational metrics — unchanged
    if conversational_test_case is not None:
        tasks["role_adherence"] = _async_eval_role_adherence(
            conversational_test_case,
            single_model,
        )
        tasks["knowledge_retention"] = _async_eval_knowledge_retention(
            conversational_test_case,
            single_model,
        )
        tasks["topic_adherence"] = _async_eval_topic_adherence(
            conversational_test_case,
            single_model,
        )

    return tasks


def evaluate_all_process_metrics(
    task_description: str,
    actual_output: str,
    proxy_logs: list,
    category: str = "",
    conversational_test_case=None,
    model=None,
    reference_trace: Optional[dict] = None,
    is_adversarial: bool = False,
) -> dict:
    """Run all process-level metrics in parallel and return consolidated results.

    Phase 4: Replaced tool-bound metrics with tool-agnostic process_reasonableness
    and process_alignment. Multi-model support is preserved.

    Args:
        task_description: Text description of the task.
        actual_output: Agent's combined text output.
        proxy_logs: Tool call logs from MCPProxy (list of ToolCallLog objects).
        category: Task category (e.g. "implementation", "data_analysis").
        conversational_test_case: Clean ConversationalTestCase (for role_adherence,
            knowledge_retention, topic_adherence).
        model: LLM judge model — single string, list of strings, or None.
        reference_trace: Reference execution data (from ReferenceStore) for step
            efficiency and process alignment anchoring.
        is_adversarial: Whether this is an adversarial task (skips alignment).

    Returns:
        Dict with per-metric scores (cross-model average), an aggregate
        process score, and ``_per_model`` breakdown when multi-model.
    """
    import copy
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

    # Pre-populate chatbot_role on conversational test case before copying
    if conversational_test_case is not None:
        if conversational_test_case.chatbot_role is None:
            conversational_test_case.chatbot_role = "quantitative finance tutor"

    # ── Build tasks for ALL models ──
    # Each model gets its own deep-copied test cases to avoid state
    # conflicts when DeepEval metrics mutate internal fields concurrently.
    # flat_tasks: list of (model_name, metric_name, coroutine)
    flat_tasks: list[tuple[str, str, object]] = []
    for model_idx, single_model in enumerate(eval_models):
        mname = model_names[model_idx]
        # Deep copy test cases per model so concurrent a_measure() calls
        # don't interfere with each other
        model_conv_tc = (
            copy.deepcopy(conversational_test_case)
            if conversational_test_case is not None
            else None
        )
        tasks_for_model = _build_process_tasks_for_model(
            single_model=single_model,
            task_description=task_description,
            actual_output=actual_output,
            proxy_logs=proxy_logs,
            category=category,
            conversational_test_case=model_conv_tc,
            is_adversarial=is_adversarial,
            reference_trace=reference_trace,
        )
        for metric_name, coro in tasks_for_model.items():
            flat_tasks.append((mname, metric_name, coro))

    total_calls = len(flat_tasks)
    _CONCURRENCY = 20  # avoid OpenRouter rate-limit throttling
    print(
        f"    Running {total_calls} process metric calls "
        f"({len(eval_models)} model(s) × metrics) in parallel "
        f"(concurrency={_CONCURRENCY})..."
    )
    t0 = _time.time()

    coros = [coro for _, _, coro in flat_tasks]

    async def _run_all():
        sem = asyncio.Semaphore(_CONCURRENCY)

        async def _limited(c):
            async with sem:
                return await c

        return await asyncio.gather(
            *[_limited(c) for c in coros], return_exceptions=True
        )

    raw_results = _run_async(_run_all())
    elapsed = _time.time() - t0
    print(f"    Completed {total_calls} process metric calls in {elapsed:.1f}s")

    # ── Collect per-model results ──
    # model_results[model_name][metric_name] = {score, reason, ...}
    model_results: dict[str, dict[str, dict]] = {mname: {} for mname in model_names}

    for i, (mname, metric_name, _) in enumerate(flat_tasks):
        raw = raw_results[i]
        if isinstance(raw, Exception):
            model_results[mname][metric_name] = {
                "score": 0.5,
                "reason": f"{metric_name} error: {raw}",
                "passed": True,
            }
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

    # ── Compute aggregate process score (cross-model averaged) ──
    all_scores = [
        v["score"]
        for k, v in results.items()
        if isinstance(v, dict)
        and "score" in v
        and v.get("score") is not None
        and not v.get("skipped", False)
        and k != "knowledge_retention"
    ]
    results["aggregate_process_score"] = round(
        sum(all_scores) / len(all_scores) if all_scores else 0.5, 4
    )
    print(f"      aggregate_process_score: {results['aggregate_process_score']}")

    # ── Per-model aggregate breakdown ──
    if multi_model:
        per_model_agg: dict[str, dict] = {}
        for mname in model_names:
            m_scores = [
                v.get("score", 0.5)
                for k, v in model_results[mname].items()
                if isinstance(v, dict)
                and v.get("score") is not None
                and not v.get("skipped", False)
                and k != "knowledge_retention"
            ]
            per_model_agg[mname] = {
                "aggregate_process_score": round(
                    sum(m_scores) / len(m_scores) if m_scores else 0.5, 4
                ),
                **{
                    k: v.get("score")
                    for k, v in model_results[mname].items()
                    if isinstance(v, dict)
                },
            }
        results["_per_model"] = per_model_agg
        print("      Per-model aggregate process scores:")
        for mname in model_names:
            agg = per_model_agg[mname]["aggregate_process_score"]
            print(f"        {mname}: {agg}")

    if is_adversarial:
        print("      (adversarial mode: process_alignment skipped)")

    return results
