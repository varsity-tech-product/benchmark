"""LLM-as-Judge for Quant Result quality (Phase 3).

Evaluates the RESULT QUALITY of an agent's task execution by comparing
against reference execution results (when available) and applying
category-specific rubrics.

Three sub-dimensions:
    Numerical Accuracy (0.35): Are quantitative results close to reference?
    Completeness      (0.35): Did agent produce all expected outputs?
    Correctness       (0.30): Is the methodology sound, even if numbers differ?

Uses 5-point ordinal scale: {0.0, 0.25, 0.5, 0.75, 1.0}.
"""

import json as _json
import os
import re

from config.llm_config import resolve_deepeval_model

try:
    from deepeval.models.llms.openai_model import GPTModel

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


# ──────────────────────────────────────────────────────────────
# Category-specific rubrics for result evaluation
# ──────────────────────────────────────────────────────────────
_CATEGORY_RUBRICS = {
    "data_analysis": (
        "Focus on: (1) correct data loading and parsing (CSV read, date "
        "parsing, dtype handling); (2) accurate statistical summaries "
        "(describe, mean, std, percentiles, distribution shape); "
        "(3) valid domain-specific observations (OHLCV column semantics, "
        "missing-data patterns, return computation, volume anomalies); "
        "(4) data quality checks (NaN detection, gap identification, "
        "outlier flagging, calendar-aware gap vs feed-issue distinction); "
        "(5) appropriate use of pandas operations (rolling, pct_change, "
        "groupby, resample) with correct parameters."
    ),
    "strategy": (
        "Focus on: (1) correct signal logic (entry/exit conditions based "
        "on indicator crossovers, thresholds, or regime detection); "
        "(2) appropriate indicator parameter choices with justification "
        "(short/long window lengths, lookback periods); (3) backtest "
        "execution producing valid performance metrics (Sharpe ratio, "
        "total/annualized return, max drawdown, win rate); (4) risk "
        "considerations (position sizing, stop-loss, transaction costs); "
        "(5) honest assessment of strategy limitations and regime "
        "sensitivity (trending vs mean-reverting markets)."
    ),
    "implementation": (
        "Focus on: (1) code correctness (produces expected numerical "
        "output matching known reference values); (2) appropriate use "
        "of pandas/numpy APIs (rolling().mean(), pct_change(), vectorized "
        "operations); (3) edge case handling (NaN at series boundaries, "
        "alignment issues, insufficient data); (4) computational "
        "efficiency (vectorized over iterative, avoiding unnecessary "
        "copies); (5) verification against known values or manual "
        "calculation to confirm implementation correctness."
    ),
    "backtest": (
        "Focus on: (1) correct interpretation of backtest metrics "
        "(Sharpe ratio magnitude and sign, max drawdown severity, "
        "win rate vs profit factor relationship); (2) understanding of "
        "statistical significance (sample size, regime dependency, "
        "look-ahead bias risks); (3) identification of potential "
        "overfitting signals (excessive parameter tuning, in-sample "
        "vs out-of-sample gap); (4) valid comparison reasoning when "
        "multiple strategies or parameter sets are evaluated; "
        "(5) actionable interpretation (what the metrics imply for "
        "real deployment feasibility)."
    ),
    "debug": (
        "Focus on: (1) correct identification of the root cause, not "
        "just surface symptoms (e.g., off-by-one in index vs wrong "
        "formula); (2) targeted fix that addresses the underlying "
        "issue without introducing new problems; (3) verification "
        "that the fix restores correct behavior with concrete evidence "
        "(before/after output comparison); (4) explanation of why the "
        "original code failed (the mechanism, not just 'it was wrong'); "
        "(5) defensive checks or best practices suggested to prevent "
        "similar bugs."
    ),
    "end_to_end": (
        "Focus on: (1) complete pipeline coverage from data ingestion "
        "through indicators, signal generation, backtesting, to "
        "performance analysis; (2) correct integration between pipeline "
        "stages (data flows, column dependencies, date alignment); "
        "(3) production of all required artifacts (runnable script, "
        "performance metrics, equity curve or signal chart); "
        "(4) coherent interpretation of final results connecting "
        "strategy logic to observed performance; (5) iterative "
        "refinement evidence (parameter tuning, comparing variants, "
        "or suggesting improvements based on results)."
    ),
    "adversarial": (
        "Focus on: (1) factual accuracy of financial/quantitative "
        "concepts presented; (2) appropriate recognition of limitations, "
        "risks, or ethical concerns in the scenario; (3) refusal to "
        "provide harmful, misleading, or overconfident advice where "
        "applicable; (4) nuanced explanation that addresses the "
        "student's potential misconception without being dismissive; "
        "(5) educational redirection toward sound practices when "
        "the student's premise is flawed."
    ),
}


