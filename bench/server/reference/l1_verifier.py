"""Reference Layer 1 verifier used by the platform evaluator adapter."""

from __future__ import annotations

from typing import Any

from eval.contracts.schemas import QuantTutorTask
from server.reference import knowledge_qa


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _expected_outputs(task: QuantTutorTask) -> list:
    gt = task.ground_truth
    return list(getattr(gt, "expected_outputs", []) or [])


def evaluate(
    *,
    task: QuantTutorTask,
    actual_output: str,
    workspace_path: str | None = None,
    eval_model: str | None = None,
) -> dict[str, Any]:
    """Evaluate a Layer 1 answer using the deterministic reference scorer."""

    task_type = _enum_value(task.task_type)
    expected_outputs = _expected_outputs(task)
    if task_type == "agent_execution" or expected_outputs:
        from eval.programmatic.l1_verifier import evaluate as evaluate_outputs

        result = evaluate_outputs(
            workspace_path or ".",
            expected_outputs=expected_outputs,
        )
        result.update(
            {
                "task_id": task.task_id,
                "category": _enum_value(task.category),
                "difficulty": _enum_value(task.difficulty),
                "eval_model": eval_model,
            }
        )
        return result

    result = knowledge_qa.evaluate(
        question=task.question or task.description,
        reference_answer=task.reference_answer or "",
        actual_output=actual_output,
        context=task.context,
    )
    result.update(
        {
            "task_id": task.task_id,
            "category": _enum_value(task.category),
            "difficulty": _enum_value(task.difficulty),
            "eval_model": eval_model,
        }
    )
    return result
