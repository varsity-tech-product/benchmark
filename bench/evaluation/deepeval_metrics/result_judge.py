"""Compatibility wrapper for canonical v6 QR result judge."""

from __future__ import annotations

from server.eval.judges.result_judge import (  # noqa: F401
    async_evaluate_result_quality,
    evaluate_result_quality,
)


def _build_result_judge_prompt(
    task_description: str,
    category: str,
    *,
    agent_key_outputs: str,
    agent_workspace_files: list[str],
    agent_summary: str,
    reference: dict | None,
) -> str:
    """Build a lightweight prompt for legacy prompt-rendering tests.

    Runtime evaluation no longer uses this function; v6 builds structured QR
    context through ``server.eval.inputs.context_builder``.
    """

    files = ", ".join(agent_workspace_files) if agent_workspace_files else "(none)"
    prompt = f"""You are evaluating the RESULT QUALITY of an AI tutoring agent's task execution.

TASK: {task_description}
CATEGORY: {category}

AGENT RESULT:
- Files produced: {files}
- Key tool outputs:
{agent_key_outputs}
- Agent summary:
{agent_summary}
"""
    if reference:
        prompt += (
            "\nREFERENCE RESULT is available and should be used as expert context.\n"
        )
    if category == "debug":
        prompt += """
DEBUG TASKS:
For debug tasks, judge whether the bug was resolved, not whether the strategy is profitable.
"""
    return prompt


__all__ = [
    "_build_result_judge_prompt",
    "async_evaluate_result_quality",
    "evaluate_result_quality",
]
