"""Shared helpers for X-series debug eval scripts (X02-X10).

Provides reusable functions for checking bug fixes, execution output,
conversation concepts, root cause explanations, and fix verification.
"""

import os
import re


def check_fix_applied(
    workspace_path: str,
    tool_logs: list,
    fix_patterns: list[str],
    bug_patterns: list[str],
    file_extension: str = ".py",
) -> dict:
    """Scan workspace files + tool_log args/results for regex patterns.

    Args:
        workspace_path: Path to the agent's workspace directory.
        tool_logs: List of ToolCallLog objects.
        fix_patterns: Regex patterns that indicate the fix was applied.
        bug_patterns: Regex patterns that indicate the bug is still present.
        file_extension: File extension to scan (default ".py").

    Returns:
        {"fixed": bool, "fix_found_in": str, "bug_still_present": bool}
    """
    result = {
        "fixed": False,
        "fix_found_in": "",
        "bug_still_present": False,
    }

    # Collect all text from workspace files
    workspace_texts = []
    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if fname.endswith(file_extension):
                    fpath = os.path.join(root, fname)
                    try:
                        with open(fpath) as f:
                            text = f.read()
                        workspace_texts.append(("workspace:" + fname, text))
                    except (IOError, UnicodeDecodeError):
                        continue

    # Also check student_code directory (in-place edits)
    student_dir = (
        os.path.join(workspace_path, "..", "student_code") if workspace_path else ""
    )
    if os.path.isdir(student_dir):
        for fname in os.listdir(student_dir):
            if fname.endswith(file_extension):
                fpath = os.path.join(student_dir, fname)
                try:
                    with open(fpath) as f:
                        text = f.read()
                    workspace_texts.append(("student_code:" + fname, text))
                except (IOError, UnicodeDecodeError):
                    continue

    # Collect text from tool logs
    tool_texts = []
    for log in tool_logs or []:
        for key, value in log.args.items():
            tool_texts.append(("tool_arg:" + str(key), str(value)))
        if log.result:
            tool_texts.append(("tool_result", str(log.result)))

    # Check all sources for fix and bug patterns
    all_sources = workspace_texts + tool_texts

    for source_name, text in all_sources:
        for pattern in fix_patterns:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                result["fixed"] = True
                if not result["fix_found_in"]:
                    result["fix_found_in"] = source_name
                break

    for source_name, text in all_sources:
        for pattern in bug_patterns:
            if re.search(pattern, text, re.IGNORECASE | re.MULTILINE):
                result["bug_still_present"] = True
                break
        if result["bug_still_present"]:
            break

    # If fix is found, override bug_still_present only if fix source is
    # the same or later than bug source (the fix replaces the bug)
    if result["fixed"] and result["bug_still_present"]:
        # If fix was found in tool args (file_write), the bug may only
        # appear in the original student code read — that's expected
        if (
            "tool_arg" in result["fix_found_in"]
            or "workspace" in result["fix_found_in"]
        ):
            result["bug_still_present"] = False

    return result


def check_execution_output(
    tool_logs: list,
    success_keywords: list[str],
    failure_keywords: list[str] | None = None,
) -> dict:
    """Check tool logs for evidence of successful execution.

    Args:
        tool_logs: List of ToolCallLog objects.
        success_keywords: Keywords that indicate successful execution.
        failure_keywords: Keywords that indicate failure (optional).

    Returns:
        {"executed": bool, "output_valid": bool}
    """
    result = {"executed": False, "output_valid": False}

    if failure_keywords is None:
        failure_keywords = ["traceback", "error:", "exception"]

    for log in tool_logs or []:
        output = str(log.result or "").lower()

        if not log.success:
            continue

        # Check for failure indicators
        has_failure = any(kw.lower() in output for kw in failure_keywords)
        if has_failure:
            continue

        # Check for success indicators
        has_success = any(kw.lower() in output for kw in success_keywords)
        if has_success:
            result["executed"] = True
            result["output_valid"] = True

    return result


def check_conversation_concepts(
    conversation: list,
    required_concepts: list[str],
    tool_logs: list = None,
) -> dict:
    """Scan conversation + tool logs for concept keywords (case-insensitive).

    Args:
        conversation: List of conversation message dicts.
        required_concepts: Keywords/phrases to look for.
        tool_logs: Optional tool call logs for additional context.

    Returns:
        {"concepts_found": dict[str, bool], "fraction": float}
    """
    # Build searchable text from conversation
    parts = []
    for msg in conversation or []:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))

    # Also include tool log results and args
    for log in tool_logs or []:
        parts.append(str(log.result or ""))
        for v in log.args.values():
            parts.append(str(v))

    full_text = "\n".join(parts).lower()

    concepts_found = {}
    for concept in required_concepts:
        concepts_found[concept] = concept.lower() in full_text

    total = len(required_concepts)
    found = sum(1 for v in concepts_found.values() if v)
    fraction = found / total if total > 0 else 0.0

    return {
        "concepts_found": concepts_found,
        "fraction": fraction,
    }


def check_root_cause_explained(
    conversation: list,
    tool_logs: list,
    root_cause_keywords: list[str],
) -> bool:
    """True if any root cause keyword found in conversation or tool logs.

    Args:
        conversation: List of conversation message dicts.
        tool_logs: List of ToolCallLog objects.
        root_cause_keywords: Keywords that indicate root cause explanation.

    Returns:
        True if at least one keyword is found.
    """
    parts = []
    for msg in conversation or []:
        content = msg.get("content", "")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    parts.append(str(item.get("text", "")))
                else:
                    parts.append(str(item))

    for log in tool_logs or []:
        parts.append(str(log.result or ""))
        for v in log.args.values():
            parts.append(str(v))

    full_text = "\n".join(parts).lower()
    return any(kw.lower() in full_text for kw in root_cause_keywords)


def check_fix_verified(
    tool_logs: list,
    before_keywords: list[str],
    after_keywords: list[str],
) -> bool:
    """True if both before AND after keyword sets found in tool logs.

    This checks whether the agent ran the code before and after fixing,
    producing output that contains both sets of keywords (indicating
    comparison was made).

    Args:
        tool_logs: List of ToolCallLog objects.
        before_keywords: Keywords expected in pre-fix output.
        after_keywords: Keywords expected in post-fix output.

    Returns:
        True if both before and after evidence found.
    """
    all_output = []
    for log in tool_logs or []:
        all_output.append(str(log.result or "").lower())
        for v in log.args.values():
            all_output.append(str(v).lower())

    full_text = "\n".join(all_output)

    has_before = any(kw.lower() in full_text for kw in before_keywords)
    has_after = any(kw.lower() in full_text for kw in after_keywords)

    return has_before and has_after
