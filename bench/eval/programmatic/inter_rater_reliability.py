"""Inter-rater reliability helpers for human review annotations."""

from __future__ import annotations

from itertools import combinations
from typing import Any


DEFAULT_SCORE_LABELS = (1, 2, 3, 4, 5)


def compute_inter_rater_reliability(
    records: list[dict[str, Any]],
    *,
    min_reviewers: int = 3,
    labels: tuple[int, ...] = DEFAULT_SCORE_LABELS,
) -> dict[str, Any]:
    """Compute reviewer agreement for score-bound human review records.

    Records are collapsed to the latest submission per reviewer. The overall
    Cohen kappa is computed over common criterion vectors for every reviewer
    pair. Per-criterion rows expose reviewer counts, score counts, exact
    pairwise agreement, and an agreement-adjusted kappa on the configured score
    scale for the single-session admin view.
    """

    latest = _latest_record_by_reviewer(records)
    reviewer_count = len(latest)
    if reviewer_count < min_reviewers:
        return {
            "status": "insufficient_reviewers",
            "reviewer_count": reviewer_count,
            "min_reviewers": min_reviewers,
            "overall_cohen_kappa": None,
            "per_criterion": [],
        }

    by_reviewer = {
        reviewer_id: _criteria_scores(record)
        for reviewer_id, record in latest.items()
    }
    criterion_ids = sorted(
        {
            criterion_id
            for scores in by_reviewer.values()
            for criterion_id in scores.keys()
        }
    )

    per_criterion: list[dict[str, Any]] = []
    for criterion_id in criterion_ids:
        scores = {
            reviewer_id: values[criterion_id]
            for reviewer_id, values in by_reviewer.items()
            if criterion_id in values
        }
        per_criterion.append(
            _criterion_summary(criterion_id, scores, labels=labels)
        )

    pairwise: list[dict[str, Any]] = []
    for left_id, right_id in combinations(sorted(by_reviewer), 2):
        common = sorted(set(by_reviewer[left_id]) & set(by_reviewer[right_id]))
        left_scores = [by_reviewer[left_id][criterion_id] for criterion_id in common]
        right_scores = [by_reviewer[right_id][criterion_id] for criterion_id in common]
        pairwise.append(
            {
                "reviewer_a": left_id,
                "reviewer_b": right_id,
                "common_criteria": common,
                "cohen_kappa": cohen_kappa(left_scores, right_scores, labels=labels),
            }
        )

    kappas = [
        float(item["cohen_kappa"])
        for item in pairwise
        if isinstance(item.get("cohen_kappa"), (int, float))
    ]
    overall = sum(kappas) / len(kappas) if kappas else None
    return {
        "status": "computed",
        "reviewer_count": reviewer_count,
        "min_reviewers": min_reviewers,
        "criteria_count": len(criterion_ids),
        "overall_cohen_kappa": overall,
        "per_criterion": per_criterion,
        "pairwise": pairwise,
    }


def cohen_kappa(
    left: list[int],
    right: list[int],
    *,
    labels: tuple[int, ...] = DEFAULT_SCORE_LABELS,
) -> float | None:
    """Return unweighted Cohen's kappa for two aligned rating vectors."""

    if len(left) != len(right) or not left:
        return None

    total = len(left)
    observed = sum(1 for a, b in zip(left, right) if a == b) / total
    expected = 0.0
    for label in labels:
        left_rate = sum(1 for score in left if score == label) / total
        right_rate = sum(1 for score in right if score == label) / total
        expected += left_rate * right_rate

    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def _latest_record_by_reviewer(
    records: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        reviewer_id = str(record.get("reviewer_id") or "").strip()
        if not reviewer_id:
            continue
        current = latest.get(reviewer_id)
        if current is None or str(record.get("submitted_at") or "") >= str(
            current.get("submitted_at") or ""
        ):
            latest[reviewer_id] = record
    return latest


def _criteria_scores(record: dict[str, Any]) -> dict[str, int]:
    scores: dict[str, int] = {}
    criteria = record.get("criteria") if isinstance(record, dict) else []
    if not isinstance(criteria, list):
        return scores
    for item in criteria:
        if not isinstance(item, dict):
            continue
        criterion_id = str(item.get("criterion_id") or "").strip()
        if not criterion_id:
            continue
        try:
            score = int(item.get("score"))
        except (TypeError, ValueError):
            continue
        scores[criterion_id] = score
    return scores


def _criterion_summary(
    criterion_id: str,
    scores: dict[str, int],
    *,
    labels: tuple[int, ...],
) -> dict[str, Any]:
    counts = {str(label): 0 for label in labels}
    for score in scores.values():
        counts[str(score)] = counts.get(str(score), 0) + 1

    pairs = list(combinations(scores.values(), 2))
    if pairs:
        exact = sum(1 for a, b in pairs if a == b) / len(pairs)
        chance = 1.0 / len(labels) if labels else 0.0
        kappa = (exact - chance) / (1.0 - chance) if chance < 1.0 else None
    else:
        exact = None
        kappa = None

    return {
        "criterion_id": criterion_id,
        "reviewer_count": len(scores),
        "score_counts": counts,
        "exact_pairwise_agreement": exact,
        "cohen_kappa": kappa,
    }
