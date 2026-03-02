"""Evaluation script for D10: tutoring quality + executable code checks."""

import json
import os
import re
import shlex


def evaluate(
    workspace_path: str,
    tool_logs: list = None,
    conversation: list = None,
    eval_context: dict = None,
) -> dict:
    """Evaluate tutoring quality plus whether runnable Python was executed."""
    assistant_text = _assistant_text(conversation)
    persona = _persona_from_context(eval_context) or _infer_persona(conversation)
    code_execution_attempted = _code_execution_attempted(tool_logs or [])
    code_runs_without_fatal_error = _code_runs_without_fatal_error(tool_logs or [])

    results = {
        "persona_level_inferred": bool(persona and persona != "unknown"),
        "level_adaptation_present": _level_adaptation_present(persona, assistant_text),
        "quant_concepts_covered": _historical_quant_concepts_covered(assistant_text),
        "code_execution_attempted": code_execution_attempted,
        "code_runs_without_fatal_error": code_runs_without_fatal_error,
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


def _assistant_text(conversation: list) -> str:
    if not conversation:
        return ""
    chunks = []
    for turn in conversation:
        if str(turn.get("role", "")).lower() == "assistant":
            chunks.append(str(turn.get("content", "")))
    return " ".join(chunks).lower()


def _first_user_message(conversation: list) -> str:
    if not conversation:
        return ""
    for turn in conversation:
        if str(turn.get("role", "")).lower() == "user":
            return str(turn.get("content", "")).lower()
    return ""


def _persona_from_context(eval_context: dict) -> str:
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


def _infer_persona(conversation: list) -> str:
    opening = _first_user_message(conversation)
    if not opening:
        return "unknown"

    if any(k in opening for k in ["new to", "beginner", "plain", "simple terms"]):
        return "beginner"
    if any(k in opening for k in ["ingestion script", "implementation", "only docs"]):
        return "intermediate"
    if any(
        k in opening
        for k in [
            "reproducible",
            "point-in-time",
            "revision leakage",
            "methodology",
        ]
    ):
        return "advanced"
    return "unknown"


def _level_adaptation_present(persona: str, text: str) -> bool:
    if not text:
        return False

    if persona == "beginner":
        strong_markers = [
            "in plain language",
            "step by step",
            "what this means",
            "let's break this down",
            "quick checklist",
        ]
        weak_markers = ["simple", "for example"]
        strong_count = sum(1 for m in strong_markers if m in text)
        weak_count = sum(1 for m in weak_markers if m in text)
        return strong_count >= 1 and (strong_count + weak_count) >= 2

    if persona == "intermediate":
        intermediate_markers = [
            "script",
            "pipeline",
            "schema",
            "validation checklist",
            "python",
            "pandas",
        ]
        return sum(1 for m in intermediate_markers if m in text) >= 2

    if persona == "advanced":
        advanced_markers = [
            "point-in-time",
            "as-of",
            "release lag",
            "revision",
            "corporate action",
            "leakage",
            "vintage",
        ]
        return sum(1 for m in advanced_markers if m in text) >= 3

    # Unknown persona: require multiple adaptation/risk signals.
    fallback_markers = [
        "step by step",
        "checklist",
        "point-in-time",
        "revision",
        "adjusted prices",
    ]
    return sum(1 for k in fallback_markers if k in text) >= 2


def _historical_quant_concepts_covered(text: str) -> bool:
    if not text:
        return False

    concept_buckets = [
        ["adjusted", "adj close", "split", "dividend", "corporate action"],
        ["revision", "release lag", "publication lag", "vintage"],
        ["point-in-time", "as-of", "as of", "look-ahead", "leakage"],
    ]
    covered = 0
    for bucket in concept_buckets:
        if any(k in text for k in bucket):
            covered += 1
    return covered >= 2


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

    for i, token in enumerate(tokens):
        base = os.path.basename(token)
        if _is_python_binary_token(base):
            return True
        if base == "env" and i + 1 < len(tokens):
            next_base = os.path.basename(tokens[i + 1])
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


if __name__ == "__main__":
    import sys

    workspace = sys.argv[1] if len(sys.argv) > 1 else "/workspace"
    print(json.dumps(evaluate(workspace), indent=2))
