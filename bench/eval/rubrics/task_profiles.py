"""Task deliverable profiles used to select applicable evaluator checks."""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping


class RubricProfile(str, Enum):
    SAFETY = "safety"
    DIAGNOSIS = "diagnosis"
    CONVERSATIONAL = "conversational"
    CODE_IMPLEMENTATION = "code_implementation"
    GENERAL = "general"


_CODE_CATEGORIES = {
    "alpha_research",
    "backtest",
    "backtest_engine",
    "code_debugging",
    "code_generation",
    "data_analysis",
    "data_engineering",
    "debug",
    "implementation",
    "strategy",
}


def enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def category_value(task: Any) -> str:
    return enum_value(getattr(task, "category", ""))


def task_requires_code(task: Any) -> bool:
    return bool(getattr(task, "requires_code", False))


def rubric_profile_for_task(task: Any) -> str:
    explicit = enum_value(getattr(task, "rubric_profile", ""))
    if explicit:
        return explicit

    return rubric_profile_for_category(
        category=category_value(task),
        requires_code=task_requires_code(task),
    )


def rubric_profile_for_category(*, category: str, requires_code: bool) -> str:
    if category == "adversarial":
        return RubricProfile.SAFETY.value
    if category == "diagnostic":
        return RubricProfile.DIAGNOSIS.value
    if category == "end_to_end" and not requires_code:
        return RubricProfile.CONVERSATIONAL.value
    if requires_code or category in _CODE_CATEGORIES:
        return RubricProfile.CODE_IMPLEMENTATION.value
    return RubricProfile.GENERAL.value


def should_apply_data_source_check(task: Any) -> bool:
    profile = rubric_profile_for_task(task)
    if profile == RubricProfile.CONVERSATIONAL.value:
        return False
    if profile == RubricProfile.SAFETY.value and not task_requires_code(task):
        return False
    return True


def should_evaluate_code_lifecycle(task: Any) -> bool:
    profile = rubric_profile_for_task(task)
    return task_requires_code(task) or profile == RubricProfile.CODE_IMPLEMENTATION.value


def qp_dimension_weights(
    base_weights: Mapping[str, float],
    *,
    task: Any | None = None,
    category: str = "",
    task_requires_code_value: bool | None = None,
    rubric_profile: str = "",
) -> dict[str, float]:
    weights = dict(base_weights)
    if task is not None:
        include_code_lifecycle = should_evaluate_code_lifecycle(task)
    else:
        category_value_text = category
        requires_code = bool(task_requires_code_value)
        profile = rubric_profile
        if not profile:
            profile = rubric_profile_for_category(
                category=category_value_text,
                requires_code=requires_code,
            )
        include_code_lifecycle = (
            requires_code or profile == RubricProfile.CODE_IMPLEMENTATION.value
        )
        if profile == RubricProfile.CONVERSATIONAL.value:
            include_code_lifecycle = False

    if not include_code_lifecycle:
        weights.pop("code_lifecycle", None)
    return weights
