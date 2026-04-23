"""Experiment-private contract helpers for issue #83 validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from experiments.student_sim_stability.core.paths import RESOURCE_ROOT
from server.schemas import StudentPersona

if TYPE_CHECKING:
    from server.schemas import QuantTutorTask

CONTRACTS_DIR = RESOURCE_ROOT / "contracts"
CONTRACT_VERSION = "issue83-2026-04-23"

PERSONA_REQUIRED_FIELDS = {
    "persona_id",
    "knowledge_level",
    "description",
    "known_concepts",
    "unknown_concepts",
    "emotional_profile",
    "behavioral_rules",
    "contract_version",
    "expected_question_style",
    "expected_confusion_style",
    "expected_recovery_style",
    "expected_confidence_pattern",
    "failure_modes",
}


def _load_json(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"Contract is not a JSON object: {path}")
    return data


def persona_contract_path(persona_id: str) -> Path:
    return CONTRACTS_DIR / "personas" / f"{persona_id}.json"


def load_persona_contract(persona_id: str) -> dict[str, Any]:
    path = persona_contract_path(persona_id)
    if not path.exists():
        raise FileNotFoundError(f"Missing persona contract: {path}")
    data = _load_json(path)
    missing = sorted(PERSONA_REQUIRED_FIELDS - set(data))
    if missing:
        raise ValueError(f"Persona contract {persona_id} missing fields: {missing}")
    return data


def load_student_persona(persona_id: str) -> StudentPersona:
    contract = load_persona_contract(persona_id)
    schema_fields = {
        "persona_id",
        "knowledge_level",
        "description",
        "known_concepts",
        "unknown_concepts",
        "emotional_profile",
        "behavioral_rules",
    }
    return StudentPersona(**{key: contract[key] for key in schema_fields})


def _flatten_concepts(value: object) -> list[str]:
    if isinstance(value, dict):
        return [str(item) for items in value.values() for item in items]
    if isinstance(value, list):
        return [str(item) for item in value]
    return []


def build_contract_user_description(persona_id: str) -> str:
    """Build simulator user_description from experiment-private contracts."""
    contract = load_persona_contract(persona_id)
    known = _flatten_concepts(contract.get("known_concepts"))
    unknown = _flatten_concepts(contract.get("unknown_concepts"))
    parts = [f"Your profile: {contract['description']}"]

    if known:
        parts.append(f"- You are familiar with: {', '.join(known)}")
    if unknown:
        parts.append(f"- You have no experience with: {', '.join(unknown)}")

    rules = contract.get("behavioral_rules", [])
    if rules:
        parts.append("\nBehavioral rules (follow strictly):")
        parts.extend(f"  - {rule}" for rule in rules)

    parts.extend(
        [
            "",
            "Persona-specific expectations:",
            f"- Question style: {contract['expected_question_style']}",
            f"- Confusion style: {contract['expected_confusion_style']}",
            f"- Recovery style: {contract['expected_recovery_style']}",
            f"- Confidence pattern: {contract['expected_confidence_pattern']}",
            "",
            "Interaction rules:",
            "- If the tutor asks you a question, answer it first before asking your next question.",
            "- Respond naturally to what the tutor just said.",
            "- Do not ask about concepts you are already familiar with unless the tutor introduced a new nuance.",
            "- If the tutor drifts from your question, bring it back.",
            "- Never fabricate data, code, or files. If asked to upload or share anything, say you have no files.",
            "- You interact through text-only chat and can only see what appears in this chat.",
        ]
    )
    return "\n".join(parts)


def build_contract_scenario(task: "QuantTutorTask", persona_id: str) -> str:
    """Build simulator scenario without reading shared prompt_config."""
    opening = (task.student_openings or {}).get(persona_id, "")
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


def render_persona_contract_text(persona_id: str) -> str:
    contract = load_persona_contract(persona_id)
    known = json.dumps(contract["known_concepts"], ensure_ascii=False)
    unknown = json.dumps(contract["unknown_concepts"], ensure_ascii=False)
    rules = "\n".join(f"- {rule}" for rule in contract["behavioral_rules"])
    failures = "\n".join(f"- {item}" for item in contract["failure_modes"])
    return (
        f"Persona contract: {contract['persona_id']}\n"
        f"Version: {contract['contract_version']}\n"
        f"Description: {contract['description']}\n"
        f"Known concepts: {known}\n"
        f"Unknown concepts: {unknown}\n"
        f"Emotional profile: {contract['emotional_profile']}\n"
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
