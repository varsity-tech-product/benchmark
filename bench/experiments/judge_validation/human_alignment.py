"""Human expert alignment metrics for judge validation."""

from __future__ import annotations

import csv
import html
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

from .report import _raw_score, _record_dimension, _record_rubric_id

HUMAN_LABELS_VERSION = "judge_validation_human_labels_v1"
HUMAN_ALIGNMENT_STATS_VERSION = "judge_validation_human_alignment_stats_v1"
HUMAN_WITHIN_ONE_TARGET = 0.85
HUMAN_PASS_FAIL_TARGET = 0.9
LARGE_DISAGREEMENT_DELTA = 2.0

REQUIRED_LABEL_FIELDS = {
    "sample_id",
    "rubric_id",
    "dimension",
    "human_score",
    "confidence",
    "human_rationale",
    "reviewer_id",
}

CSV_HEADER_ALIASES = {
    "timestamp": ("timestamp", "submission_time", "submitted_at"),
    "reviewer_id": ("reviewer_id", "reviewer", "reviewer_email", "email_address"),
    "sample_id": ("sample_id", "sample", "validation_sample_id"),
    "transcript_id": ("transcript_id", "transcript", "conversation_id"),
    "rubric_id": ("rubric_id", "registry_rubric_id", "rubric"),
    "dimension": ("dimension", "judge_dimension"),
    "human_score": ("human_score", "score", "human_rating"),
    "confidence": ("confidence", "reviewer_confidence"),
    "human_rationale": ("human_rationale", "rationale", "reviewer_rationale"),
    "evidence_spans": ("evidence_spans", "evidence", "supporting_evidence"),
    "failure_tags": ("failure_tags", "tags", "failure_modes"),
    "label_version": ("label_version", "version"),
    "notes": ("notes", "reviewer_notes"),
}

CANONICAL_FAILURE_TAGS = [
    "quant_error",
    "code_error",
    "missing_task_completion",
    "hallucinated_tool_output",
    "poor_adaptation",
    "answer_dump",
    "unsafe_financial_advice",
    "unclear_rubric",
    "other",
]


def _safe_mean(values: list[float]) -> float | None:
    return round(mean(values), 4) if values else None


def _rate(values: list[bool]) -> float | None:
    if not values:
        return None
    return round(sum(1 for value in values if value) / len(values), 4)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")


def _split_list(value: Any, *, split_commas: bool = False) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    pattern = r"[\n;|]+"
    if split_commas:
        pattern = r"[\n;|,]+"
    return [part.strip() for part in re.split(pattern, text) if part.strip()]


def _normalize_confidence(value: str) -> str:
    text = value.strip().lower()
    tokens = set(re.findall(r"[a-z]+", text))
    for confidence in ("low", "medium", "high"):
        if confidence in tokens or text == confidence:
            return confidence
    if "低" in text:
        return "low"
    if "中" in text:
        return "medium"
    if "高" in text:
        return "high"
    raise ValueError(f"confidence must be low, medium, or high: {value}")


def _normalize_failure_tags(values: list[str]) -> list[str]:
    normalized_tags: list[str] = []
    for value in values:
        normalized = _normalize_header(value)
        match = next(
            (
                tag
                for tag in CANONICAL_FAILURE_TAGS
                if normalized == tag
                or normalized.startswith(f"{tag}_")
                or normalized.endswith(f"_{tag}")
                or f"_{tag}_" in normalized
            ),
            normalized,
        )
        if match and match not in normalized_tags:
            normalized_tags.append(match)
    return normalized_tags


def _as_int_score(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError(f"human_score must be an integer from 1 to 5: {value}")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"human_score must be an integer from 1 to 5: {value}") from exc
    if not numeric.is_integer():
        raise ValueError(f"human_score must be an integer from 1 to 5: {value}")
    score = int(numeric)
    if score < 1 or score > 5:
        raise ValueError(f"human_score must be an integer from 1 to 5: {value}")
    return score


def _required_text(raw: dict[str, Any], field: str) -> str:
    value = raw.get(field)
    if value is None or str(value).strip() == "":
        raise ValueError(f"human label is missing required field: {field}")
    return str(value).strip()


