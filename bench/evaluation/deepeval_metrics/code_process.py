"""Code Process Quality — programmatic + LLM-judged code development evaluation.

Phase 5: Evaluates the agent's code development PROCESS, complementing
code_eval.py (which evaluates code QUALITY/RESULTS).

Programmatic metrics (50%):
    Iterative Refinement  (0.25) — write→test→fix cycle adherence
    Test Before Deliver   (0.25) — verified code works before responding
    Error Recovery        (0.25) — recovered from execution failures
    Code Evolution        (0.25) — substantive changes across rewrites

LLM-judged metrics (50%):
    Debugging Competence     (1/3) — root cause diagnosis, fix quality
    Incremental Development  (1/3) — progressive building vs big-bang
    Code Explanation Quality (1/3) — explains code to student

Uses 5-point ordinal scale: {0.0, 0.25, 0.5, 0.75, 1.0} for LLM dimensions.
"""

import os
import re
from typing import Optional

from config.model_resolver import resolve_deepeval_model

from evaluation.deepeval_metrics._scoring_utils import extract_json_from_response

try:
    from deepeval.models.llms.openai_model import GPTModel

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False


# ──────────────────────────────────────────────────────────────
# Code execution detection (Python + C#)
# ──────────────────────────────────────────────────────────────

_CODE_EXTENSIONS = (".py", ".cs")

_PYTHON_CMD_RE = re.compile(r"python3?\s+" r"(?:-c\s+|" r"-\s*<<|" r"[\w./\\-]+\.py)")

_CSHARP_CMD_RE = re.compile(r"(?:dotnet\s+(?:build|run|test)|run_backtest|msbuild)")


def _is_python_exec(log) -> bool:
    """Check if a shell_exec log runs Python code."""
    if log.name != "shell_exec":
        return False
    cmd = log.args.get("command", "")
    return bool(_PYTHON_CMD_RE.search(cmd))


def _is_csharp_exec(log) -> bool:
    """Check if a shell_exec log runs C# code (dotnet build/run, run_backtest)."""
    if log.name != "shell_exec":
        return False
    cmd = log.args.get("command", "")
    return bool(_CSHARP_CMD_RE.search(cmd))


def _is_code_exec(log) -> bool:
    """Check if a shell_exec log runs any code (Python or C#)."""
    return _is_python_exec(log) or _is_csharp_exec(log)


def _extract_script_name(log) -> str:
    """Extract script/file name from a code execution command."""
    cmd = log.args.get("command", "")
    # Try Python script
    match = re.search(r"([\w./\\-]+\.py)", cmd)
    if match:
        return os.path.basename(match.group(1))
    # Try C# file
    match = re.search(r"([\w./\\-]+\.cs)", cmd)
    if match:
        return os.path.basename(match.group(1))
    # C# commands without explicit file reference
    if _is_csharp_exec(log):
        return "dotnet"
    return "inline"


def _is_exec_successful(log) -> bool:
    """Check if a code execution succeeded (no traceback, no error)."""
    result = (log.result or "").lower()
    if "timed out" in result or "timeout" in result:
        return False
    if "syntaxerror" in result:
        return False
    if "traceback (most recent call last)" in result:
        return False
    # C# build failures
    if "build failed" in result:
        return False
    if "error cs" in result:
        return False
    if log.success and "error" not in result:
        return True
    return log.success


# ──────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────


def _clamp_ordinal(val, default=0.5) -> float:
    """Snap value to nearest 5-point ordinal."""
    try:
        v = float(val)
    except (TypeError, ValueError):
        return default
    ordinals = [0.0, 0.25, 0.5, 0.75, 1.0]
    return min(ordinals, key=lambda x: abs(x - v))


# ──────────────────────────────────────────────────────────────
# Programmatic metrics
# ──────────────────────────────────────────────────────────────