def _get_category_rubric_text(category: str) -> str:
    """Get a detailed category-specific rubric for result evaluation."""
    return _CATEGORY_RUBRICS.get(category, _CATEGORY_RUBRICS["adversarial"])


# ──────────────────────────────────────────────────────────────
# Agent result extraction
# ──────────────────────────────────────────────────────────────


def _extract_agent_key_outputs(tool_logs: list) -> str:
    """Extract key outputs from agent's tool logs for result evaluation.

    Focuses on outputs from substantive tools (shell_exec results,
    backtest metrics, computed indicators, etc.).
    """
    key_outputs = []
    _SKIP_TOOLS = {"send_message", "get_environment_info"}

    for log in tool_logs:
        if log.name in _SKIP_TOOLS:
            continue
        if not log.result:
            continue

        # Truncate long outputs but keep enough for evaluation
        result_preview = str(log.result)[:400]
        status = "OK" if log.success else "FAIL"
        key_outputs.append(f"  {log.name} [{status}]: {result_preview}")

    return "\n".join(key_outputs[-15:]) if key_outputs else "  (no tool outputs)"


def _extract_agent_summary(conversation: list) -> str:
    """Extract a concise summary of what the agent communicated."""
    assistant_msgs = [
        t["content"] for t in (conversation or []) if t.get("role") == "assistant"
    ]
    if not assistant_msgs:
        return "(no agent responses)"

    # Use last 3 messages as the summary (most relevant to final result)
    recent = assistant_msgs[-3:]
    summary_parts = []
    for msg in recent:
        # Truncate each message
        text = str(msg)[:500]
        summary_parts.append(text)

    return "\n---\n".join(summary_parts)


def _list_workspace_files(workspace_path: str) -> list[str]:
    """List files in the workspace directory."""
    if not workspace_path or not os.path.isdir(workspace_path):
        return []
    try:
        return sorted(os.listdir(workspace_path))
    except OSError:
        return []


# ──────────────────────────────────────────────────────────────
# Prompt construction
# ──────────────────────────────────────────────────────────────


