"""Custom conversational metrics: RoleAdherence + TopicAdherence.

Replaces DeepEval's built-in RoleAdherenceMetric and TopicAdherenceMetric
with direct GPTModel calls using prompts designed for tool-use tutoring agents.

Independent module — does NOT import from process_metrics.py.
Uses GPTModel + _call_llm pattern from process_reasonableness.py.

Test with: cd bench && python -m evaluation.test_custom_conv_metrics
"""

import json as _json
import re

from config.model_resolver import resolve_deepeval_model

try:
    from deepeval.models.llms.openai_model import GPTModel

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


# ──────────────────────────────────────────────────────────────
# Shared helpers (same patterns as process_reasonableness.py)
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


async def _call_llm(model, prompt: str) -> dict:
    """Call LLM via GPTModel and parse JSON response."""
    model_obj = resolve_deepeval_model(model)
    if isinstance(model_obj, str):
        model_obj = GPTModel(model=model_obj)
    response_text, call_cost = await model_obj.a_generate(prompt)
    result = _extract_json_from_response(response_text)
    result["_eval_cost"] = float(call_cost) if call_cost else 0.0
    return result


def _build_turns_text(turns: list[dict], max_chars: int = 12000) -> str:
    """Build conversation text from turns, truncating if needed.

    Args:
        turns: List of {"role": "user"/"assistant", "content": "..."}.
        max_chars: Max total characters. If exceeded, keep first + last
            turns and omit middle with a marker.
    """
    lines = []
    for i, t in enumerate(turns):
        role_label = "Student" if t["role"] == "user" else "Tutor"
        lines.append(f"[Turn {i + 1} — {role_label}]\n{t['content']}")
    full = "\n\n".join(lines)
    if len(full) <= max_chars:
        return full
    # Truncate: keep first 3 + last 3 turns, omit middle
    keep_n = 3
    if len(turns) <= keep_n * 2:
        return full[:max_chars] + "\n\n[... truncated ...]"
    head = turns[:keep_n]
    tail = turns[-keep_n:]
    omitted = len(turns) - keep_n * 2
    head_lines = []
    for i, t in enumerate(head):
        role_label = "Student" if t["role"] == "user" else "Tutor"
        head_lines.append(f"[Turn {i + 1} — {role_label}]\n{t['content']}")
    tail_lines = []
    for j, t in enumerate(tail):
        idx = len(turns) - keep_n + j + 1
        role_label = "Student" if t["role"] == "user" else "Tutor"
        tail_lines.append(f"[Turn {idx} — {role_label}]\n{t['content']}")
    mid = f"\n\n[... {omitted} turns omitted for brevity ...]\n\n"
    result = "\n\n".join(head_lines) + mid + "\n\n".join(tail_lines)
    # Final safety trim
    if len(result) > max_chars:
        result = result[:max_chars] + "\n[... truncated ...]"
    return result


# ──────────────────────────────────────────────────────────────
# Role Adherence — custom evaluator
# ──────────────────────────────────────────────────────────────

_ROLE_ADHERENCE_PROMPT = """\
You are evaluating ROLE ADHERENCE of a quantitative finance tutor agent.

ROLE DEFINITION: The agent is a "quantitative finance tutor" who teaches
through a combination of:
- Explaining financial/technical concepts in student-appropriate language
- Using tools (shell_exec, fetch_market_data, file_read, etc.) to prepare
  real data and demonstrate concepts concretely
- Writing and executing Python code to show analysis steps
- Providing step-by-step guidance, answering questions, scaffolding learning

CRITICAL RULE: Tool usage and code execution are CORE tutoring activities,
NOT deviations from role. An agent that fetches data, runs analysis, and
explains the results IS fulfilling the tutor role. Only flag deviations
where the agent clearly abandons the teaching context (e.g., becomes a
generic task-completion assistant with zero educational framing, refuses
to teach, or takes on a completely unrelated role).

SCORING SCALE: Use ONLY these values: {{0.0, 0.25, 0.5, 0.75, 1.0}}.

Evaluate these 2 sub-dimensions:

1. PERSONA_CONSISTENCY (0.0-1.0):
   Does the agent maintain a consistent tutor persona throughout the conversation?
   - 1.0: Every substantive response includes educational framing (explains what,
          why, how) even when executing code or using tools
   - 0.75: Mostly tutoring; occasional responses are pure task execution without
           educational context, but the overall tone is pedagogical
   - 0.5: Mixed — sometimes teaches, sometimes just silently executes without
          any explanation or educational framing
   - 0.25: Mostly silent execution with rare teaching moments
   - 0.0: No evidence of tutoring behavior; purely mechanical task execution

2. BOUNDARY_MAINTENANCE (0.0-1.0):
   Does the agent stay within the tutor role boundaries?
   - 1.0: Consistently acts as educator; all content serves learning objectives
   - 0.75: Good boundaries; minor forays into non-educational territory
   - 0.5: Acceptable; some content doesn't serve educational purpose
   - 0.25: Frequently steps outside tutor role
   - 0.0: Completely abandons tutor role

CONVERSATION:
{turns_text}

Return a valid JSON object with exactly these keys:
{{"persona_consistency": <float>, "boundary_maintenance": <float>, "reason": "<1-3 sentences>"}}

JSON:"""


