"""Ensure canonical personas and the experiment's persona contracts stay in sync
on the fields that must be identical.

Two sources exist:
  1. bench/personas/*.json            — canonical, consumed by the main server
  2. bench/experiments/student_sim_stability/resources/contracts/personas/*.json
     — experiment-private copy, extended with contract_version and expected_*
     fields for the student-sim-stability acceptance contract.

The shared fields must match byte-for-byte so that any persona change lands in
both places. Experiment-only fields (contract_version, expected_*, failure_modes)
are allowed to live only in the experiment copy.
"""

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_DIR = REPO_ROOT / "bench" / "personas"
EXPERIMENT_CONTRACTS_DIR = (
    REPO_ROOT
    / "bench"
    / "experiments"
    / "student_sim_stability"
    / "resources"
    / "contracts"
)
EXPERIMENT_DIR = EXPERIMENT_CONTRACTS_DIR / "personas"

SHARED_FIELDS = [
    "persona_id",
    "knowledge_level",
    "description",
    "familiar_concepts",
    "unfamiliar_concepts",
    "emotional_profile",
    "behavioral_rules",
]

PERSONA_IDS = [
    "developer_crossover",
    "double_novice",
    "finance_veteran",
    "fullstack_practitioner",
]


@pytest.mark.parametrize("persona_id", PERSONA_IDS)
def test_persona_contract_matches_canonical(persona_id: str) -> None:
    canonical = json.loads((CANONICAL_DIR / f"{persona_id}.json").read_text())
    experiment = json.loads((EXPERIMENT_DIR / f"{persona_id}.json").read_text())
    for field in SHARED_FIELDS:
        assert canonical[field] == experiment[field], (
            f"{persona_id}.{field} differs between "
            f"{CANONICAL_DIR} and {EXPERIMENT_DIR}"
        )


@pytest.mark.parametrize("persona_id", PERSONA_IDS)
def test_no_legacy_concept_keys(persona_id: str) -> None:
    """Guard against accidental reintroduction of the pre-rename field names."""
    for source_dir in (CANONICAL_DIR, EXPERIMENT_DIR):
        data = json.loads((source_dir / f"{persona_id}.json").read_text())
        assert (
            "known_concepts" not in data
        ), f"{persona_id} in {source_dir} still has legacy 'known_concepts'"
        assert (
            "unknown_concepts" not in data
        ), f"{persona_id} in {source_dir} still has legacy 'unknown_concepts'"


def test_emotional_profiles_match_byte_identical() -> None:
    """The experiment reads its private ``resources/contracts/emotional_profiles.json``
    (see ``core/contracts.py::resolve_emotional_profile``) while the server
    REST path reads ``bench/personas/emotional_profiles.json``. The student
    prompt (built through server) and the judge prompts (built through the
    experiment-private copy) must see identical emotional-profile text, so the
    two files must stay byte-identical. This test locks that invariant."""
    canonical = (CANONICAL_DIR / "emotional_profiles.json").read_bytes()
    experiment = (EXPERIMENT_CONTRACTS_DIR / "emotional_profiles.json").read_bytes()
    assert canonical == experiment, (
        "emotional_profiles.json differs between "
        f"{CANONICAL_DIR} and {EXPERIMENT_CONTRACTS_DIR}; the two files must "
        "stay byte-identical so student and judge prompts align."
    )


def test_emotional_profile_keys_cover_all_personas() -> None:
    """Every persona's ``emotional_profile`` key must exist in the emotional
    profiles JSON so ``resolve_emotional_profile`` returns an expanded
    description instead of the fallback-to-key."""
    profiles = json.loads(
        (EXPERIMENT_CONTRACTS_DIR / "emotional_profiles.json").read_text()
    )
    for persona_id in PERSONA_IDS:
        data = json.loads((EXPERIMENT_DIR / f"{persona_id}.json").read_text())
        key = data.get("emotional_profile", "")
        assert key in profiles, (
            f"{persona_id}.emotional_profile={key!r} not in "
            f"{EXPERIMENT_CONTRACTS_DIR / 'emotional_profiles.json'}; "
            "judge prompt would fall back to showing the bare key."
        )


