"""Compatibility prompt entry points for older server imports."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from eval.contracts.schemas import QuantTutorTask, UserPersona


def _reference_prompts() -> Any:
    return import_module("server.reference.prompts")


def build_user_description(
    persona: UserPersona,
    has_incremental_tc: bool = False,
) -> str:
    """Compatibility wrapper for the reference user simulator prompt."""
    return _reference_prompts().build_reference_user_description(
        persona,
        has_incremental_tc=has_incremental_tc,
    )


def build_scenario(
    task: QuantTutorTask,
    persona_id: str,
    has_incremental_tc: bool = False,
) -> str:
    """Compatibility wrapper for the reference user simulator scenario."""
    return _reference_prompts().build_reference_scenario(
        task,
        persona_id,
        has_incremental_tc=has_incremental_tc,
    )
