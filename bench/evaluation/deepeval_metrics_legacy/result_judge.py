"""LLM-as-Judge for Quant Result quality (Phase 3).

Evaluates the RESULT QUALITY of an agent's task execution by comparing
against reference execution results (when available) and the task's
expected_outcome acceptance criteria.

Two sub-dimensions (numerical accuracy is handled by code_eval Layer C):
    Completeness (0.55): Did agent produce all expected outputs?
    Correctness  (0.45): Are outputs usable, runnable, and in expected format?

Uses 10-point integer scale (1-10) normalized to 0.0-1.0.
"""

import json as _json
import os
import threading

from config.model_resolver import resolve_deepeval_model

from evaluation.deepeval_metrics._scoring_utils import extract_json_from_response

try:
    from deepeval.models.llms.openai_model import GPTModel

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


# ──────────────────────────────────────────────────────────────
# Agent result extraction
# ──────────────────────────────────────────────────────────────


def _extract_agent_key_outputs(tool_logs: list) -> str:
    """Extract key outputs from agent's tool logs for result evaluation.

    Focuses on outputs from substantive tools (shell_exec results,
    backtest metrics, computed indicators, etc.).
    """
    key_outputs = []
    # Skip tools whose results are reference/input material, not agent output.
    # Including these would let doc content (e.g. required file names) leak
    # into the judge prompt and bias completeness scoring.
    _SKIP_TOOLS = {
        "get_environment_info",
        "file_read",
        "file_list",
        "search_docs",
        "search_web",
    }

    for log in tool_logs:
        if log.name in _SKIP_TOOLS:
            continue
        if not log.result:
            continue

        # Keep enough per-result for the judge to see key metrics.
        # Most structured tool outputs (run_backtest ~800, evaluate_signal
        # ~770, compute_statistics ~1500) fit within 1500 chars.
        result_preview = str(log.result)[:1500]
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
    # Dynamic budget: 6000 chars total, split evenly across messages
    per_msg_limit = 6000 // len(recent)
    summary_parts = []
    for msg in recent:
        text = str(msg)[:per_msg_limit]
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
    expected_outcome: str | None = None,
) -> str:
    """Build the result quality evaluation prompt."""

    header = """You are evaluating the RESULT QUALITY of an AI tutoring agent's task execution.

SCORING SCALE: Rate each dimension as an INTEGER from 1 to 10.
Use the full range — avoid defaulting to middle scores (5-6) without justification."""

    task_section = f"""
TASK: {task_description}
CATEGORY: {category}"""

    if expected_outcome:
        task_section += f"""

EXPECTED OUTCOME (acceptance criteria):
{expected_outcome}

Evaluate the agent's outputs against the EXPECTED OUTCOME above.
Items not mentioned in EXPECTED OUTCOME should not be penalized if missing."""

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
{agent_summary}"""

    # Evaluation guidelines (reduce systematic LLM mis-judgments)
    guidelines = """
IMPORTANT EVALUATION GUIDELINES:
1. SUBSET VARIATION IS EXPECTED: The same metric computed over different
   time periods, data subsets, parameter choices, or model specifications
   will naturally produce different values. This is NOT inconsistency.
   Only flag contradictions when identical inputs yield conflicting outputs.
2. SEPARATE CORRECTNESS FROM QUALITY: A correctly implemented analysis
   may produce unfavorable results (poor strategy returns, weak model fit,
   insignificant test statistics). Judge whether the implementation is
   methodologically sound and executes without errors — not whether the
   results are impressive or match expectations.
3. ACCEPT ALTERNATIVE METHODS: There are often multiple valid approaches
   to the same analytical task. If the agent uses a different method than
   what you might expect but arrives at defensible results, this is not
   an error. Evaluate whether the chosen approach is reasonable for the
   stated objective.
4. INTERMEDIATE OUTPUT IS NORMAL: Exploratory analysis, debugging output,
   and iterative refinement are part of a sound analytical workflow.
   Do not penalize intermediate or superseded results as long as the
   final output is coherent.
