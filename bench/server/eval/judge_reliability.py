"""Reference metadata for the selected judge-reliability bundle."""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_REFERENCE_PATH = Path(__file__).with_name("judge_reliability_reference.json")


@lru_cache(maxsize=1)
def load_judge_reliability_reference() -> dict[str, Any]:
    """Load the selected judge-reliability reference bundle."""

    payload = json.loads(_REFERENCE_PATH.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"Invalid judge reliability reference: {_REFERENCE_PATH}")
    return payload


def build_judge_reliability_metadata(eval_model: str | None) -> dict[str, Any]:
    """Return score-export metadata linking the run to judge validation."""

    reference = copy.deepcopy(load_judge_reliability_reference())
    current_eval_model = str(eval_model).strip() if eval_model else None
    reference_judge_model = str(reference.get("reference_judge_model") or "").strip()
    model_match = (
        current_eval_model == reference_judge_model
        if current_eval_model and reference_judge_model
        else None
    )
    reference["current_eval_model"] = current_eval_model
    reference["current_eval_model_matches_reference"] = model_match
    return reference
