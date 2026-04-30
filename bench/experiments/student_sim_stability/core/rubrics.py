"""Named rubric metadata for issue #83 validation."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import Any

from experiments.student_sim_stability.core.paths import RESOURCE_ROOT

RUBRICS_DIR = RESOURCE_ROOT / "rubrics"
RUBRIC_VERSION = "v1.3.0"

DIMENSION_TO_FILE = {
    "S1": "S1_persona_adherence.json",
    "S2": "S2_persona_drift.json",
    "S3": "S3_cross_run_reproducibility.json",
    "S4": "S4_blind_identifiability.json",
    "S5": "S5_targeted_probes.json",
    "S6": "S6_generic_falsifier.json",
}

# Single source of truth for which score field carries the dimension's headline
# numeric (used by judge-agreement, human-alignment, multi-judge, primary
# report, and judge-qualification gate). Each entry must appear in the
# corresponding rubric's ``score_fields`` list — `primary_score_field` enforces
# this at lookup time.
_PRIMARY_SCORE_FIELD: dict[str, str] = {
    "S1": "overall",
    "S2": "overall_drift_score",
    "S3": "overall_reproducibility",
    "S4": "contract_fit",
    "S5": "overall_probe_pass",
    "S6": "distinctiveness",
}

# Numeric fields used by panel averaging. These are the ``score_fields`` minus
# the categorical / structured ones (``identified_persona`` for S4,
# ``persona_value_add`` for S6, ``per_turn`` and ``drift_onset_turn`` for S2)
# that cannot be element-wise averaged across judges.
_NUMERIC_SCORE_FIELDS: dict[str, tuple[str, ...]] = {
    "S1": ("knowledge_boundary", "emotional_tone", "behavioral_rules", "overall"),
    "S2": ("overall_drift_score",),
    "S3": (
        "topic_trajectory",
        "knowledge_display",
        "emotional_consistency",
        "question_patterns",
        "overall_reproducibility",
    ),
    "S4": ("confidence", "contract_fit"),
    "S5": ("contract_fit", "facet_fit", "overall_probe_pass"),
    "S6": ("distinctiveness",),
}

LEGACY_RUBRIC_ID_BY_ID: dict[str, str] = {
    "S1_persona_adherence": "D" "1_persona_adherence",
    "S2_persona_drift": "D" "3_drift_detection",
    "S3_cross_run_reproducibility": "D" "2_cross_run_reproducibility",
    "S4_blind_identifiability": "B" "1_blind_persona_identification",
    "S5_targeted_probes": "P" "1_targeted_probe",
    "S6_generic_falsifier": "C" "1_persona_distinguishability",
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


@functools.lru_cache(maxsize=None)
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


@functools.lru_cache(maxsize=None)
def primary_score_field(dimension: str) -> str:
    """Headline numeric score field for ``dimension``.

    Validates on first lookup that the named field actually appears in the
    rubric JSON's ``score_fields`` so the table here cannot silently drift
    away from the rubric.
    """
    try:
        field = _PRIMARY_SCORE_FIELD[dimension]
    except KeyError as exc:
        raise KeyError(f"Unknown rubric dimension: {dimension!r}") from exc
    declared = set(load_rubric(dimension).get("score_fields", []))
    if field not in declared:
        raise ValueError(
            f"primary score field {field!r} for {dimension} not declared in rubric "
            f"score_fields {sorted(declared)}"
        )
    return field


@functools.lru_cache(maxsize=None)
def numeric_score_fields(dimension: str) -> tuple[str, ...]:
    """Numeric score fields used for panel averaging on ``dimension``.

    Strict subset of the rubric JSON's ``score_fields``. Excludes categorical
    (``identified_persona``), structured (``per_turn``, ``persona_value_add``),
    and incidental (``drift_onset_turn``) entries.
    """
    try:
        fields = _NUMERIC_SCORE_FIELDS[dimension]
    except KeyError as exc:
        raise KeyError(f"Unknown rubric dimension: {dimension!r}") from exc
    declared = set(load_rubric(dimension).get("score_fields", []))
    extra = sorted(set(fields) - declared)
    if extra:
        raise ValueError(
            f"numeric score fields {extra} for {dimension} not declared in rubric "
            f"score_fields {sorted(declared)}"
        )
    return fields


def all_rubrics() -> list[dict[str, Any]]:
    return [load_rubric(dim) for dim in DIMENSION_TO_FILE]
