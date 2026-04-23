"""Named rubric metadata for issue #83 validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments.student_sim_stability.core.paths import RESOURCE_ROOT

RUBRICS_DIR = RESOURCE_ROOT / "rubrics"
RUBRIC_VERSION = "issue83-2026-04-23"

DIMENSION_TO_FILE = {
    "D1": "D1_persona_adherence.json",
    "D2": "D2_cross_run_reproducibility.json",
    "D3": "D3_cross_model_consistency.json",
    "D4": "D4_drift_detection.json",
    "control": "C1_persona_distinguishability.json",
    "P1": "P1_targeted_probe.json",
    "B1": "B1_blind_persona_identification.json",
}

FAILURE_TAXONOMY_FIELDS = [
    "knowledge_leak",
    "under_competence",
    "emotional_mismatch",
    "generic_student_behavior",
    "co_teacher_drift",
    "task_forgetting",
    "persona_contract_contradiction",
]

FAILURE_OUTPUT_FIELDS = [
    "failure_types",
    "dominant_failure_type",
    "failure_evidence",
]


def rubric_path(dimension: str) -> Path:
    try:
        filename = DIMENSION_TO_FILE[dimension]
    except KeyError as exc:
        raise ValueError(f"Unknown rubric dimension: {dimension}") from exc
    return RUBRICS_DIR / filename


def load_rubric(dimension: str) -> dict[str, Any]:
    path = rubric_path(dimension)
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    required = {
        "rubric_id",
        "version",
        "dimension",
        "name",
        "definition",
        "judge_context_inputs",
        "score_fields",
        "score_scales",
        "aggregation_formula",
        "failure_taxonomy_fields",
        "prompt_template",
    }
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"Rubric {dimension} missing fields: {missing}")
    path_value = data.get("prompt_template_path")
    if path_value and not (RUBRICS_DIR / str(path_value)).exists():
        raise ValueError(
            f"Rubric {dimension} prompt template path missing: {path_value}"
        )
    return data


def rubric_metadata(dimension: str) -> dict[str, Any]:
    rubric = load_rubric(dimension)
    return {
        "rubric_id": rubric["rubric_id"],
        "rubric_version": rubric["version"],
        "rubric_name": rubric["name"],
        "aggregation_formula": rubric["aggregation_formula"],
    }


def rubric_prompt_template(dimension: str) -> str:
    rubric = load_rubric(dimension)
    path_value = rubric.get("prompt_template_path")
    if path_value:
        path = RUBRICS_DIR / str(path_value)
        return path.read_text(encoding="utf-8")
    return str(rubric["prompt_template"])


def required_score_keys(dimension: str) -> set[str]:
    rubric = load_rubric(dimension)
    keys = set(rubric.get("score_fields", []))
    keys.add("reasoning")
    keys.update(rubric.get("required_failure_output_fields", FAILURE_OUTPUT_FIELDS))
    return keys


def all_rubrics() -> list[dict[str, Any]]:
    return [load_rubric(dim) for dim in DIMENSION_TO_FILE]
