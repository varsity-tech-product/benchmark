"""6D rubric builder with variant selection and [KNOWN]/[UNKNOWN] injection.

Loads rubric_6d.json, selects the correct scoring variant for each
dimension based on persona_id and task category, and substitutes
[KNOWN]/[UNKNOWN] tags with concept lists from the persona file.

Single source of truth: persona JSON files define known/unknown concepts
for both the student simulator and the rubric evaluation.

Usage::

    from server.eval.rubric_builder import build_rubric_text, load_6d_rubric

    rubric = load_6d_rubric()
    text = build_rubric_text(
        rubric, "D1_finance_adaptation",
        persona_id="developer_crossover",
        category="strategy",
    )
"""

import json
import logging
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_RUBRIC_DIR = Path(__file__).parent / "rubrics"
_PERSONA_DIR = Path(__file__).parents[2] / "personas"

# ──────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────

_rubric_6d: dict | None = None
_persona_cache: dict[str, dict] = {}


def load_6d_rubric() -> dict:
    global _rubric_6d
    if _rubric_6d is None:
        path = _RUBRIC_DIR / "rubric_6d.json"
        with open(path) as f:
            _rubric_6d = json.load(f)
    return _rubric_6d


def _load_persona(persona_id: str) -> dict:
    if persona_id not in _persona_cache:
        path = _PERSONA_DIR / f"{persona_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Persona file not found: {path}")
        with open(path) as f:
            _persona_cache[persona_id] = json.load(f)
    return _persona_cache[persona_id]


# ──────────────────────────────────────────────────────────────
# Variant selection
# ──────────────────────────────────────────────────────────────


def _select_variant(
    dim_data: dict,
    persona_id: str,
    category: Optional[str] = None,
) -> dict:
    """Select the correct scoring_guidance from a dimension's variants.

    For per_quadrant: matches persona_id against applies_to lists.
    For per_category: matches task category against applies_to lists.
    For universal: returns scoring_guidance directly.
    """
    scope = dim_data.get("scope", "universal")

    if scope == "universal":
        sg = dim_data.get("scoring_guidance", {})
        if not sg:
            raise ValueError("Universal dimension has empty scoring_guidance")
        return sg

    variants = dim_data.get("scoring_variants", {})
    if not variants:
        raise ValueError(f"Dimension has scope={scope} but no scoring_variants")

    if scope == "per_quadrant":
        for variant_name, variant_data in variants.items():
            if persona_id in variant_data.get("applies_to", []):
                sg = variant_data.get("scoring_guidance", {})
                if not sg:
                    raise ValueError(
                        f"Variant '{variant_name}' has empty scoring_guidance"
                    )
                return sg
        raise ValueError(
            f"No variant matches persona_id='{persona_id}' in {list(variants.keys())}"
        )

    if scope == "per_category":
        for variant_name, variant_data in variants.items():
            if category in variant_data.get("applies_to", []):
                sg = variant_data.get("scoring_guidance", {})
                if not sg:
                    raise ValueError(
                        f"Variant '{variant_name}' has empty scoring_guidance"
                    )
                return sg
        raise ValueError(
            f"No variant matches category='{category}' in {list(variants.keys())}"
        )

    raise ValueError(f"Unknown scope: {scope}")


# ──────────────────────────────────────────────────────────────
# [KNOWN]/[UNKNOWN] injection from persona files
# ──────────────────────────────────────────────────────────────

# Map dimension name → domain key in persona's known_concepts/unknown_concepts
_DIM_TO_DOMAIN = {
    "D1_finance_adaptation": "finance",
    "D2_code_adaptation": "code",
}


def _get_concept_lists(
    dimension_name: str,
    persona_id: str,
) -> tuple[list[str], list[str]] | None:
    """Get [KNOWN]/[UNKNOWN] concept lists from persona file.

    Returns (known_list, unknown_list) or None if the dimension
    doesn't use [KNOWN]/[UNKNOWN] tags.
    """
    domain = _DIM_TO_DOMAIN.get(dimension_name)
    if domain is None:
        return None  # D3-D6 don't use concept tags

    persona = _load_persona(persona_id)
    known = persona.get("known_concepts", {}).get(domain, [])
    unknown = persona.get("unknown_concepts", {}).get(domain, [])
    return known, unknown


def _inject_concepts(text: str, known: list[str], unknown: list[str]) -> str:
    """Replace [KNOWN] and [UNKNOWN] tags with formatted concept lists."""
    known_str = ", ".join(known)
    unknown_str = ", ".join(unknown)
    text = text.replace("[KNOWN]", f"[KNOWN: {known_str}]")
    text = text.replace("[UNKNOWN]", f"[UNKNOWN: {unknown_str}]")
    return text


# ──────────────────────────────────────────────────────────────
# Main builder
# ──────────────────────────────────────────────────────────────

_SCORE_LABELS = {
    "1": "Failure",
    "2": "Below Expectations",
    "3": "Adequate (Baseline)",
    "4": "Good",
    "5": "Excellent",
}

_DIM_LABELS = {
    "D1_finance_adaptation": "Finance Adaptation",
    "D2_code_adaptation": "Code Adaptation",
    "D3_pedagogical_method": "Pedagogical Method",
    "D4_instructional_accuracy": "Instructional Accuracy",
    "D5_empathetic_response": "Empathetic Response",
    "D6_safety_boundaries": "Safety Boundaries",
}


def build_rubric_text(
    rubric: dict,
    dimension_name: str,
    persona_id: str,
    category: Optional[str] = None,
) -> str:
    """Build the complete rubric block for injection into _SCORE_PROMPT.

    Steps:
    1. Select the correct scoring variant (per_quadrant/per_category/universal)
    2. Replace [KNOWN]/[UNKNOWN] tags with concept lists from persona file
    3. Format with score labels and dimension header

    Returns the formatted rubric string ready for prompt injection.
    """
    dim_data = rubric["dimensions"].get(dimension_name)
    if not dim_data:
        raise ValueError(f"Dimension '{dimension_name}' not found in rubric")

    # Step 1: Select variant
    scoring_guidance = _select_variant(dim_data, persona_id, category)

    # Step 2: Build score lines
    scoring_lines = []
    for score_key, description in sorted(
        scoring_guidance.items(), key=lambda x: int(x[0])
    ):
        label = _SCORE_LABELS.get(score_key, "")
        prefix = f"Score {score_key} — {label}" if label else f"Score {score_key}"
        scoring_lines.append(f"{prefix}: {description}")

    # Step 3: Inject [KNOWN]/[UNKNOWN] from persona file
    concept_lists = _get_concept_lists(dimension_name, persona_id)
    if concept_lists:
        known, unknown = concept_lists
        scoring_lines = [
            _inject_concepts(line, known, unknown) for line in scoring_lines
        ]

    # Step 4: Format header
    dim_label = _DIM_LABELS.get(dimension_name, dimension_name)
    score_keys = sorted(scoring_guidance.keys(), key=int)
    max_score = int(score_keys[-1])

    return f"## Dimension: {dim_label} (1-{max_score} scale)\n\n" + "\n".join(
        scoring_lines
    )


def get_max_score(rubric: dict, dimension_name: str) -> int:
    """Get the max score for a dimension from its scoring guidance."""
    dim_data = rubric["dimensions"].get(dimension_name, {})
    scope = dim_data.get("scope", "universal")

    if scope == "universal":
        keys = dim_data.get("scoring_guidance", {}).keys()
    else:
        variants = dim_data.get("scoring_variants", {})
        first = next(iter(variants.values()), {})
        keys = first.get("scoring_guidance", {}).keys()

    return max((int(k) for k in keys), default=5)