def normalize_human_label(
    raw: dict[str, Any],
    *,
    default_timestamp: str | None = None,
) -> dict[str, Any]:
    """Return a canonical human label record."""

    missing = [
        field
        for field in sorted(REQUIRED_LABEL_FIELDS)
        if raw.get(field) is None or str(raw.get(field)).strip() == ""
    ]
    if missing:
        raise ValueError(f"human label is missing required fields: {missing}")

    sample_id = _required_text(raw, "sample_id")
    confidence = _normalize_confidence(_required_text(raw, "confidence"))
    label = {
        "sample_id": sample_id,
        "transcript_id": str(raw.get("transcript_id") or sample_id).strip(),
        "rubric_id": _required_text(raw, "rubric_id"),
        "dimension": _required_text(raw, "dimension"),
        "human_score": _as_int_score(raw.get("human_score")),
        "confidence": confidence,
        "human_rationale": _required_text(raw, "human_rationale"),
        "evidence_spans": _split_list(raw.get("evidence_spans")),
        "failure_tags": _normalize_failure_tags(
            _split_list(raw.get("failure_tags"), split_commas=True)
        ),
        "reviewer_id": _required_text(raw, "reviewer_id"),
        "label_version": str(raw.get("label_version") or "v1").strip(),
        "timestamp": str(
            raw.get("timestamp") or default_timestamp or _utc_now()
        ).strip(),
    }
    notes = str(raw.get("notes") or "").strip()
    if notes:
        label["notes"] = notes
    return label


def _extract_csv_value(row: dict[str, str], canonical_field: str) -> str:
    aliases = CSV_HEADER_ALIASES[canonical_field]
    if canonical_field == "reviewer_id":
        for alias in ("reviewer_id", "reviewer", "reviewer_email"):
            if alias in row:
                return row[alias]
        for alias in ("reviewer_id", "reviewer_email"):
            for key, value in row.items():
                if (
                    key.startswith(f"{alias}_")
                    or key.endswith(f"_{alias}")
                    or f"_{alias}_" in key
                ):
                    return value
        return row.get("email_address", "")

    for alias in aliases:
        if alias in row:
            return row[alias]
    for alias in aliases:
        if alias == "reviewer":
            continue
        for key, value in row.items():
            if (
                key.startswith(f"{alias}_")
                or key.endswith(f"_{alias}")
                or f"_{alias}_" in key
            ):
                return value
    return ""


def labels_from_csv_rows(
    rows: Iterable[dict[str, str]],
    *,
    default_timestamp: str | None = None,
) -> list[dict[str, Any]]:
    """Normalize Google Form CSV rows into canonical label records."""

    labels = []
    for row in rows:
        normalized_row = {_normalize_header(key): value for key, value in row.items()}
        raw = {
            field: _extract_csv_value(normalized_row, field)
            for field in CSV_HEADER_ALIASES
        }
        labels.append(normalize_human_label(raw, default_timestamp=default_timestamp))
    return labels


def convert_csv_to_human_labels(
    csv_path: Path,
    *,
    default_timestamp: str | None = None,
) -> dict[str, Any]:
    """Read a reviewer CSV export and return the canonical JSON payload."""

    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        labels = labels_from_csv_rows(
            csv.DictReader(fh),
            default_timestamp=default_timestamp,
        )
    return {
        "version": HUMAN_LABELS_VERSION,
        "generated_at": default_timestamp or _utc_now(),
        "labels": labels,
    }