5. CODE MUST BE EXECUTED TO COUNT: Code that was written but never
   successfully executed (shown as [FAIL] in tool outputs, or absent from
   tool outputs entirely) should NOT receive credit for completeness or
   correctness. Plans, drafts, and unexecuted code are preparation — not
   results. Only credit outputs from tools marked [OK]. If the agent
   wrote multiple code iterations, only the executed versions matter.
"""
    # Debug-specific guideline: separate "fix works" from "strategy profits"
    if category == "debug":
        guidelines += """6. DEBUG TASKS: For debugging tasks, "fix" means resolving the identified
   bug so the code behaves as architecturally intended (e.g., trades execute
   instead of canceling, warm-up period is respected, correct order type is
   used). The fix does NOT need to produce profitable results — a correctly
   fixed strategy may still lose money due to market conditions. Judge
   whether the bug was resolved, not whether the strategy is profitable.
"""

    # Dimensions — numerical accuracy is handled separately by programmatic
    # code_eval (Layer C), so the judge focuses on completeness + correctness.
    if reference:
        dimensions = """
EVALUATE these TWO dimensions (integer 1-10):

1. COMPLETENESS (1-10):
   Did the agent produce ALL expected outputs compared to the reference?
   - 10: All reference outputs present with full detail (files, metrics, visualizations)
   -  9: All key outputs present; one very minor element has slightly less detail
   -  8: All key outputs present but one minor output missing (e.g. a secondary chart)
   -  7: Most outputs present; 1-2 minor items missing but all core deliverables exist
   -  6: Core outputs present; a few secondary items missing
   -  5: Core outputs partially present; several items missing
   -  4: Some outputs present but notable gaps in core deliverables
   -  3: Only partial outputs; several key items missing
   -  2: Minimal outputs; most key items missing
   -  1: No meaningful outputs produced

2. CORRECTNESS (1-10):
   Are the outputs usable and in the expected format?
   - 10: All outputs are runnable/usable, formats match expectations, results fully actionable
   -  9: All outputs usable; one trivial format issue (e.g. minor label mismatch)
   -  8: Outputs mostly usable; minor format issues (e.g. missing column headers)
   -  7: Outputs functional but with some format or labeling inconsistencies
   -  6: Core outputs usable but several have format or quality issues
   -  5: Core outputs present but some are unusable or in wrong format
   -  4: Several outputs have broken formatting or are partially unusable
   -  3: Most outputs are broken, unrunnable, or in unexpected format
   -  2: Nearly all outputs are unusable
   -  1: Outputs are entirely unusable or missing

Return ONLY a JSON object (no markdown, no extra text):
{"completeness": <integer 1-10>, "correctness": <integer 1-10>, "reason": "<brief explanation>"}"""
    else:
        # No reference — evaluate on standalone merit
        dimensions = """
EVALUATE these TWO dimensions (no reference baseline available, integer 1-10):

1. COMPLETENESS (1-10):
   Given the task requirements, did the agent produce all expected outputs?
   - 10: Task fully addressed — all requested outputs present with full detail
   -  9: All key requirements met; one very minor element slightly abbreviated
   -  8: All key requirements met; one minor item missing
   -  7: Most requirements met; 1-2 minor items missing
   -  6: Core requirements met; a few secondary items missing
   -  5: Core requirements partially met; several items missing
   -  4: Some requirements addressed but notable gaps
   -  3: Only partial work completed; several key items missing
   -  2: Minimal work; most requirements unmet
   -  1: Task barely attempted

2. CORRECTNESS (1-10):
   Are the outputs usable and in the expected format?
   - 10: All outputs are runnable/usable, formats match expectations, results fully actionable
   -  9: All outputs usable; one trivial format issue
   -  8: Outputs mostly usable; minor format issues (e.g. missing column headers)
   -  7: Outputs functional but with some format or labeling inconsistencies
   -  6: Core outputs usable but several have format or quality issues
   -  5: Core outputs present but some are unusable or in wrong format
   -  4: Several outputs have broken formatting or are partially unusable
   -  3: Most outputs are broken, unrunnable, or in unexpected format
   -  2: Nearly all outputs are unusable
   -  1: Outputs are entirely unusable or missing

