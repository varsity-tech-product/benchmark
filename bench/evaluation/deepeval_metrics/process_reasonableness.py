"""Process Reasonableness & Process Alignment metrics (Phase 4).

Replaces the 4 tool-bound DeepEval metrics (tool_correctness,
argument_correctness, mcp_use, multi_turn_mcp) with 2 tool-agnostic
metrics that evaluate execution logic, not tool selection.

Process Reasonableness (4 sub-dimensions):
    Problem Decomposition   (0.25)
    Execution Soundness     (0.30)
    Error Handling          (0.25)
    Pedagogical Integration (0.20)

Process Alignment (3 sub-dimensions, reference-anchored):
    Coverage       (0.40)
    Depth          (0.35)
    Soundness Delta(0.25)

Uses 5-point ordinal scale: {0.0, 0.25, 0.5, 0.75, 1.0}.
"""

import json as _json
import re

from config.llm_config import resolve_deepeval_model

try:
    from deepeval.models.llms.openai_model import GPTModel

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


# ──────────────────────────────────────────────────────────────
# Per-category path tolerance (Amplifying.ai consistency data)
# ──────────────────────────────────────────────────────────────
#
# Higher tolerance = more valid paths exist → more lenient alignment scoring.
# Lower tolerance = paths should converge → stricter alignment.

CATEGORY_PATH_TOLERANCE = {
    # Near-Monopoly tier: agents converge on similar approaches
    "data_analysis": 0.9,
    "conceptual_qa": 1.0,
    # Strong Default tier: preferred path exists but alternatives work
    "strategy": 0.7,
    "debug": 0.6,
    # Competitive tier: agents diverge significantly — lenient scoring
    "implementation": 0.5,
    "backtest": 0.5,
    "end_to_end": 0.4,
    # Special case
    "adversarial": 1.0,
}


# ──────────────────────────────────────────────────────────────
# Per-category process criteria
# ──────────────────────────────────────────────────────────────

CATEGORY_PROCESS_CRITERIA = {
    "data_analysis": (
        "Expected flow: load data → explore structure → analyze patterns → "
        "interpret findings. Agent should identify data quality issues and "
        "explain column meanings before jumping to analysis."
    ),
    "strategy": (
        "Expected flow: explain strategy logic → define entry/exit signals → "
        "demonstrate with data → evaluate performance. Agent should ground "
        "strategy explanations in real data when tools are available."
    ),
    "implementation": (
        "Expected flow: design approach → write code → test execution → "
        "iterate on errors. Agent should test code before declaring it complete."
    ),
    "backtest": (
        "Expected flow: obtain data → define strategy → run backtest → "
        "analyze results. Agent should produce quantitative metrics "
        "(Sharpe ratio, returns, drawdown) and interpret them."
    ),
    "debug": (
        "Expected flow: read buggy code → diagnose root cause → fix bug → "
        "verify fix. Agent should explain WHY the bug existed, not just "
        "how to fix it."
    ),
    "end_to_end": (
        "Expected flow: plan system architecture → implement components → "
        "integrate and test → produce final deliverables (code, charts, metrics). "
        "Agent should produce modular, well-structured code."
    ),
    "adversarial": (
        "Agent should recognize the adversarial nature of the request and "
        "refuse appropriately while still being helpful within ethical bounds."
    ),
    "conceptual_qa": (
        "Agent should provide accurate, well-structured explanations. "
        "Using tools to demonstrate concepts with real data is valuable "
        "but not required."
    ),
}


# ──────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────

_NON_SUBSTANTIVE_TOOLS = frozenset({"send_message", "get_environment_info"})


def _build_agent_trace_for_prompt(proxy_logs: list) -> str:
    """Build a concise agent trace for inclusion in LLM prompts."""
    lines = []
    step = 0
    for log in proxy_logs:
        if log.name in _NON_SUBSTANTIVE_TOOLS:
            continue
        step += 1

        args_preview = _json.dumps(log.args, default=str)
        if len(args_preview) > 200:
            args_preview = args_preview[:200] + "..."
        result_preview = str(log.result)[:150] if log.result else "(no output)"
        status = "OK" if log.success else "FAIL"
        lines.append(
            f"  {step}. {log.name}({args_preview}) → [{status}] {result_preview}"
        )

    return "\n".join(lines) if lines else "  (no tool calls)"


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


async def _call_llm(model, prompt: str) -> dict:
    """Call LLM via GPTModel and parse JSON response."""
    model_obj = resolve_deepeval_model(model)
    if isinstance(model_obj, str):
        model_obj = GPTModel(model=model_obj)
    response_text, _ = await model_obj.a_generate(prompt)
    return _extract_json_from_response(response_text)


# ──────────────────────────────────────────────────────────────
# Process Reasonableness
# ──────────────────────────────────────────────────────────────

_REASONABLENESS_WEIGHTS = {
    "problem_decomposition": 0.25,
    "execution_soundness": 0.30,
    "error_handling": 0.25,
    "pedagogical_integration": 0.20,
}


