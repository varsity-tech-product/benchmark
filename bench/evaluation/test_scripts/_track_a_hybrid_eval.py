"""Shared helpers for Track A optional-tool hybrid eval scripts.

These utilities keep D02-D11 aligned with the same scoring philosophy:
- persona adaptation checks
- quant concept coverage checks
- runnable Python execution evidence
"""

import os
import re
import shlex


def evaluate_track_a_hybrid(
    workspace_path: str,
    tool_logs: list | None,
    conversation: list | None,
    eval_context: dict | None,
    *,
    persona_rules: dict,
    adaptation_markers: dict,
    concept_buckets: list[list[str]],
    concept_min_covered: int = 2,
) -> dict:
    """Evaluate a Track A data task with tutoring + execution checks."""
    assistant_text = _assistant_text(conversation)
    persona = _persona_from_context(eval_context) or _infer_persona(
        conversation, persona_rules
    )
    # Keep concept coverage aligned with D10/D11 baseline behavior:
    # score from tutor teaching text rather than artifact keyword spillover.
    concept_text = assistant_text

    results = {
        "persona_level_inferred": bool(persona and persona != "unknown"),
        "level_adaptation_present": _level_adaptation_present(
            persona,
            assistant_text,
            adaptation_markers,
        ),
        "quant_concepts_covered": _concepts_covered(
            concept_text,
            concept_buckets,
            min_covered=concept_min_covered,
        ),
        "code_execution_attempted": _code_execution_attempted(tool_logs or []),
        "code_runs_without_fatal_error": _code_runs_without_fatal_error(
            tool_logs or []
        ),
        "score": 0.0,
    }

    score = sum(
        [
            0.15 if results["persona_level_inferred"] else 0.0,
            0.30 if results["level_adaptation_present"] else 0.0,
            0.25 if results["quant_concepts_covered"] else 0.0,
            0.15 if results["code_execution_attempted"] else 0.0,
            0.15 if results["code_runs_without_fatal_error"] else 0.0,
        ]
    )
    results["score"] = round(score, 2)
    return results


def _assistant_text(conversation: list | None) -> str:
    if not conversation:
        return ""
    chunks = []
    for turn in conversation:
        if str(turn.get("role", "")).lower() == "assistant":
            chunks.append(str(turn.get("content", "")))
    return " ".join(chunks).lower()


def _first_user_message(conversation: list | None) -> str:
    if not conversation:
        return ""
    for turn in conversation:
        if str(turn.get("role", "")).lower() == "user":
            return str(turn.get("content", "")).lower()
    return ""


def _persona_from_context(eval_context: dict | None) -> str:
    if not eval_context:
        return ""
    level = str(eval_context.get("persona_level", "")).strip().lower()
    if level in {"beginner", "intermediate", "advanced"}:
        return level

    persona_id = str(eval_context.get("persona_id", "")).strip().lower()
    if persona_id.startswith("beginner"):
        return "beginner"
    if persona_id.startswith("intermediate"):
        return "intermediate"
    if persona_id.startswith("advanced"):
        return "advanced"
    return ""


def _infer_persona(conversation: list | None, persona_rules: dict) -> str:
    opening = _first_user_message(conversation)
    if not opening:
        return "unknown"

    beginner_patterns = persona_rules.get("beginner", [])
    intermediate_patterns = persona_rules.get("intermediate", [])
    advanced_patterns = persona_rules.get("advanced", [])

    if any(k in opening for k in beginner_patterns):
        return "beginner"
    if any(k in opening for k in intermediate_patterns):
        return "intermediate"
    if any(k in opening for k in advanced_patterns):
        return "advanced"
    return "unknown"


def _level_adaptation_present(
    persona: str, text: str, adaptation_markers: dict
) -> bool:
    if not text:
        return False

    if persona == "beginner":
        strong = adaptation_markers.get("beginner_strong", [])
        weak = adaptation_markers.get("beginner_weak", [])
        strong_min = int(adaptation_markers.get("beginner_min_strong", 1))
        total_min = int(adaptation_markers.get("beginner_min_total", 2))

        strong_count = sum(1 for marker in strong if marker in text)
        weak_count = sum(1 for marker in weak if marker in text)
        return strong_count >= strong_min and (strong_count + weak_count) >= total_min

    if persona == "intermediate":
        markers = adaptation_markers.get("intermediate_markers", [])
        threshold = int(adaptation_markers.get("intermediate_min", 2))
        return sum(1 for marker in markers if marker in text) >= threshold

    if persona == "advanced":
        markers = adaptation_markers.get("advanced_markers", [])
        threshold = int(adaptation_markers.get("advanced_min", 3))
        return sum(1 for marker in markers if marker in text) >= threshold

    markers = adaptation_markers.get("fallback_markers", [])
    threshold = int(adaptation_markers.get("fallback_min", 2))
    return sum(1 for marker in markers if marker in text) >= threshold


def _concepts_covered(
    text: str, concept_buckets: list[list[str]], min_covered: int
) -> bool:
    if not text:
        return False

    covered = 0
    for bucket in concept_buckets:
        if any(keyword in text for keyword in bucket):
            covered += 1
    return covered >= min_covered


def _log_value(log: dict, key: str, default=None):
    if isinstance(log, dict):
        return log.get(key, default)
    return getattr(log, key, default)


def _python_shell_logs(tool_logs: list) -> list:
    selected = []
    for log in tool_logs:
        if str(_log_value(log, "name", "")).strip() != "shell_exec":
            continue

        args = _log_value(log, "input_args", None)
        if not isinstance(args, dict):
            args = _log_value(log, "args", {})
        if not isinstance(args, dict):
            args = {}

        command = str((args or {}).get("command", "")).strip()
        if _looks_like_python_invocation(command):
            selected.append(log)

    return selected


def _is_python_binary_token(token: str) -> bool:
    return bool(re.fullmatch(r"python(?:\d+(?:\.\d+)?)?", token))


def _looks_like_python_invocation(command: str) -> bool:
    if not command:
        return False

    lowered = command.strip().lower()
    try:
        tokens = shlex.split(lowered)
    except ValueError:
        tokens = lowered.split()

    for index, token in enumerate(tokens):
        base = os.path.basename(token)
        if _is_python_binary_token(base):
            return True
        if base == "env" and index + 1 < len(tokens):
            next_base = os.path.basename(tokens[index + 1])
            if _is_python_binary_token(next_base):
                return True

    return bool(
        re.search(
            r"(^|[\"'`;|&()\s])(?:[^\s\"']*/)?python(?:\d+(?:\.\d+)?)?(?=\s|$)",
            lowered,
        )
    )


def _code_execution_attempted(tool_logs: list) -> bool:
    return len(_python_shell_logs(tool_logs)) > 0


def _extract_exit_code(output: str):
    match = re.search(r"\[exit code\]:\s*(-?\d+)", output, re.IGNORECASE)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _code_runs_without_fatal_error(tool_logs: list) -> bool:
    fatal_markers = [
        "traceback (most recent call last)",
        "syntaxerror",
        "modulenotfounderror",
        "importerror",
        "nameerror",
        "typeerror",
        "valueerror",
        "attributeerror",
        "keyerror",
        "filenotfounderror",
        "permissionerror",
        "command not found",
        "error: command timed out",
    ]

    for log in _python_shell_logs(tool_logs):
        output = str(_log_value(log, "result", "") or "")
        if not output.strip():
            continue

        exit_code = _extract_exit_code(output)
        if exit_code is not None and exit_code != 0:
            continue

        output_lower = output.lower()
        if any(marker in output_lower for marker in fatal_markers):
            continue

        return True

    return False