Return ONLY a JSON object (no markdown, no extra text):
{"completeness": <integer 1-10>, "correctness": <integer 1-10>, "reason": "<brief explanation>"}"""

    prompt = (
        header + task_section + ref_section + agent_section + guidelines + dimensions
    )

    # Safety cap: if prompt exceeds budget, trim agent_key_outputs
    _TOTAL_PROMPT_CAP = 40000
    if len(prompt) > _TOTAL_PROMPT_CAP:
        overshoot = len(prompt) - _TOTAL_PROMPT_CAP
        trimmed_outputs = agent_key_outputs[
            : max(200, len(agent_key_outputs) - overshoot)
        ]
        agent_section_trimmed = f"""
AGENT RESULT:
- Files produced: {agent_files_str}
- Key tool outputs (trimmed):
{trimmed_outputs}
- Agent's explanation (summary):
{agent_summary}"""
        prompt = (
            header
            + task_section
            + ref_section
            + agent_section_trimmed
            + guidelines
            + dimensions
        )

    return prompt


def _normalize_score(val, default=0.5) -> float:
    """Normalize a 1-10 integer score to 0.0-1.0 range.

    Uses normalize_10pt from _scoring_utils.py.
    Falls back to default if parsing fails.
    """
    from evaluation.deepeval_metrics._scoring_utils import normalize_10pt

    return normalize_10pt(val, default=default)


# ──────────────────────────────────────────────────────────────
# Main evaluation entry point
# ──────────────────────────────────────────────────────────────

_SUB_WEIGHTS = {
    "completeness": 0.55,
    "correctness": 0.45,
}


_ABORT_SENTINEL = object()


async def async_evaluate_result_quality(
    task_description: str,
    category: str,
    workspace_path: str,
    tool_logs: list,
    conversation: list,
    model=None,
    reference: dict | None = None,
    expected_outcome: str | None = None,
    abort_event: threading.Event | None = None,
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
        expected_outcome: Task's expected outcome (acceptance criteria), or None.
        abort_event: Shared threading.Event for cross-thread abort signaling.

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
        expected_outcome=expected_outcome,
    )

    # ── Call all models in parallel with abort protection ──
    _abort = abort_event if abort_event is not None else threading.Event()
    _first_error: list[Exception] = []

    async def _call_single_model(m):
        model_obj = resolve_deepeval_model(m)
        if isinstance(model_obj, str):
            from config.pricing import get_deepeval_cost_kwargs

            model_obj = GPTModel(model=model_obj, **get_deepeval_cost_kwargs(model_obj))
        response_text, call_cost = await model_obj.a_generate(prompt)
        parsed = extract_json_from_response(response_text)
        parsed["_eval_cost"] = float(call_cost) if call_cost else 0.0
        return parsed

    async def _guarded(m):
        if _abort.is_set():
            return _ABORT_SENTINEL
        try:
            return await _call_single_model(m)
        except Exception as e:
            _abort.set()
            if not _first_error:
                _first_error.append(e)
            return _ABORT_SENTINEL

    raw_results = await asyncio.gather(*[_guarded(m) for m in eval_models])

    # Propagate first error
    if _first_error:
        raise _first_error[0]

    # ── Parse per-model results ──
    per_model: dict[str, dict] = {}
    total_eval_cost = 0.0
    cost_by_model: dict[str, float] = {}
    for i, m in enumerate(eval_models):
        raw = raw_results[i]
        if raw is _ABORT_SENTINEL:
            raise RuntimeError(f"ResultJudge: unexpected abort for model {m}")
        m_cost = raw.get("_eval_cost", 0.0)
        cost_by_model[m] = round(m_cost, 6)
        total_eval_cost += m_cost
        sub = {k: _normalize_score(raw.get(k, 5)) for k in _SUB_WEIGHTS}
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
        "_eval_cost": total_eval_cost,
        "_eval_cost_by_model": cost_by_model,
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
    expected_outcome: str | None = None,
    abort_event: threading.Event | None = None,
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
            expected_outcome,
            abort_event,
        )
    )
