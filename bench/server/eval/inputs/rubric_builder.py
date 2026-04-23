"""Rubric builder — loads rubric JSON, selects variants, formats for prompts.

Supports all rubric types:
- 6D (Tutor): per-quadrant/per-category variant selection + [KNOWN]/[UNKNOWN] injection
- QP (Process): task_planning, problem_solving
- QR (Result): result_judge

Usage::

    from server.eval.inputs.rubric_builder import load_rubric, build_eval_params

    # Generic (QP/QR)
    rubric = load_rubric("qp")
    params = build_eval_params(rubric, "task_planning")
    # → {"role": ..., "rules": ..., "criteria": ..., "max_score": 5}

    # 6D (Tutor) — preserved interface
    from server.eval.inputs.rubric_builder import load_6d_rubric, build_rubric_text
    rubric = load_6d_rubric()
    text = build_rubric_text(rubric, "D1_finance_adaptation",
                             persona_id="developer_crossover", category="strategy")
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

_log = logging.getLogger(__name__)

_EVAL_DIR = Path(__file__).resolve().parents[1]
_BENCH_ROOT = Path(__file__).resolve().parents[3]
_RUBRIC_DIR = _EVAL_DIR / "rubrics"
_PERSONA_DIR = _BENCH_ROOT / "personas"

# ──────────────────────────────────────────────────────────────
# Cache
# ──────────────────────────────────────────────────────────────

_rubric_6d: dict | None = None
_rubric_cache: dict[str, dict] = {}
_persona_cache: dict[str, dict] = {}


def _score_keys_from_dim(dim_data: dict) -> list[str]:
    """Return score keys from universal or variant-scoped guidance."""

    scope = dim_data.get("scope", "universal")
    if scope == "universal":
        keys = list((dim_data.get("scoring_guidance") or {}).keys())
    else:
        keys = []
        for variant in (dim_data.get("scoring_variants") or {}).values():
            keys.extend((variant.get("scoring_guidance") or {}).keys())
    return sorted({str(key) for key in keys}, key=int)


def build_rubric_metadata(
    rubric: dict,
    dimension_name: str,
    *,
    rubric_name: str | None = None,
    context_fields: list[str] | None = None,
    transcript_source: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build stable metadata for a judge prompt/output dimension."""

    dim_data = rubric["dimensions"].get(dimension_name)
    if not dim_data:
        raise ValueError(f"Dimension '{dimension_name}' not found in rubric")

    score_keys = _score_keys_from_dim(dim_data)
    rubric_version = str(
        dim_data.get("rubric_version") or rubric.get("version") or "unversioned"
    )
    rubric_stem = str(dim_data.get("rubric_id") or rubric.get("rubric_id") or "")
    if not rubric_stem:
        rubric_stem = str(rubric_name or rubric_version or "rubric")
    rubric_id = str(dim_data.get("rubric_id") or f"{rubric_stem}.{dimension_name}")

    metadata: dict[str, Any] = {
        "rubric_id": rubric_id,
        "rubric_version": rubric_version,
        "dimension": dimension_name,
        "score_scale": {
            "min": int(score_keys[0]) if score_keys else 1,
            "max": int(score_keys[-1]) if score_keys else 5,
            "type": "integer",
        },
        "score_anchor_keys": score_keys,
        "context_fields_included": list(context_fields or ["context"]),
        "transcript_source": transcript_source or "evaluation_context",
        "score_interpretation": (
            "Judge returns a raw integer score on the rubric scale; the "
            "runtime stores a normalized 0-1 score for aggregation."
        ),
    }
    if extra:
        metadata.update(extra)
    return metadata


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


# ──────────────────────────────────────────────────────────────
# Generic rubric loading (QP / QR / any future rubric)
# ──────────────────────────────────────────────────────────────


def load_rubric(name: str) -> dict:
    """Load a rubric JSON by name (e.g., "qp" → rubric_qp.json).

    Caches after first load.
    """
    if name not in _rubric_cache:
        path = _RUBRIC_DIR / f"rubric_{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Rubric file not found: {path}")
        with open(path) as f:
            _rubric_cache[name] = json.load(f)
    return _rubric_cache[name]


def _format_scoring_guidance(scoring_guidance: dict, label: str) -> str:
    """Format scoring_guidance dict into rubric text block.

    Same format as build_rubric_text output, usable as EwanConvGEval criteria.
    """
    scoring_lines = []
    for score_key, description in sorted(
        scoring_guidance.items(), key=lambda x: int(x[0])
    ):
        score_label = _SCORE_LABELS.get(score_key, "")
        prefix = (
            f"Score {score_key} — {score_label}"
            if score_label
            else f"Score {score_key}"
        )
        scoring_lines.append(f"{prefix}: {description}")

    score_keys = sorted(scoring_guidance.keys(), key=int)
    max_score = int(score_keys[-1])
    return f"## Dimension: {label} (1-{max_score} scale)\n\n" + "\n".join(scoring_lines)


def build_eval_params(
    rubric: dict,
    dimension_name: str,
    *,
    rubric_name: str | None = None,
    context_fields: list[str] | None = None,
    transcript_source: str | None = None,
) -> dict:
    """Extract evaluation parameters from a rubric for EwanConvGEval.

    Returns dict with keys: role, rules, criteria, max_score.
    Works for any rubric JSON that follows the standard schema
    (rubric_qp.json, rubric_qr.json, or rubric_6d.json).

    The ``role`` field is read from dimension level first, then rubric
    top level. The ``rules`` field follows the same precedence.
    """
    dim_data = rubric["dimensions"].get(dimension_name)
    if not dim_data:
        raise ValueError(f"Dimension '{dimension_name}' not found in rubric")

    # Role: dimension-level > rubric top-level
    role = dim_data.get("role", rubric.get("role", ""))

    # Rules: dimension-level > rubric top-level. list → formatted string
    rules_list = dim_data.get("rules", rubric.get("rules", []))
    if isinstance(rules_list, list):
        rules = "\n".join(f"- {r}" for r in rules_list)
    else:
        rules = str(rules_list)

    # Scoring guidance → formatted criteria text
    sg = dim_data.get("scoring_guidance", {})
    if not sg:
        raise ValueError(f"Dimension '{dimension_name}' has no scoring_guidance")

    label = dim_data.get("label", dimension_name)
    criteria = _format_scoring_guidance(sg, label)

    # Max score
    max_score = max((int(k) for k in sg.keys()), default=5)

    return {
        "role": role,
        "rules": rules,
        "criteria": criteria,
        "max_score": max_score,
        "rubric_metadata": build_rubric_metadata(
            rubric,
            dimension_name,
            rubric_name=rubric_name,
            context_fields=context_fields,
            transcript_source=transcript_source,
        ),
    }
