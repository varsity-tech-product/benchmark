"""Experiment-private contract helpers for issue #83 validation."""

from __future__ import annotations

import functools
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from experiments.user_sim_stability.core.config import STABILITY_TASK_OPENINGS
from experiments.user_sim_stability.core.io_utils import load_json
from experiments.user_sim_stability.core.paths import RESOURCE_ROOT
from server.config.prompt_config import (
    build_user_description as _server_build_user_description,
)
from eval.contracts.schemas import UserPersona

if TYPE_CHECKING:
    from eval.contracts.schemas import QuantTutorTask

CONTRACTS_DIR = RESOURCE_ROOT / "contracts"
CONTRACT_VERSION = "v1.0.0"

# Experiment-private copy of emotional profile descriptions. The byte-identity
# of this file and bench/personas/emotional_profiles.json is enforced by
# tests/test_persona_source_consistency.py so the judge side (which reads this
# path) and the user side (which reads the canonical file through
# server.config.prompt_config) never diverge.
_EMOTIONAL_PROFILES_PATH = CONTRACTS_DIR / "emotional_profiles.json"


@functools.lru_cache(maxsize=1)
def _emotional_profiles() -> dict[str, str]:
    if not _EMOTIONAL_PROFILES_PATH.exists():
        raise FileNotFoundError(
            f"Missing experiment-private emotional profiles at {_EMOTIONAL_PROFILES_PATH}"
        )
    return json.loads(_EMOTIONAL_PROFILES_PATH.read_text(encoding="utf-8"))


PERSONA_REQUIRED_FIELDS = {
    "persona_id",
    "knowledge_level",
    "description",
    "familiar_concepts",
    "unfamiliar_concepts",
    "emotional_profile",
    "behavioral_rules",
    "contract_version",
    "expected_question_style",
    "expected_confusion_style",
    "expected_recovery_style",
    "expected_confidence_pattern",
    "failure_modes",
}


def persona_contract_path(persona_id: str) -> Path:
    return CONTRACTS_DIR / "personas" / f"{persona_id}.json"


@functools.lru_cache(maxsize=None)
def load_persona_contract(persona_id: str) -> dict[str, Any]:
    path = persona_contract_path(persona_id)
    if not path.exists():
        raise FileNotFoundError(f"Missing persona contract: {path}")
    data = load_json(path)
    if not isinstance(data, dict):
        raise ValueError(f"Contract is not a JSON object: {path}")
    missing = sorted(PERSONA_REQUIRED_FIELDS - set(data))
    if missing:
        raise ValueError(f"Persona contract {persona_id} missing fields: {missing}")
    return data


@functools.lru_cache(maxsize=None)
def load_user_persona(persona_id: str) -> UserPersona:
    contract = load_persona_contract(persona_id)
    schema_fields = {
        "persona_id",
        "knowledge_level",
        "description",
        "familiar_concepts",
        "unfamiliar_concepts",
        "emotional_profile",
        "behavioral_rules",
    }
    return UserPersona(**{key: contract[key] for key in schema_fields})


def build_contract_user_description(persona_id: str) -> str:
    """Build simulator user_description for experiment trials.

    This is a thin wrapper around ``server.config.prompt_config.build_user_description``
    so that experiment user prompts are byte-identical to production REST session
    prompts. Experiment-private contract fields (``expected_*``, ``failure_modes``)
    are intentionally not injected here — they are judge-side metadata and are
    consumed through ``render_persona_contract_text`` in judge prompt rendering.
    """
    return _server_build_user_description(load_user_persona(persona_id))


def build_contract_scenario(task: "QuantTutorTask", persona_id: str) -> str:
    """Build simulator scenario without reading shared prompt_config."""
    opening = (
        STABILITY_TASK_OPENINGS.get(task.task_id, {}).get(persona_id)
        or task.user_opening
    )
    parts = [
        f"Scenario: {task.description}",
        f'Your opening message was: "{opening}"',
    ]
    if task.ground_truth and task.ground_truth.required_capabilities:
        goals = list(task.ground_truth.required_capabilities)
        is_adversarial = getattr(task.category, "value", task.category) == "adversarial"
        parts.append("")
        parts.append(
            "What you expect from the tutor in this conversation:"
            if is_adversarial
            else "Learning goals:"
        )
        for idx, goal in enumerate(goals, 1):
            parts.append(f"  {idx}. {goal}")
        if not is_adversarial:
            parts.extend(
                [
                    "",
                    "Introduce goals one at a time. Do not ask about all of them at once.",
                    "Once you feel you understand a topic, naturally move to the next goal.",
                    "If the tutor drifts into setup or configuration tangents, redirect after one turn.",
                ]
            )
    return "\n".join(parts)


def resolve_emotional_profile(persona_id: str) -> tuple[str, str]:
    """Return ``(key, expanded_description)`` for a persona's emotional profile.

    Reads the experiment-private ``resources/contracts/emotional_profiles.json``
    (kept byte-identical to ``bench/personas/emotional_profiles.json`` by the
    ``tests/test_persona_source_consistency.py`` lock) so the judge side of the
    experiment never accidentally pulls a mutated canonical file.

    Falls back to ``(key, key)`` if the key is not present in the private copy
    so callers can always render something.
    """
    contract = load_persona_contract(persona_id)
    key = contract.get("emotional_profile", "")
    description = _emotional_profiles().get(key, key)
    return key, description


def render_persona_contract_text(persona_id: str) -> str:
    """Render the full persona contract for judge-side consumption (S5/S4/S6/S1-S2).

    Judges see the full contract including ``expected_*`` and ``failure_modes`` so
    they have the complete rubric anchor when scoring persona fidelity. The user
    simulator does NOT see this text — user prompt is built from the slimmer
    ``server.config.prompt_config.build_user_description`` via the thin wrapper.
    """
    contract = load_persona_contract(persona_id)
    familiar = json.dumps(contract["familiar_concepts"], ensure_ascii=False)
    unfamiliar = json.dumps(contract["unfamiliar_concepts"], ensure_ascii=False)
    emo_key, emo_desc = resolve_emotional_profile(persona_id)
    rules = "\n".join(f"- {rule}" for rule in contract["behavioral_rules"])
    failures = "\n".join(f"- {item}" for item in contract["failure_modes"])
    return (
        f"Persona contract: {contract['persona_id']}\n"
        f"Version: {contract['contract_version']}\n"
        f"Description: {contract['description']}\n"
        f"Familiar concepts: {familiar}\n"
        f"Unfamiliar concepts: {unfamiliar}\n"
        f"Emotional profile ({emo_key}): {emo_desc}\n"
        f"Behavioral rules:\n{rules}\n"
        f"Expected question style: {contract['expected_question_style']}\n"
        f"Expected confusion style: {contract['expected_confusion_style']}\n"
        f"Expected recovery style: {contract['expected_recovery_style']}\n"
        f"Expected confidence pattern: {contract['expected_confidence_pattern']}\n"
        f"Failure modes:\n{failures}"
    )


def list_persona_contracts() -> list[dict[str, Any]]:
    personas_dir = CONTRACTS_DIR / "personas"
    return [
        load_persona_contract(path.stem) for path in sorted(personas_dir.glob("*.json"))
    ]