def _metric_iterative_refinement(logs: list) -> Optional[float]:
    """Write→test→fix cycle adherence.

    For each code file (.py/.cs) written via file_write, checks whether
    the agent executes it at least once after writing.  Agents that test
    every file score 1.0; agents that write and forget score 0.0.

    Returns None if no code files are written (metric not applicable).
    """
    written_scripts: set[str] = set()
    tested_scripts: set[str] = set()

    # Build timeline: write events and exec events
    write_indices: dict[str, int] = {}  # script → earliest write index
    exec_events: list[tuple[int, str]] = []  # (index, script_name)

    for i, log in enumerate(logs):
        name = log.name
        if name == "file_write":
            path = log.args.get("path", "")
            if any(path.endswith(ext) for ext in _CODE_EXTENSIONS):
                script = os.path.basename(path)
                written_scripts.add(script)
                if script not in write_indices:
                    write_indices[script] = i
        elif _is_code_exec(log):
            exec_events.append((i, _extract_script_name(log)))

    if not written_scripts:
        return None

    # For each written script, check if executed after any write
    for script in written_scripts:
        first_write = write_indices.get(script, 0)
        for exec_idx, exec_script in exec_events:
            # Match by exact name or by containment (e.g. "./backtest.py" contains "backtest.py")
            if (
                exec_script == script or script in exec_script
            ) and exec_idx > first_write:
                tested_scripts.add(script)
                break

    return len(tested_scripts) / len(written_scripts)


def _metric_test_before_deliver(logs: list) -> Optional[float]:
    """Did the agent verify its code works before the final response?

    Finds the last code execution (Python or C#) before the final tool call.
    Score 1.0 if it succeeded, 0.0 if it failed.
    Returns None if no code execution occurred.
    """
    boundary = len(logs)

    # Find last code exec before boundary
    for i in range(boundary - 1, -1, -1):
        if _is_code_exec(logs[i]):
            return 1.0 if _is_exec_successful(logs[i]) else 0.0

    return None


def _metric_error_recovery(logs: list) -> Optional[float]:
    """Recovery rate from execution failures.

    For each script that fails at least once, checks whether a later
    execution of that script succeeds.
    Score = recovered_scripts / scripts_with_failures.
    Returns 1.0 if no failures occurred (vacuously true).
    Returns None if no code execution occurred.
    """
    script_history: dict[str, list[bool]] = {}

    for log in logs:
        if not _is_code_exec(log):
            continue
        script = _extract_script_name(log)
        success = _is_exec_successful(log)
        script_history.setdefault(script, []).append(success)

    if not script_history:
        return None

    scripts_with_failures = 0
    recovered = 0

    for script, history in script_history.items():
        has_failure = False
        for j, success in enumerate(history):
            if not success:
                has_failure = True
                # Check if any later exec of same script succeeds
                if any(history[j + 1 :]):
                    recovered += 1
                    break  # Count recovery once per script
        if has_failure:
            scripts_with_failures += 1

    if scripts_with_failures == 0:
        return 1.0  # No failures → perfect recovery

    return recovered / scripts_with_failures


def _metric_code_evolution(logs: list) -> Optional[float]:
    """Substantive changes across file rewrites.

    For files written multiple times, checks whether content changes
    meaningfully (>5% line-level difference) between versions.
    Files written once score 1.0 (no evolution needed).

    Returns None if no code files are written.
    """
    writes_by_file: dict[str, list[str]] = {}

    for log in logs:
        if log.name != "file_write":
            continue
        path = log.args.get("path", "")
        content = log.args.get("content", "")
        if any(path.endswith(ext) for ext in _CODE_EXTENSIONS) and content:
            fname = os.path.basename(path)
            writes_by_file.setdefault(fname, []).append(content)

    if not writes_by_file:
        return None

    scores: list[float] = []
    for fname, versions in writes_by_file.items():
        if len(versions) <= 1:
            scores.append(1.0)
            continue

        # Check each rewrite for substantive changes
        substantive = 0
        for i in range(1, len(versions)):
            prev_lines = set(versions[i - 1].strip().splitlines())
            curr_lines = set(versions[i].strip().splitlines())
            changed = len(prev_lines.symmetric_difference(curr_lines))
            total = max(len(prev_lines), len(curr_lines), 1)
            if changed / total > 0.05:
                substantive += 1

        scores.append(substantive / (len(versions) - 1))

    return sum(scores) / len(scores) if scores else 0.5