def _build_result_judge_prompt(
    task_description: str,
    category: str,
    *,
    agent_key_outputs: str,
    agent_workspace_files: list[str],
    agent_summary: str,
    reference: dict | None,
) -> str:
    """Build the result quality evaluation prompt."""
    category_rubric = _get_category_rubric_text(category)

    header = """You are evaluating the RESULT QUALITY of an AI tutoring agent's task execution.

SCORING SCALE: Use ONLY these values: {0.0, 0.25, 0.5, 0.75, 1.0}.
When in doubt between two levels, select the LOWER score."""

    task_section = f"""
TASK: {task_description}
CATEGORY: {category}
CATEGORY-SPECIFIC FOCUS: {category_rubric}"""

    # Reference section
    ref_section = ""
    if reference:
        ref_key_results = reference.get("key_results", {})
        ref_workspace = reference.get("workspace_files", [])
        ref_trace = reference.get("trace_summary", [])
        ref_output = ""
        if isinstance(ref_trace, list):
            ref_output = "\n".join(
                f"  {i+1}. {s}" for i, s in enumerate(ref_trace[-8:])
            )
        else:
            ref_output = str(ref_trace)[:500]

        ref_section = f"""
REFERENCE RESULT (expert baseline):
- Key metrics: {_json.dumps(ref_key_results, indent=2, default=str)}
- Files produced: {', '.join(ref_workspace) if ref_workspace else '(none)'}
- Execution trace (last steps):
{ref_output}"""

    # Agent section
    agent_files_str = (
        ", ".join(agent_workspace_files) if agent_workspace_files else "(none)"
    )
    agent_section = f"""
AGENT RESULT:
- Files produced: {agent_files_str}
- Key tool outputs:
{agent_key_outputs}
- Agent's explanation (summary):
{agent_summary[:800]}"""

    # Dimensions
    if reference:
        dimensions = """
EVALUATE these THREE dimensions:

1. NUMERICAL ACCURACY (0.0-1.0):
   Compare the agent's quantitative outputs against the reference.
   - 1.0:  Results match reference closely (within ~5% for numerical values)
   - 0.75: Results mostly correct, minor deviations (5-15%)
   - 0.5:  Some results correct, some significantly off (15-30%)
   - 0.25: Most results substantially different from reference
   - 0.0:  No correct numerical results, or no results produced

2. COMPLETENESS (0.0-1.0):
   Did the agent produce ALL expected outputs compared to the reference?
   - 1.0:  All reference outputs present (files, metrics, visualizations)
   - 0.75: Most outputs present, one minor item missing
   - 0.5:  Core outputs present but several secondary items missing
   - 0.25: Only partial outputs, several key items missing
   - 0.0:  No meaningful outputs produced

3. CORRECTNESS (0.0-1.0):
   Even if numbers differ from reference, is the methodology sound?
   - 1.0:  Methodology is correct and well-implemented
   - 0.75: Methodology mostly correct, minor issues in approach
   - 0.5:  Basic approach is right but implementation has flaws
   - 0.25: Significant methodological problems
   - 0.0:  Fundamentally wrong approach

Return ONLY a JSON object (no markdown, no extra text):
{"numerical_accuracy": <float>, "completeness": <float>, "correctness": <float>, "reason": "<brief explanation>"}"""
    else:
        # No reference — evaluate on standalone merit
        dimensions = """
EVALUATE these THREE dimensions (no reference baseline available):

1. NUMERICAL ACCURACY (0.0-1.0):
   Do the agent's quantitative outputs appear reasonable and internally consistent?
   - 1.0:  All numbers are plausible for financial data and internally consistent
   - 0.75: Most numbers reasonable, minor inconsistencies
   - 0.5:  Some numbers seem off or inconsistent
   - 0.25: Several implausible or contradictory values
   - 0.0:  No numerical results, or clearly wrong values

2. COMPLETENESS (0.0-1.0):
   Given the task requirements, did the agent produce all expected outputs?
   - 1.0:  Task fully addressed — all requested outputs present
   - 0.75: Most requirements met, one minor item missing
   - 0.5:  Core requirements met but several items missing
   - 0.25: Only partial work completed
   - 0.0:  Task barely attempted

3. CORRECTNESS (0.0-1.0):
   Is the methodology appropriate for the task?
   - 1.0:  Sound methodology, correct use of financial concepts
   - 0.75: Mostly correct approach, minor conceptual issues
   - 0.5:  Basic approach works but has notable flaws
   - 0.25: Significant methodological problems
   - 0.0:  Fundamentally wrong approach

Return ONLY a JSON object (no markdown, no extra text):
{"numerical_accuracy": <float>, "completeness": <float>, "correctness": <float>, "reason": "<brief explanation>"}"""

    return header + task_section + ref_section + agent_section + dimensions


# ──────────────────────────────────────────────────────────────
# JSON extraction (shared with process_metrics.py pattern)
# ──────────────────────────────────────────────────────────────


def _extract_json_from_response(text: str) -> dict:
    """Extract JSON object from LLM response, handling markdown fences."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines).strip()
    try:
        return _json.loads(text)
    except _json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", text, re.DOTALL)
        if match:
            try:
                return _json.loads(match.group())
            except _json.JSONDecodeError:
                pass
    return {}


def _clamp_ordinal(val, default=0.5) -> float:
    """Snap value to nearest 5-point ordinal."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    ordinals = [0.0, 0.25, 0.5, 0.75, 1.0]
    return min(ordinals, key=lambda x: abs(x - v))


# ──────────────────────────────────────────────────────────────
# Main evaluation entry point
# ──────────────────────────────────────────────────────────────

_SUB_WEIGHTS = {
    "numerical_accuracy": 0.35,
    "completeness": 0.35,
    "correctness": 0.30,
}


