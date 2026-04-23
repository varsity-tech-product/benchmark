"""Code Lifecycle — programmatic code development process evaluation.

Evaluates the agent's code development PROCESS using 4 deterministic metrics.
LLM-judged dimensions (debugging_competence, incremental_development,
code_explanation_quality) have been removed — debugging is covered by
problem_solving (QP LLM), and the others by Tutor D2 and code_lifecycle itself.

Metrics:
    Iterative Refinement  — write→test→fix cycle adherence
    Test Before Deliver   — verified code works before responding
    Error Recovery        — recovered from execution failures
    Code Evolution        — substantive changes across rewrites

Combined score: average of applicable metrics.
Returns score=None when no code activity → excluded from QP aggregate.
"""

import os
import re
from typing import Optional

# ──────────────────────────────────────────────────────────────
# Code execution detection (Python + C#)
# ──────────────────────────────────────────────────────────────

_CODE_EXTENSIONS = (".py", ".cs")

_PYTHON_CMD_RE = re.compile(r"python3?\s+" r"(?:-c\s+|" r"-?\s*<<|" r"[\w./\\-]+\.py)")

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
    match = re.search(r"([\w./\\-]+\.py)", cmd)
    if match:
        return os.path.basename(match.group(1))
    match = re.search(r"([\w./\\-]+\.cs)", cmd)
    if match:
        return os.path.basename(match.group(1))
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
    if "build failed" in result:
        return False
    if "error cs" in result:
        return False
    if log.success and "error" not in result:
        return True
    return log.success


# ──────────────────────────────────────────────────────────────
# Programmatic metrics
# ──────────────────────────────────────────────────────────────


def _metric_iterative_refinement(logs: list) -> Optional[float]:
    """Write→test→fix cycle adherence.

    For each code file written via file_write, checks whether the agent
    executes it at least once after writing.
    Returns None if no code files are written.
    """
    written_scripts: set[str] = set()
    tested_scripts: set[str] = set()
    write_indices: dict[str, int] = {}
    exec_events: list[tuple[int, str]] = []

    for i, log in enumerate(logs):
        if log.name == "file_write":
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

    for script in written_scripts:
        first_write = write_indices.get(script, 0)
        for exec_idx, exec_script in exec_events:
            if (
                exec_script == script or script in exec_script
            ) and exec_idx > first_write:
                tested_scripts.add(script)
                break

    return len(tested_scripts) / len(written_scripts)


def _metric_test_before_deliver(logs: list) -> Optional[float]:
    """Did the agent verify its code works before the final response?

    Score 1.0 if last code execution succeeded, 0.0 if it failed.
    Returns None if no code execution occurred.
    """
    for i in range(len(logs) - 1, -1, -1):
        if _is_code_exec(logs[i]):
            return 1.0 if _is_exec_successful(logs[i]) else 0.0
    return None


def _metric_error_recovery(logs: list) -> Optional[float]:
    """Recovery rate from execution failures.

    Score = recovered_scripts / scripts_with_failures.
    Returns 1.0 if no failures occurred.
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
                if any(history[j + 1 :]):
                    recovered += 1
                    break
        if has_failure:
            scripts_with_failures += 1

    if scripts_with_failures == 0:
        return 1.0

    return recovered / scripts_with_failures


def _metric_code_evolution(logs: list) -> Optional[float]:
    """Substantive changes across file rewrites.

    For files written multiple times, checks whether content changes
    meaningfully (>5% line-level difference) between versions.
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


# ──────────────────────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────────────────────


def evaluate_code_lifecycle(logs: list) -> dict:
    """Evaluate code lifecycle (4 programmatic metrics).

    Returns dict with per-metric scores, combined score, and applicable flag.
    score=None when no code activity → excluded from QP aggregate.
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
            "score": None,
            "reason": "no code activity detected",
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


# Backward-compatible alias
evaluate_code_process_programmatic = evaluate_code_lifecycle