def evaluate_code_process_programmatic(logs: list) -> dict:
    """Run all 4 programmatic code process metrics.

    Returns dict with per-metric scores and combined programmatic score.
    Metrics not applicable (no code) return None and are excluded from average.
    """
    iterative = _metric_iterative_refinement(logs)
    test_deliver = _metric_test_before_deliver(logs)
    recovery = _metric_error_recovery(logs)
    evolution = _metric_code_evolution(logs)

    sub_scores = {
        "iterative_refinement": iterative,
        "test_before_deliver": test_deliver,
        "error_recovery": recovery,
        "code_evolution": evolution,
    }

    applicable = [v for v in sub_scores.values() if v is not None]

    if not applicable:
        return {
            "applicable": False,
            "score": 0.0,
            "sub_scores": sub_scores,
        }

    combined = sum(applicable) / len(applicable)

    return {
        "applicable": True,
        "score": round(combined, 4),
        "sub_scores": {
            k: round(v, 4) if v is not None else None for k, v in sub_scores.items()
        },
    }


# ──────────────────────────────────────────────────────────────
# LLM-judged code process
# ──────────────────────────────────────────────────────────────


def _build_code_activity_trace(logs: list) -> str:
    """Build a trace showing WRITE/EXEC activity with brief summaries."""
    lines = []
    step = 0

    for log in logs:
        name = log.name

        if name == "file_write":
            path = log.args.get("path", "")
            if not any(path.endswith(ext) for ext in _CODE_EXTENSIONS):
                continue
            content = log.args.get("content", "")
            step += 1
            line_count = len(content.strip().splitlines()) if content else 0
            # Brief preview: first few significant code lines
            # Skip comments for both Python (#) and C# (//)
            code_lines = [
                ln.strip()
                for ln in content.splitlines()
                if ln.strip()
                and not ln.strip().startswith("#")
                and not ln.strip().startswith("//")
            ]
            preview = "; ".join(code_lines[:3])
            if len(preview) > 150:
                preview = preview[:150] + "..."
            lines.append(
                f"  {step}. WRITE: {os.path.basename(path)} "
                f"({line_count} lines) — {preview}"
            )

        elif _is_code_exec(log):
            step += 1
            script = _extract_script_name(log)
            result = log.result or ""
            success = _is_exec_successful(log)
            status = "OK" if success else "FAIL"
            result_preview = (
                result[:200].replace("\n", " ") if result else "(no output)"
            )
            lines.append(f"  {step}. EXEC: {script} → [{status}] {result_preview}")

    return "\n".join(lines) if lines else "  (no code activity)"


def _build_code_process_llm_prompt(
    task: str,
    activity_trace: str,
    actual_output: str,
) -> str:
    """Build the LLM prompt for code process evaluation."""
    output_preview = actual_output[:2000] if actual_output else "(no output)"

    return f"""You are evaluating the CODE DEVELOPMENT PROCESS of an AI tutoring agent.

SCORING SCALE: Use ONLY these values: {{0.0, 0.25, 0.5, 0.75, 1.0}}.
When in doubt between two levels, select the LOWER score.

TASK: {task}

CODE ACTIVITY TRACE (write/exec events only):
{activity_trace}

AGENT CONVERSATION (excerpt):
{output_preview}

{'=' * 55}
EVALUATE on 3 dimensions:

1. DEBUGGING COMPETENCE (0.0-1.0):
   When code fails, does the agent correctly diagnose the root cause?
   Does it make targeted fixes rather than blind trial-and-error?
   - 1.0:  Excellent diagnosis — identifies exact issue, makes precise fix
   - 0.75: Good diagnosis — fixes the right problem, minor extra attempts
   - 0.5:  Partial diagnosis — some correct fixes mixed with guesswork
   - 0.25: Poor diagnosis — mostly trial-and-error, multiple failed attempts
   - 0.0:  No debugging ability — doesn't fix errors or makes them worse
   If no errors occurred, score based on code quality that prevented errors.

2. INCREMENTAL DEVELOPMENT (0.0-1.0):
   Does the agent build progressively (small steps, testing each)?
   Or does it write a large block of code all at once?
   - 1.0:  Excellent progressive development — builds in small, tested increments
   - 0.75: Mostly incremental — tests at key points
   - 0.5:  Mixed approach — some testing but also large untested blocks
   - 0.25: Mostly big-bang — writes large code blocks without intermediate testing
   - 0.0:  Pure big-bang — dumps all code at once, tests only at the end

3. CODE EXPLANATION QUALITY (0.0-1.0):
   Does the agent explain its code to the student? Does it describe
   what the code does, why design choices were made, and what results mean?
   - 1.0:  Excellent explanations — code logic, design choices, and results interpreted
   - 0.75: Good explanations for most code sections
   - 0.5:  Some explanation but gaps in key areas
   - 0.25: Minimal explanation — mostly just code without context
   - 0.0:  No explanation — pure code dump

Return ONLY a JSON object (no markdown, no extra text):
{{"debugging_competence": <float>, "incremental_development": <float>, "code_explanation_quality": <float>, "reason": "<brief explanation>"}}"""