def test_persona_vocabulary_is_closed_world() -> None:
    """Every term that appears in any persona's familiar or unfamiliar list must
    appear in every other persona's familiar OR unfamiliar list (no grey holes)
    and must not appear in both familiar and unfamiliar for the same persona.

    Rationale: judges evaluate knowledge boundary by asking "is this term
    expected or out-of-scope for this persona?". If a term like ``returns`` is
    listed as familiar for fullstack_practitioner but absent from
    developer_crossover entirely, the judge has no way to score
    developer_crossover's use of it either way, and B1 / D1 scoring becomes
    ambiguous on that vocabulary. Closed-world forces an explicit decision per
    term per persona.
    """
    personas = {
        pid: json.loads((EXPERIMENT_DIR / f"{pid}.json").read_text())
        for pid in PERSONA_IDS
    }

    def _collect(domain: str, bucket: str) -> set[str]:
        result: set[str] = set()
        for data in personas.values():
            result.update(data[f"{bucket}_concepts"].get(domain, []))
        return result

    for domain in ("finance", "code"):
        universe = _collect(domain, "familiar") | _collect(domain, "unfamiliar")
        for pid, data in personas.items():
            fam = set(data["familiar_concepts"].get(domain, []))
            unfam = set(data["unfamiliar_concepts"].get(domain, []))
            missing = universe - fam - unfam
            overlap = fam & unfam
            assert not missing, (
                f"{pid}.{domain}: closed-world gap — these terms appear in "
                f"another persona but are undeclared here: {sorted(missing)}. "
                "Every such term must be added to either familiar_concepts "
                f"or unfamiliar_concepts for {pid}."
            )
            assert not overlap, (
                f"{pid}.{domain}: term appears in both familiar and "
                f"unfamiliar: {sorted(overlap)}"
            )


# ---------------------------------------------------------------------------
# Schema migration guards (2026-04-24 known/unknown -> familiar/unfamiliar)
# ---------------------------------------------------------------------------


def _import_student_persona_classes():
    """Import both StudentPersona copies (server + orchestrator)."""
    import sys

    bench_dir = str(REPO_ROOT / "bench")
    if bench_dir not in sys.path:
        sys.path.insert(0, bench_dir)

    from orchestrator.schemas import StudentPersona as OrchestratorStudentPersona
    from eval.contracts.schemas import StudentPersona as ServerStudentPersona

    return [ServerStudentPersona, OrchestratorStudentPersona]


@pytest.mark.parametrize(
    "legacy_key,current_key",
    [
        ("known_concepts", "familiar_concepts"),
        ("unknown_concepts", "unfamiliar_concepts"),
    ],
)
def test_student_persona_rejects_legacy_concept_keys(
    legacy_key: str, current_key: str
) -> None:
    """Without this guard, Pydantic would silently drop legacy keys (extra='ignore')
    and produce a student with empty knowledge boundaries — a silent evaluation
    corruption. See 2026-04-24 persona schema migration."""
    for cls in _import_student_persona_classes():
        with pytest.raises(Exception) as exc_info:
            cls(
                persona_id="x",
                knowledge_level="y",
                description="z",
                emotional_profile="",
                behavioral_rules=[],
                **{legacy_key: {"finance": ["a"]}},
            )
        msg = str(exc_info.value)
        assert legacy_key in msg, (
            f"{cls.__module__}.{cls.__name__} should mention the legacy key in "
            f"the error; got: {msg}"
        )
        assert current_key in msg, (
            f"{cls.__module__}.{cls.__name__} should point to the replacement "
            f"key in the error; got: {msg}"
        )


def test_student_persona_accepts_current_keys() -> None:
    """Sanity: the validator must not block legitimate current-schema payloads."""
    for cls in _import_student_persona_classes():
        p = cls(
            persona_id="x",
            knowledge_level="y",
            description="z",
            familiar_concepts={"finance": ["a"]},
            unfamiliar_concepts={"finance": ["b"]},
            emotional_profile="curious_anxious",
            behavioral_rules=["rule"],
        )
        assert p.familiar_concepts == {"finance": ["a"]}
        assert p.unfamiliar_concepts == {"finance": ["b"]}
