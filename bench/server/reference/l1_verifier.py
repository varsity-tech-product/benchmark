"""Reference Layer 1 verifier used by the platform evaluator adapter."""

from __future__ import annotations

from typing import Any

from eval.contracts.schemas import QuantTutorTask
from server.reference import knowledge_qa


def evaluate(
    *,
    task: QuantTutorTask,
    actual_output: str,
    eval_model: str | None = None,
) -> dict[str, Any]:
    """Evaluate a Layer 1 answer using the deterministic reference scorer."""

    result = knowledge_qa.evaluate(
        question=task.description,
        reference_answer=task.reference_answer or "",
        actual_output=actual_output,
        context=task.context,
    )
    result.update(
        {
            "task_id": task.task_id,
            "category": task.category.value,
            "difficulty": task.difficulty.value,
            "eval_model": eval_model,
        }
    )
    return result