_LLM_SUB_KEYS = (
    "debugging_competence",
    "incremental_development",
    "code_explanation_quality",
)


async def evaluate_code_process_llm(
    task_description: str,
    logs: list,
    actual_output: str,
    model=None,
) -> dict:
    """LLM-judged code process evaluation.

    Returns dict with per-dimension scores and combined LLM score.
    """
    if not DEEPEVAL_AVAILABLE:
        return {"score": 0.5, "reason": "deepeval not available", "applicable": True}

    activity_trace = _build_code_activity_trace(logs)

    if activity_trace.strip() == "(no code activity)":
        return {"applicable": False, "score": None}

    prompt = _build_code_process_llm_prompt(
        task=task_description,
        activity_trace=activity_trace,
        actual_output=actual_output,
    )

    model_obj = resolve_deepeval_model(model)
    if isinstance(model_obj, str):
        from config.pricing import get_deepeval_cost_kwargs

        model_obj = GPTModel(model=model_obj, **get_deepeval_cost_kwargs(model_obj))
    response_text, call_cost = await model_obj.a_generate(prompt)
    result = extract_json_from_response(response_text)

    sub_scores = {k: _clamp_ordinal(result.get(k, 0.5)) for k in _LLM_SUB_KEYS}
    overall = sum(sub_scores.values()) / len(sub_scores)

    return {
        "applicable": True,
        "score": round(overall, 4),
        "reason": result.get("reason", ""),
        "sub_scores": sub_scores,
        "_eval_cost": float(call_cost) if call_cost else 0.0,
    }


# ──────────────────────────────────────────────────────────────
# Combined evaluation
# ──────────────────────────────────────────────────────────────


async def async_eval_code_process(
    task_description: str,
    proxy_logs: list,
    actual_output: str,
    model=None,
) -> dict:
    """Combined code process evaluation (50% programmatic + 50% LLM-judged).

    Skipped entirely if no code activity (file_write of .py/.cs or
    code shell_exec) is detected — returns score=None so it's excluded
    from QP aggregate.

    Args:
        task_description: Task description text.
        proxy_logs: Tool call logs (ToolCallLog objects or dicts).
        actual_output: Combined agent text output.
        model: LLM judge model.

    Returns:
        Dict with score, sub-results, and applicable flag.
    """
    programmatic = evaluate_code_process_programmatic(proxy_logs)

    if not programmatic["applicable"]:
        return {
            "applicable": False,
            "score": None,
            "reason": "no code activity detected",
            "programmatic": programmatic,
            "llm_judged": None,
        }

    llm_result = await evaluate_code_process_llm(
        task_description=task_description,
        logs=proxy_logs,
        actual_output=actual_output,
        model=model,
    )

    if llm_result.get("applicable", True) and llm_result.get("score") is not None:
        combined = 0.50 * programmatic["score"] + 0.50 * llm_result["score"]
    else:
        # LLM part not applicable — use programmatic only
        combined = programmatic["score"]

    return {
        "applicable": True,
        "score": round(combined, 4),
        "passed": combined >= 0.5,
        "programmatic": programmatic,
        "llm_judged": llm_result,
        "_eval_cost": llm_result.get("_eval_cost", 0.0),
    }