async def async_evaluate_result_quality(
    task_description: str,
    category: str,
    workspace_path: str,
    tool_logs: list,
    conversation: list,
    model=None,
    reference: dict | None = None,
) -> dict:
    """Evaluate result quality using LLM-as-Judge (multi-model).

    Calls all EVAL_DEFAULT_MODELS in parallel and averages sub-scores.

    Args:
        task_description: The task's description text.
        category: Task category (e.g. "implementation", "data_analysis").
        workspace_path: Path to agent's workspace directory.
        tool_logs: Tool call logs (list of ToolCallLog from proxy.get_logs()).
        conversation: Full conversation (list of {role, content} dicts).
        model: LLM judge model — single string, list of strings, or None.
            None → use all EVAL_DEFAULT_MODELS in parallel.
        reference: Reference execution data from ReferenceStore, or None.

    Returns:
        Dict with score, sub_scores, reason, has_reference, and _per_model breakdown.
    """
    import asyncio

    from config.llm_config import EVAL_DEFAULT_MODELS

    if not DEEPEVAL_AVAILABLE:
        return {
            "score": 0.5,
            "reason": "deepeval not available (needed for GPTModel)",
            "sub_scores": {},
            "has_reference": reference is not None,
        }

    # ── Resolve model list ──
    if isinstance(model, list) and len(model) > 0:
        eval_models = model
    elif model is None:
        eval_models = list(EVAL_DEFAULT_MODELS)
    else:
        eval_models = [model]
    multi_model = len(eval_models) > 1

    # Extract agent results (shared across all model calls)
    agent_key_outputs = _extract_agent_key_outputs(tool_logs)
    agent_workspace_files = _list_workspace_files(workspace_path)
    agent_summary = _extract_agent_summary(conversation)

    # Build prompt (same for all models)
    prompt = _build_result_judge_prompt(
        task_description=task_description,
        category=category,
        agent_key_outputs=agent_key_outputs,
        agent_workspace_files=agent_workspace_files,
        agent_summary=agent_summary,
        reference=reference,
    )

    # ── Call all models in parallel ──
    async def _call_single_model(m):
        try:
            model_obj = resolve_deepeval_model(m)
            if isinstance(model_obj, str):
                model_obj = GPTModel(model=model_obj)
            response_text, _ = await model_obj.a_generate(prompt)
            return _extract_json_from_response(response_text)
        except Exception as e:
            return {"_error": str(e)}

    raw_results = await asyncio.gather(*[_call_single_model(m) for m in eval_models])

    # ── Parse per-model results ──
    per_model: dict[str, dict] = {}
    for i, m in enumerate(eval_models):
        raw = raw_results[i]
        if "_error" in raw:
            sub = {k: 0.5 for k in _SUB_WEIGHTS}
            reason = f"ResultJudge error: {raw['_error']}"
        else:
            sub = {k: _clamp_ordinal(raw.get(k, 0.5)) for k in _SUB_WEIGHTS}
            reason = raw.get("reason", "")
        m_overall = sum(_SUB_WEIGHTS[k] * sub[k] for k in _SUB_WEIGHTS)
        per_model[m] = {
            "score": round(m_overall, 4),
            "sub_scores": sub,
            "reason": reason,
        }

    # ── Cross-model average ──
    avg_sub = {}
    for k in _SUB_WEIGHTS:
        vals = [per_model[m]["sub_scores"][k] for m in eval_models]
        avg_sub[k] = round(sum(vals) / len(vals), 4)
    avg_overall = sum(_SUB_WEIGHTS[k] * avg_sub[k] for k in _SUB_WEIGHTS)

    # Log per-model scores
    if multi_model:
        model_parts = []
        for m in eval_models:
            short = m.split("/")[-1] if "/" in m else m
            model_parts.append(f"{short}={per_model[m]['score']}")
        print(f"    ResultJudge per-model: {', '.join(model_parts)}")

    result = {
        "score": round(avg_overall, 4),
        "reason": per_model[eval_models[0]].get("reason", ""),
        "sub_scores": avg_sub,
        "has_reference": reference is not None,
    }
    if multi_model:
        result["_per_model"] = per_model

    return result


def evaluate_result_quality(
    task_description: str,
    category: str,
    workspace_path: str,
    tool_logs: list,
    conversation: list,
    model=None,
    reference: dict | None = None,
) -> dict:
    """Synchronous wrapper for result quality evaluation."""
    import asyncio

    import nest_asyncio

    nest_asyncio.apply()
    loop = asyncio.get_event_loop()
    return loop.run_until_complete(
        async_evaluate_result_quality(
            task_description,
            category,
            workspace_path,
            tool_logs,
            conversation,
            model,
            reference,
        )
    )