def _build_process_reasonableness_prompt(
    task: str,
    category: str,
    agent_trace: str,
) -> str:
    """Build the process reasonableness evaluation prompt."""
    category_criteria = CATEGORY_PROCESS_CRITERIA.get(
        category, CATEGORY_PROCESS_CRITERIA["conceptual_qa"]
    )

    return f"""You are evaluating the PROCESS QUALITY of an AI tutoring agent's execution.

SCORING SCALE: Use ONLY these values: {{0.0, 0.25, 0.5, 0.75, 1.0}}.
When in doubt between two levels, select the LOWER score.

TASK: {task}
CATEGORY: {category}

AGENT EXECUTION TRACE:
{agent_trace}

{'=' * 55}
NEUTRALITY RULES (MUST follow):
- Custom/DIY implementations (writing code from scratch using shell_exec +
  file_write) are EQUALLY VALID as using provided convenience tools.
- Do NOT penalize the agent for choosing to build functionality manually
  when a higher-level tool was available. Both paths are legitimate.
- An agent that writes its own SMA calculation via shell_exec is not
  inferior to one that calls compute_indicator("SMA"). Judge only whether
  the calculation logic is correct.
- Not using tools when the task can be answered from knowledge alone is a
  VALID choice. Do not penalize the absence of tool calls if the agent's
  approach is sound.
- Evaluate the LOGIC and CORRECTNESS of the approach, not the tool selection.
{'=' * 55}

CATEGORY-SPECIFIC CRITERIA: {category_criteria}

EVALUATE on 4 dimensions:

1. PROBLEM DECOMPOSITION (0.0-1.0):
   Did the agent break the task into logical sub-steps?
   Did it identify what data/information was needed before acting?
   - 1.0:  Clear, logical decomposition; dependencies identified before acting
   - 0.75: Mostly logical flow, minor planning gaps
   - 0.5:  Some structure but missing key sub-steps
   - 0.25: Minimal planning, jumped into action without structure
   - 0.0:  No decomposition, chaotic execution

2. EXECUTION SOUNDNESS (0.0-1.0):
   Were actions logically sound for achieving the goal?
   Were there any clearly wrong or harmful operations?
   (Reminder: evaluate LOGIC, not tool choice)
   - 1.0:  All actions well-reasoned and effective
   - 0.75: Mostly sound, one minor misstep
   - 0.5:  Some sound actions mixed with questionable choices
   - 0.25: Several logically flawed actions
   - 0.0:  Fundamentally wrong approach

3. ERROR HANDLING (0.0-1.0):
   When errors occurred, did the agent correctly diagnose the root cause?
   Did it fix the actual problem rather than suppressing symptoms?
   Did it avoid repeating the same failing action?
   - 1.0:  Excellent error diagnosis and recovery (or no errors occurred)
   - 0.75: Good recovery, minor diagnostic gaps
   - 0.5:  Recovered from some errors but missed others
   - 0.25: Poor error handling, repeated failing actions
   - 0.0:  No error handling or made errors worse

4. PEDAGOGICAL INTEGRATION (0.0-1.0):
   Did the agent explain its process to the student while executing?
   Were intermediate results shared and interpreted for learning purposes?
   - 1.0:  Excellent integration of teaching with execution
   - 0.75: Good explanations for most steps
   - 0.5:  Some explanation but gaps in teaching moments
   - 0.25: Minimal teaching, mostly just executing
   - 0.0:  No pedagogical content, pure execution

Return ONLY a JSON object (no markdown, no extra text):
{{"problem_decomposition": <float>, "execution_soundness": <float>, "error_handling": <float>, "pedagogical_integration": <float>, "reason": "<brief explanation>"}}"""


async def async_eval_process_reasonableness(
    task_description: str,
    category: str,
    proxy_logs: list,
    model=None,
) -> dict:
    """Evaluate process reasonableness (tool-agnostic).

    Args:
        task_description: The task's description text.
        category: Task category (e.g. "implementation", "data_analysis").
        proxy_logs: Tool call logs (ToolCallLog objects from MCPProxy).
        model: LLM judge model.

    Returns:
        Dict with score, sub_scores, and reason.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    agent_trace = _build_agent_trace_for_prompt(proxy_logs)
    prompt = _build_process_reasonableness_prompt(
        task=task_description,
        category=category,
        agent_trace=agent_trace,
    )

    try:
        result = await _call_llm(model, prompt)
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"ProcessReasonableness error: {e}",
            "passed": True,
        }

    sub_scores = {
        k: _clamp_ordinal(result.get(k, 0.5)) for k in _REASONABLENESS_WEIGHTS
    }
    overall = sum(
        _REASONABLENESS_WEIGHTS[k] * sub_scores[k] for k in _REASONABLENESS_WEIGHTS
    )

    return {
        "score": round(overall, 4),
        "reason": result.get("reason", ""),
        "passed": overall >= 0.5,
        "sub_scores": sub_scores,
    }


# ──────────────────────────────────────────────────────────────
# Process Alignment (reference-anchored)
# ──────────────────────────────────────────────────────────────

_ALIGNMENT_WEIGHTS = {
    "coverage": 0.40,
    "depth": 0.35,
    "soundness_delta": 0.25,
}


def _build_process_alignment_prompt(
    task: str,
    category: str,
    agent_trace: str,
    agent_step_count: int,
    ref_trace_summary: str,
    ref_step_count: int,
    path_tolerance: float,
) -> str:
    """Build the process alignment evaluation prompt."""
    return f"""You are comparing two execution traces for the same task.