async def eval_role_adherence(
    turns: list[dict],
    model: str,
    threshold: float = 0.5,
) -> dict:
    """Evaluate role adherence using direct GPTModel call.

    Args:
        turns: Conversation as list of {"role": "user"/"assistant", "content": "..."}.
        model: Eval model name (OpenRouter format).
        threshold: Pass/fail threshold.

    Returns:
        Dict with score, reason, sub_scores, passed, _eval_cost.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    turns_text = _build_turns_text(turns)
    prompt = _ROLE_ADHERENCE_PROMPT.format(turns_text=turns_text)

    result = await _call_llm(model, prompt)

    persona = _clamp_ordinal(result.get("persona_consistency", 0.5))
    boundary = _clamp_ordinal(result.get("boundary_maintenance", 0.5))
    score = round(0.5 * persona + 0.5 * boundary, 4)

    return {
        "score": score,
        "reason": result.get("reason", ""),
        "passed": score >= threshold,
        "sub_scores": {
            "persona_consistency": persona,
            "boundary_maintenance": boundary,
        },
        "_eval_cost": result.get("_eval_cost", 0.0),
    }


# ──────────────────────────────────────────────────────────────
# Topic Adherence — custom evaluator
# ──────────────────────────────────────────────────────────────

_TOPIC_ADHERENCE_PROMPT = """\
You are evaluating TOPIC ADHERENCE of a quantitative finance tutor agent.

TASK CONTEXT: {task_description}

RELEVANT TOPIC DOMAINS:
{topics_list}

CRITICAL CONTEXT FOR TOOL-USE AGENTS:
- Tool outputs (data tables, code execution results, numerical output, error
  messages) are ON-TOPIC when they serve the task. Raw output from shell_exec
  showing pandas DataFrames, statistics, or financial data is analytical work,
  NOT off-topic content.
- Python code written for financial data analysis, visualization, or strategy
  implementation is ON-TOPIC.
- Brief environment/metadata responses (e.g., listing available files, checking
  Python version) are neutral setup actions, not off-topic.
- Only flag content that is genuinely UNRELATED to the task or listed topic
  domains (e.g., discussing cooking recipes, unrelated personal topics).

SCORING SCALE: Use ONLY these values: {{0.0, 0.25, 0.5, 0.75, 1.0}}.

Evaluate these 2 sub-dimensions:

1. TOPIC_RELEVANCE (0.0-1.0):
   How well does the conversation content align with the task and topic domains?
   - 1.0: All substantive content directly serves the task within listed domains
   - 0.75: Mostly on-topic with minor tangents that don't derail the session
   - 0.5: Mixed — significant portions are relevant but notable off-topic content
   - 0.25: Mostly off-topic with some relevant content
   - 0.0: Entirely off-topic

2. TASK_FOCUS (0.0-1.0):
   Does the agent stay focused on the specific task objectives?
   - 1.0: Agent consistently works toward the stated task goals
   - 0.75: Mostly focused; minor diversions that still relate to quant finance
   - 0.5: Partially focused; some work doesn't contribute to task completion
   - 0.25: Poorly focused; substantial effort wasted on unrelated directions
   - 0.0: No focus on the stated task

CONVERSATION:
{turns_text}

Return a valid JSON object with exactly these keys:
{{"topic_relevance": <float>, "task_focus": <float>, "reason": "<1-3 sentences>"}}

JSON:"""


async def eval_topic_adherence(
    turns: list[dict],
    model: str,
    task_description: str = "",
    relevant_topics: list[str] | None = None,
    threshold: float = 0.5,
) -> dict:
    """Evaluate topic adherence using direct GPTModel call.

    Args:
        turns: Conversation as list of {"role": "user"/"assistant", "content": "..."}.
        model: Eval model name (OpenRouter format).
        task_description: The task description for context.
        relevant_topics: List of relevant topic strings.
        threshold: Pass/fail threshold.

    Returns:
        Dict with score, reason, sub_scores, passed, _eval_cost.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "passed": True}

    from evaluation.deepeval_metrics.process_metrics import QUANT_TUTOR_TOPICS

    topics = relevant_topics or QUANT_TUTOR_TOPICS
    topics_list = "\n".join(f"  - {t}" for t in topics)
    turns_text = _build_turns_text(turns)

    prompt = _TOPIC_ADHERENCE_PROMPT.format(
        task_description=task_description or "(not provided)",
        topics_list=topics_list,
        turns_text=turns_text,
    )

    result = await _call_llm(model, prompt)

    relevance = _clamp_ordinal(result.get("topic_relevance", 0.5))
    focus = _clamp_ordinal(result.get("task_focus", 0.5))
    score = round(0.6 * relevance + 0.4 * focus, 4)

    return {
        "score": score,
        "reason": result.get("reason", ""),
        "passed": score >= threshold,
        "sub_scores": {
            "topic_relevance": relevance,
            "task_focus": focus,
        },
        "_eval_cost": result.get("_eval_cost", 0.0),
    }