def load_human_labels(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        raw_labels = payload
    elif isinstance(payload, dict) and isinstance(payload.get("labels"), list):
        raw_labels = payload["labels"]
    else:
        raise ValueError("human labels must be a list or an object with labels")
    return [normalize_human_label(label) for label in raw_labels]


def load_sample_id_map(path: Path) -> dict[str, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        rows = payload.get("mappings", [])
    elif isinstance(payload, list):
        rows = payload
    else:
        raise ValueError("sample ID map must be a list or an object with mappings")
    return {
        str(row.get("review_sample_id")): str(row.get("original_sample_id"))
        for row in rows
        if row.get("review_sample_id") and row.get("original_sample_id")
    }


def _apply_sample_id_map(
    label: dict[str, Any],
    sample_id_map: dict[str, str],
) -> dict[str, Any]:
    if not sample_id_map:
        return label
    review_sample_id = str(label.get("sample_id") or "")
    original_sample_id = sample_id_map.get(review_sample_id)
    if not original_sample_id:
        return label
    mapped = dict(label)
    mapped["review_sample_id"] = review_sample_id
    mapped["sample_id"] = original_sample_id
    if str(mapped.get("transcript_id") or review_sample_id) == review_sample_id:
        mapped["transcript_id"] = original_sample_id
    return mapped


def _nearest_score(score: float) -> int:
    return max(1, min(5, int(score + 0.5)))


def _aggregate_alignment(rows: list[dict[str, Any]]) -> dict[str, Any]:
    deltas = [float(row["absolute_delta"]) for row in rows]
    signed = [float(row["signed_delta"]) for row in rows]
    return {
        "labels": len(rows),
        "exact_agreement": _rate([bool(row["exact_agreement"]) for row in rows]),
        "within_one_agreement": _rate(
            [bool(row["within_one_agreement"]) for row in rows]
        ),
        "mean_absolute_delta": _safe_mean(deltas),
        "mean_signed_delta": _safe_mean(signed),
        "pass_fail_agreement": _rate(
            [bool(row["pass_fail_agreement"]) for row in rows]
        ),
    }


def _grouped_alignment(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    return {
        key: _aggregate_alignment(value)
        for key, value in sorted(grouped.items())
    }


def _reviewer_coverage(labels: list[dict[str, Any]]) -> dict[str, Any]:
    by_reviewer: dict[str, int] = defaultdict(int)
    by_confidence: dict[str, int] = defaultdict(int)
    for label in labels:
        by_reviewer[str(label.get("reviewer_id", "unknown"))] += 1
        by_confidence[str(label.get("confidence", "unknown"))] += 1
    return {
        "reviewers": dict(sorted(by_reviewer.items())),
        "confidence": dict(sorted(by_confidence.items())),
    }


def _group_labels_by_sample_dim(
    labels: list[dict[str, Any]],
) -> dict[tuple[str, str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for label in labels:
        key = (
            str(label.get("sample_id", "")),
            str(label.get("rubric_id", "")),
            str(label.get("dimension", "")),
        )
        grouped[key].append(label)
    return grouped


def _reviewer_pair_key(a: str, b: str) -> str:
    return " | ".join(sorted([a, b]))


def compute_inter_rater_agreement(
    labels: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compute reviewer-vs-reviewer agreement across overlapping labels.

    For each (sample_id, rubric_id, dimension) group with ≥ 2 reviewers,
    compute all unordered reviewer-pair deltas. Aggregate overall, by
    dimension, and by reviewer pair. Samples with only 1 reviewer are
    skipped (inter-rater is only defined on overlap).

    Output shape::

        {
            "counts": {
                "overlapping_groups": int,
                "reviewer_pair_comparisons": int,
                "unique_reviewers": int,
            },
            "overall": {"exact_agreement": ..., ...},
            "by_dimension": {"D1_...": {...}, ...},
            "by_reviewer_pair": {"a | b": {...}, ...},
            "disagreements": [{...}],  # top entries with |Δ| ≥ 2
        }
    """

    grouped = _group_labels_by_sample_dim(labels)
    overlap_rows: list[dict[str, Any]] = []
    disagreements: list[dict[str, Any]] = []
    overlapping_group_count = 0

    for (sample_id, rubric_id, dimension), group in grouped.items():
        distinct_reviewers = {
            str(label.get("reviewer_id", "unknown")) for label in group
        }
        if len(distinct_reviewers) < 2:
            continue
        overlapping_group_count += 1
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                reviewer_a = str(a.get("reviewer_id", "unknown"))
                reviewer_b = str(b.get("reviewer_id", "unknown"))
                if reviewer_a == reviewer_b:
                    # Same reviewer submitted multiple labels — not a
                    # cross-reviewer comparison.
                    continue
                score_a = int(a.get("human_score", 0))
                score_b = int(b.get("human_score", 0))
                signed = score_a - score_b
                absolute = abs(signed)
                row = {
                    "sample_id": sample_id,
                    "rubric_id": rubric_id,
                    "dimension": dimension,
                    "reviewer_a": reviewer_a,
                    "reviewer_b": reviewer_b,
                    "reviewer_pair": _reviewer_pair_key(reviewer_a, reviewer_b),
                    "score_a": score_a,
                    "score_b": score_b,
                    "signed_delta": signed,
                    "absolute_delta": absolute,
                    "exact_agreement": absolute == 0,
                    "within_one_agreement": absolute <= 1,
                }
                overlap_rows.append(row)
                if absolute >= LARGE_DISAGREEMENT_DELTA:
                    disagreements.append(row)

    def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {
                "comparisons": 0,
                "exact_agreement": None,
                "within_one_agreement": None,
                "mean_absolute_delta": None,
            }
        abs_vals = [int(row["absolute_delta"]) for row in rows]
        return {
            "comparisons": len(rows),
            "exact_agreement": _rate([row["exact_agreement"] for row in rows]),
            "within_one_agreement": _rate(
                [row["within_one_agreement"] for row in rows]
            ),
            "mean_absolute_delta": _safe_mean([float(v) for v in abs_vals]),
        }

    by_dimension = {
        dimension: _aggregate(rows)
        for dimension, rows in sorted(
            _group_rows_by(overlap_rows, "dimension").items()
        )
    }
    by_reviewer_pair = {
        pair: _aggregate(rows)
        for pair, rows in sorted(
            _group_rows_by(overlap_rows, "reviewer_pair").items()
        )
    }
    unique_reviewers = {
        reviewer
        for row in overlap_rows
        for reviewer in (row["reviewer_a"], row["reviewer_b"])
    }
    return {
        "counts": {
            "overlapping_groups": overlapping_group_count,
            "reviewer_pair_comparisons": len(overlap_rows),
            "unique_reviewers": len(unique_reviewers),
        },
        "overall": _aggregate(overlap_rows),
        "by_dimension": by_dimension,
        "by_reviewer_pair": by_reviewer_pair,
        "disagreements": disagreements[:25],
    }


def _group_rows_by(
    rows: list[dict[str, Any]],
    field: str,
) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(field) or "unknown")].append(row)
    return grouped


def compute_judge_vs_reviewer_mean(
    comparisons: list[dict[str, Any]],
) -> dict[str, Any]:
    """Aggregate judge deltas using the mean across reviewers as ground truth.

    ``comparisons`` is the per-label row list from
    :func:`compute_human_alignment_stats`. For each (sample_id,
    rubric_id, dimension), average the human scores across reviewers
    and recompute judge vs. that mean. Only groups with ≥ 2 reviewers
    contribute; single-reviewer groups would collapse back to the
    label-level numbers and are excluded so the slice is interpretable.
    """

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in comparisons:
        key = (
            str(row.get("sample_id", "")),
            str(row.get("rubric_id", "")),
            str(row.get("dimension", "")),
        )
        grouped[key].append(row)

    multi_reviewer_rows: list[dict[str, Any]] = []
    for (sample_id, rubric_id, dimension), rows in grouped.items():
        # Collapse duplicate submissions from the same reviewer first so
        # the "reviewer mean" isn't inflated by one reviewer who labelled
        # the same sample twice.
        per_reviewer: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            reviewer_id = str(row.get("reviewer_id", ""))
            per_reviewer[reviewer_id].append(float(row["human_score"]))
        if len(per_reviewer) < 2:
            continue
        per_reviewer_means = {
            reviewer: sum(scores) / len(scores)
            for reviewer, scores in per_reviewer.items()
        }
        score_values = list(per_reviewer_means.values())
        human_mean = sum(score_values) / len(score_values)
        human_span = max(score_values) - min(score_values)
        judge_mean = float(rows[0]["judge_mean_score"])
        signed = round(judge_mean - human_mean, 4)
        absolute = round(abs(signed), 4)
        nearest_judge = _nearest_score(judge_mean)
        nearest_human_mean = _nearest_score(human_mean)
        multi_reviewer_rows.append(
            {
                "sample_id": sample_id,
                "rubric_id": rubric_id,
                "dimension": dimension,
                "reviewers": sorted(per_reviewer.keys()),
                "human_scores_per_reviewer": {
                    reviewer: round(score, 4)
                    for reviewer, score in per_reviewer_means.items()
                },
                "human_mean_score": round(human_mean, 4),
                "human_score_span": round(human_span, 4),
                "judge_mean_score": round(judge_mean, 4),
                "nearest_judge_score": nearest_judge,
                "nearest_human_mean_score": nearest_human_mean,
                "signed_delta": signed,
                "absolute_delta": absolute,
                "exact_agreement": nearest_judge == nearest_human_mean,
                "within_one_agreement": absolute <= 1,
            }
        )

    def _aggregate_multi(rows: list[dict[str, Any]]) -> dict[str, Any]:
        if not rows:
            return {
                "sample_dim_groups": 0,
                "exact_agreement": None,
                "within_one_agreement": None,
                "mean_absolute_delta": None,
                "mean_signed_delta": None,
            }
        return {
            "sample_dim_groups": len(rows),
            "exact_agreement": _rate([row["exact_agreement"] for row in rows]),
            "within_one_agreement": _rate(
                [row["within_one_agreement"] for row in rows]
            ),
            "mean_absolute_delta": _safe_mean(
                [float(row["absolute_delta"]) for row in rows]
            ),
            "mean_signed_delta": _safe_mean(
                [float(row["signed_delta"]) for row in rows]
            ),
        }

    by_dimension = {
        dimension: _aggregate_multi(rows)
        for dimension, rows in sorted(
            _group_rows_by(multi_reviewer_rows, "dimension").items()
        )
    }
    distinct_samples = len({row["sample_id"] for row in multi_reviewer_rows})
    return {
        "counts": {
            "sample_dim_groups": len(multi_reviewer_rows),
            "distinct_samples": distinct_samples,
        },
        "overall": _aggregate_multi(multi_reviewer_rows),
        "by_dimension": by_dimension,
        "sample_dim_groups": multi_reviewer_rows,
    }


def compute_human_alignment_stats(
    *,
    corpus: dict[str, Any],
    records: list[dict[str, Any]],
    labels: list[dict[str, Any]],
    pass_threshold: float | None = None,
    sample_id_map: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Compare successful judge scores against human expert labels."""

    threshold = float(pass_threshold or corpus.get("pass_threshold") or 3)
    normalized_labels = [
        _apply_sample_id_map(normalize_human_label(label), sample_id_map or {})
        for label in labels
    ]
    item_index = {
        str(item.get("sample_id", "")): item
        for item in corpus.get("items", [])
    }

    judge_scores: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for record in records:
        if record.get("status") != "success":
            continue
        score = _raw_score(record)
        if score is None:
            continue
        key = (
            str(record.get("sample_id", "")),
            _record_rubric_id(record),
            _record_dimension(record),
        )
        judge_scores[key].append(float(score))

    comparisons: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for label in normalized_labels:
        key = (label["sample_id"], label["rubric_id"], label["dimension"])
        scores = judge_scores.get(key, [])
        item = item_index.get(label["sample_id"], {})
        if not scores:
            missing.append(
                {
                    "sample_id": label["sample_id"],
                    "rubric_id": label["rubric_id"],
                    "dimension": label["dimension"],
                    "reviewer_id": label["reviewer_id"],
                }
            )
            continue

        judge_mean = _safe_mean(scores)
        assert judge_mean is not None
        human_score = int(label["human_score"])
        signed_delta = round(float(judge_mean) - human_score, 4)
        absolute_delta = round(abs(signed_delta), 4)
        row = {
            "sample_id": label["sample_id"],
            "transcript_id": label["transcript_id"],
            "rubric_id": label["rubric_id"],
            "dimension": label["dimension"],
            "task_id": item.get("task_id"),
            "category": item.get("category"),
            "persona_id": item.get("persona_id"),
            "transcript_source": item.get("transcript_source"),
            "reviewer_id": label["reviewer_id"],
            "review_sample_id": label.get("review_sample_id"),
            "confidence": label["confidence"],
            "human_score": human_score,
            "judge_mean_score": judge_mean,
            "judge_score_count": len(scores),
            "nearest_judge_score": _nearest_score(float(judge_mean)),
            "signed_delta": signed_delta,
            "absolute_delta": absolute_delta,
            "exact_agreement": _nearest_score(float(judge_mean)) == human_score,
            "within_one_agreement": absolute_delta <= 1,
            "human_pass": human_score >= threshold,
            "judge_pass": float(judge_mean) >= threshold,
            "pass_fail_agreement": (human_score >= threshold)
            == (float(judge_mean) >= threshold),
            "human_rationale": label["human_rationale"],
            "evidence_spans": label["evidence_spans"],
            "failure_tags": label["failure_tags"],
        }
        if label.get("notes"):
            row["notes"] = label["notes"]
        comparisons.append(row)

    large_disagreements = [
        row
        for row in comparisons
        if float(row["absolute_delta"]) >= LARGE_DISAGREEMENT_DELTA
    ]
    large_disagreements_documented = all(
        row.get("evidence_spans") and row.get("failure_tags")
        for row in large_disagreements
    )
    overall = _aggregate_alignment(comparisons)
    gate_passed = bool(
        comparisons
        and not missing
        and (overall["within_one_agreement"] or 0) >= HUMAN_WITHIN_ONE_TARGET
        and (overall["pass_fail_agreement"] or 0) >= HUMAN_PASS_FAIL_TARGET
        and large_disagreements_documented
    )

    weak_dimensions = [
        {
            "dimension": dimension,
            **metrics,
        }
        for dimension, metrics in _grouped_alignment(
            comparisons,
            "dimension",
        ).items()
        if (metrics["within_one_agreement"] or 0) < HUMAN_WITHIN_ONE_TARGET
        or (metrics["pass_fail_agreement"] or 0) < HUMAN_PASS_FAIL_TARGET
    ]

    inter_rater = compute_inter_rater_agreement(normalized_labels)
    judge_vs_mean = compute_judge_vs_reviewer_mean(comparisons)

    return {
        "version": HUMAN_ALIGNMENT_STATS_VERSION,
        "pass_threshold": threshold,
        "targets": {
            "human_within_one_agreement": HUMAN_WITHIN_ONE_TARGET,
            "human_pass_fail_agreement": HUMAN_PASS_FAIL_TARGET,
            "large_disagreement_delta": LARGE_DISAGREEMENT_DELTA,
        },
        "counts": {
            "labels": len(normalized_labels),
            "comparable_labels": len(comparisons),
            "missing_judge_comparisons": len(missing),
            "large_disagreements": len(large_disagreements),
        },
        "reviewer_coverage": _reviewer_coverage(normalized_labels),
        "overall": overall,
        "slices": {
            "by_dimension": _grouped_alignment(comparisons, "dimension"),
            "by_task_category": _grouped_alignment(comparisons, "category"),
            "by_persona_id": _grouped_alignment(comparisons, "persona_id"),
            "by_transcript_source": _grouped_alignment(
                comparisons,
                "transcript_source",
            ),
        },
        "inter_rater_agreement": inter_rater,
        "judge_vs_reviewer_mean": judge_vs_mean,
        "stage3_gate": {
            "status": "pass" if gate_passed else "needs_review",
            "large_disagreements_documented": large_disagreements_documented,
            "weak_dimensions": weak_dimensions,
        },
        "comparisons": comparisons,
        "missing_judge_comparisons": missing,
        "large_disagreement_examples": large_disagreements[:25],
    }


def _table(headers: list[str], rows: list[list[Any]]) -> str:
    head = "".join(f"<th>{html.escape(str(header))}</th>" for header in headers)
    body = "\n".join(
        "<tr>"
        + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row)
        + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def markdown_human_alignment_report(
    stats: dict[str, Any],
    *,
    run_id: str = "",
) -> str:
    overall = stats["overall"]
    counts = stats["counts"]
    lines = [
        "# Human Alignment Report",
        "",
        f"- Judge run ID: {run_id or 'unrecorded'}",
        f"- Human labels: {counts['labels']}",
        f"- Comparable labels: {counts['comparable_labels']}",
        f"- Missing judge comparisons: {counts['missing_judge_comparisons']}",
        f"- Exact agreement: {overall['exact_agreement']}",
        f"- Within-one agreement: {overall['within_one_agreement']}",
        f"- Mean absolute delta: {overall['mean_absolute_delta']}",
        f"- Pass/fail agreement: {overall['pass_fail_agreement']}",
        f"- Stage 3 gate: {stats['stage3_gate']['status']}",
        "",
        "## Dimension Metrics",
        "",
        "| Dimension | Labels | Exact | Within One | Mean Abs Delta | Pass/Fail |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for dimension, metrics in stats["slices"]["by_dimension"].items():
        lines.append(
            "| {dimension} | {labels} | {exact} | {within_one} | {delta} | {pf} |".format(
                dimension=dimension,
                labels=metrics["labels"],
                exact=metrics["exact_agreement"],
                within_one=metrics["within_one_agreement"],
                delta=metrics["mean_absolute_delta"],
                pf=metrics["pass_fail_agreement"],
            )
        )

    lines.extend(
        [
            "",
            "## Large Disagreements",
            "",
            "| Sample | Rubric | Human | Judge Mean | Delta | Tags |",
            "| --- | --- | ---: | ---: | ---: | --- |",
        ]
    )
    for row in stats["large_disagreement_examples"]:
        lines.append(
            "| {sample_id} | {rubric} | {human} | {judge} | {delta} | {tags} |".format(
                sample_id=row.get("sample_id"),
                rubric=row.get("rubric_id"),
                human=row.get("human_score"),
                judge=row.get("judge_mean_score"),
                delta=row.get("absolute_delta"),
                tags=", ".join(row.get("failure_tags") or []),
            )
        )

    lines.extend(
        [
            "",
            "## Missing Judge Comparisons",
            "",
            "| Sample | Rubric | Dimension | Reviewer |",
            "| --- | --- | --- | --- |",
        ]
    )
    for row in stats["missing_judge_comparisons"]:
        lines.append(
            "| {sample_id} | {rubric} | {dimension} | {reviewer} |".format(
                sample_id=row.get("sample_id"),
                rubric=row.get("rubric_id"),
                dimension=row.get("dimension"),
                reviewer=row.get("reviewer_id"),
            )
        )

    lines.extend(["", "## Reviewer Coverage", ""])
    for reviewer_id, count in stats["reviewer_coverage"]["reviewers"].items():
        lines.append(f"- {reviewer_id}: {count}")

    ir = stats.get("inter_rater_agreement") or {}
    ir_counts = ir.get("counts") or {}
    if ir_counts.get("reviewer_pair_comparisons"):
        overall_ir = ir.get("overall") or {}
        lines.extend(
            [
                "",
                "## Inter-rater Agreement (reviewers vs. each other)",
                "",
                f"- Overlapping sample/dim groups: {ir_counts.get('overlapping_groups')}",
                f"- Reviewer-pair comparisons: {ir_counts.get('reviewer_pair_comparisons')}",
                f"- Unique reviewers: {ir_counts.get('unique_reviewers')}",
                f"- Overall exact: {overall_ir.get('exact_agreement')}",
                f"- Overall within-one: {overall_ir.get('within_one_agreement')}",
                f"- Overall mean abs delta: {overall_ir.get('mean_absolute_delta')}",
                "",
                "| Dimension | Comparisons | Exact | Within One | Mean Abs Delta |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for dimension, metrics in (ir.get("by_dimension") or {}).items():
            lines.append(
                "| {dim} | {n} | {exact} | {within_one} | {delta} |".format(
                    dim=dimension,
                    n=metrics.get("comparisons"),
                    exact=metrics.get("exact_agreement"),
                    within_one=metrics.get("within_one_agreement"),
                    delta=metrics.get("mean_absolute_delta"),
                )
            )
        lines.extend(
            [
                "",
                "### Inter-rater disagreements (abs delta ≥ "
                f"{int(LARGE_DISAGREEMENT_DELTA)})",
                "",
                "| Sample | Dimension | Reviewer A | Score A | Reviewer B | Score B | Abs Δ |",
                "| --- | --- | --- | ---: | --- | ---: | ---: |",
            ]
        )
        for row in ir.get("disagreements") or []:
            lines.append(
                "| {sid} | {dim} | {ra} | {sa} | {rb} | {sb} | {delta} |".format(
                    sid=row.get("sample_id"),
                    dim=row.get("dimension"),
                    ra=row.get("reviewer_a"),
                    sa=row.get("score_a"),
                    rb=row.get("reviewer_b"),
                    sb=row.get("score_b"),
                    delta=row.get("absolute_delta"),
                )
            )

    jvm = stats.get("judge_vs_reviewer_mean") or {}
    jvm_counts = jvm.get("counts") or {}
    if jvm_counts.get("sample_dim_groups"):
        overall_jvm = jvm.get("overall") or {}
        lines.extend(
            [
                "",
                "## Judge vs. reviewer MEAN (multi-reviewer groups only)",
                "",
                f"- Sample/dim groups: {jvm_counts.get('sample_dim_groups')}",
                f"- Distinct samples: {jvm_counts.get('distinct_samples')}",
                f"- Overall exact: {overall_jvm.get('exact_agreement')}",
                f"- Overall within-one: {overall_jvm.get('within_one_agreement')}",
                f"- Overall mean abs delta: {overall_jvm.get('mean_absolute_delta')}",
                f"- Overall mean signed delta: {overall_jvm.get('mean_signed_delta')}",
                "",
                "| Dimension | Groups | Within One | Mean Abs Delta | Mean Signed Delta |",
                "| --- | ---: | ---: | ---: | ---: |",
            ]
        )
        for dimension, metrics in (jvm.get("by_dimension") or {}).items():
            lines.append(
                "| {dim} | {n} | {within_one} | {abs_delta} | {signed} |".format(
                    dim=dimension,
                    n=metrics.get("sample_dim_groups"),
                    within_one=metrics.get("within_one_agreement"),
                    abs_delta=metrics.get("mean_absolute_delta"),
                    signed=metrics.get("mean_signed_delta"),
                )
            )
    lines.append("")
    return "\n".join(lines)


def html_human_alignment_report(
    stats: dict[str, Any],
    *,
    run_id: str = "",
) -> str:
    overall = stats["overall"]
    counts = stats["counts"]
    dimension_rows = [
        [
            dimension,
            metrics["labels"],
            metrics["exact_agreement"],
            metrics["within_one_agreement"],
            metrics["mean_absolute_delta"],
            metrics["pass_fail_agreement"],
        ]
        for dimension, metrics in stats["slices"]["by_dimension"].items()
    ]
    disagreement_rows = [
        [
            row.get("sample_id"),
            row.get("rubric_id"),
            row.get("human_score"),
            row.get("judge_mean_score"),
            row.get("absolute_delta"),
            ", ".join(row.get("failure_tags") or []),
        ]
        for row in stats["large_disagreement_examples"]
    ]
    missing_rows = [
        [
            row.get("sample_id"),
            row.get("rubric_id"),
            row.get("dimension"),
            row.get("reviewer_id"),
        ]
        for row in stats["missing_judge_comparisons"]
    ]
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Human Alignment</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 32px; color: #1f2933; }}
    h1, h2 {{ color: #102a43; }}
    .cards {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 20px 0; }}
    .card {{ border: 1px solid #d9e2ec; border-radius: 8px; padding: 14px; background: #f8fafc; }}
    .metric {{ font-size: 26px; font-weight: 700; color: #1864ab; }}
    table {{ border-collapse: collapse; width: 100%; margin: 12px 0 28px; font-size: 14px; }}
    th, td {{ border: 1px solid #d9e2ec; padding: 8px; text-align: left; vertical-align: top; }}
    th {{ background: #e7f5ff; }}
  </style>
</head>
<body>
  <h1>Human Alignment</h1>
  <p>Judge run ID: <code>{html.escape(run_id or "unrecorded")}</code></p>
  <div class="cards">
    <div class="card"><div>Human Labels</div><div class="metric">{counts["labels"]}</div></div>
    <div class="card"><div>Comparable Labels</div><div class="metric">{counts["comparable_labels"]}</div></div>
    <div class="card"><div>Exact Agreement</div><div class="metric">{overall["exact_agreement"]}</div></div>
    <div class="card"><div>Within-One</div><div class="metric">{overall["within_one_agreement"]}</div></div>
    <div class="card"><div>Mean Abs Delta</div><div class="metric">{overall["mean_absolute_delta"]}</div></div>
    <div class="card"><div>Pass/Fail Agreement</div><div class="metric">{overall["pass_fail_agreement"]}</div></div>
    <div class="card"><div>Stage 3 Gate</div><div class="metric">{stats["stage3_gate"]["status"]}</div></div>
  </div>
  <h2>Dimension Metrics</h2>
  {_table(["Dimension", "Labels", "Exact", "Within One", "Mean Abs Delta", "Pass/Fail"], dimension_rows)}
  <h2>Large Disagreements</h2>
  {_table(["Sample", "Rubric", "Human", "Judge Mean", "Delta", "Tags"], disagreement_rows)}
  <h2>Missing Judge Comparisons</h2>
  {_table(["Sample", "Rubric", "Dimension", "Reviewer"], missing_rows)}
</body>
</html>
"""


def write_human_alignment_reports(
    *,
    stats: dict[str, Any],
    run_id: str,
    output_dir: Path,
) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stats_path = output_dir / "human_alignment_stats.json"
    md_path = output_dir / "human_alignment_report.md"
    html_path = output_dir / "human_alignment_report.html"
    stats_path.write_text(
        json.dumps(stats, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    md_path.write_text(
        markdown_human_alignment_report(stats, run_id=run_id),
        encoding="utf-8",
    )
    html_path.write_text(
        html_human_alignment_report(stats, run_id=run_id),
        encoding="utf-8",
    )
    return {
        "stats": str(stats_path),
        "markdown": str(md_path),
        "html": str(html_path),
    }