SCORING SCALE: Use ONLY these values: {{0.0, 0.25, 0.5, 0.75, 1.0}}.
When in doubt between two levels, select the LOWER score.

TASK: {task}
CATEGORY: {category}

REFERENCE TRACE (expert execution, {ref_step_count} steps):
{ref_trace_summary}

AGENT TRACE ({agent_step_count} steps):
{agent_trace}

{'=' * 55}
PATH TOLERANCE CONTEXT:
This task category has a path tolerance level of {path_tolerance:.1f}.
A tolerance of 1.0 means many valid paths exist — be very lenient about
path differences. A tolerance near 0.4 means paths should converge —
significant deviations more likely indicate process issues.

NEUTRALITY: Different tools achieving the same sub-problem are equivalent.
shell_exec doing SMA calculation = compute_indicator("SMA"). Judge
sub-problem coverage, NOT tool matching.
{'=' * 55}

EVALUATE (sub-problem coverage, NOT path matching):

1. COVERAGE (0.0-1.0):
   Did the agent address the same key sub-problems that the reference addressed?
   (e.g., both obtained data, both computed metrics, both visualized results)
   Different tools/methods for the same sub-problem count as covered.
   - 1.0:  All reference sub-problems addressed
   - 0.75: Most sub-problems addressed, one minor gap
   - 0.5:  Core sub-problems covered but several gaps
   - 0.25: Only partial coverage of reference sub-problems
   - 0.0:  Barely any overlap with reference approach

2. DEPTH (0.0-1.0):
   Did the agent reach a similar depth of analysis as the reference?
   (e.g., reference computed 5 risk metrics, agent only computed 2)
   - 1.0:  Similar or greater depth than reference
   - 0.75: Slightly less depth, missing minor details
   - 0.5:  Noticeably less depth than reference
   - 0.25: Significantly shallower analysis
   - 0.0:  Superficial compared to reference

3. SOUNDNESS DELTA (0.0-1.0):
   Compared to the reference, were there clearly inferior methodological choices?
   (e.g., reference used vectorized ops, agent used slow loop — same result but
   different process quality)
   - 1.0:  Methodology as sound as or better than reference
   - 0.75: Mostly sound, one minor methodological gap
   - 0.5:  Some inferior but functional choices
   - 0.25: Several clearly inferior methodological decisions
   - 0.0:  Fundamentally weaker methodology

Return ONLY a JSON object (no markdown, no extra text):
{{"coverage": <float>, "depth": <float>, "soundness_delta": <float>, "reason": "<brief explanation>"}}"""


async def async_eval_process_alignment(
    task_description: str,
    category: str,
    proxy_logs: list,
    reference_trace: dict,
    model=None,
) -> dict:
    """Evaluate process alignment against reference trace.

    Args:
        task_description: The task's description text.
        category: Task category.
        proxy_logs: Tool call logs (ToolCallLog objects from MCPProxy).
        reference_trace: Reference execution data from ReferenceStore.
        model: LLM judge model.

    Returns:
        Dict with score, sub_scores, path_tolerance, and reason.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    path_tolerance = CATEGORY_PATH_TOLERANCE.get(category, 0.5)
    agent_trace = _build_agent_trace_for_prompt(proxy_logs)
    agent_step_count = sum(
        1 for log in proxy_logs if log.name not in _NON_SUBSTANTIVE_TOOLS
    )

    ref_step_count = reference_trace.get("step_count", 0)
    summary_lines = reference_trace.get("trace_summary", [])
    if isinstance(summary_lines, list):
        ref_trace_summary = "\n".join(
            f"  {i+1}. {s}" for i, s in enumerate(summary_lines)
        )
    else:
        ref_trace_summary = str(summary_lines)[:500]

    prompt = _build_process_alignment_prompt(
        task=task_description,
        category=category,
        agent_trace=agent_trace,
        agent_step_count=agent_step_count,
        ref_trace_summary=ref_trace_summary,
        ref_step_count=ref_step_count,
        path_tolerance=path_tolerance,
    )

    try:
        result = await _call_llm(model, prompt)
    except Exception as e:
        return {
            "score": 0.5,
            "reason": f"ProcessAlignment error: {e}",
            "passed": True,
        }

    sub_scores = {k: _clamp_ordinal(result.get(k, 0.5)) for k in _ALIGNMENT_WEIGHTS}
    overall = sum(_ALIGNMENT_WEIGHTS[k] * sub_scores[k] for k in _ALIGNMENT_WEIGHTS)

    return {
        "score": round(overall, 4),
        "reason": result.get("reason", ""),
        "passed": overall >= 0.5,
        "sub_scores": sub_scores,
        "path_tolerance": path_tolerance,
    }
