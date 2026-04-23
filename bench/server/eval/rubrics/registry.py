"""Helpers for the judge rubric registry."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_RUBRIC_DIR = Path(__file__).resolve().parent
_REGISTRY_PATH = _RUBRIC_DIR / "rubric_registry.json"


@lru_cache(maxsize=1)
def load_rubric_registry() -> dict[str, Any]:
    """Load the first-class judged-dimension rubric registry."""

    return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))


def registry_rubrics_by_id() -> dict[str, dict[str, Any]]:
    """Return registry rubric entries keyed by rubric_id."""

    registry = load_rubric_registry()
    return {
        str(entry["rubric_id"]): entry
        for entry in registry.get("rubrics", [])
        if entry.get("rubric_id")
    }


def get_registry_rubric(rubric_id: str) -> dict[str, Any] | None:
    """Look up a registry rubric by ID."""

    return registry_rubrics_by_id().get(rubric_id)


def mapped_registry_ids(track: str, dimension: str) -> list[str]:
    """Find registry IDs that reference an implemented judge dimension."""

    matches: list[str] = []
    for rubric_id, entry in registry_rubrics_by_id().items():
        for mapped in entry.get("mapped_judge_dimensions", []):
            if mapped.get("track") == track and mapped.get("dimension") == dimension:
                matches.append(rubric_id)
                break
    return sorted(matches)
