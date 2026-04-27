"""Derive pairwise ground-truth pairs from existing pointwise human labels.

For each (task_id, dimension, rubric_id) group, pair samples whose
per-sample mean human score differs by at least ``min_score_gap``.
Output is shaped like the pilot corpus the pairwise judge runner already
consumes — ``items`` plus ``adversarial_pairs`` — so the derived file can
be passed via ``run pairwise --corpus`` without further reshaping.

Reuses the 68 pointwise labels we already have — no new human work and
no API calls happen here. Running the pairwise judge against the output
is a separate step.

Same-task only: cross-task pairwise needs per-side context support in
the runner (today the runner uses one shared_context taken from the
stronger sample), so emitting cross-task pairs here would have them
evaluated against the wrong task. That extension belongs to a separate
slice if/when needed.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

DERIVED_PAIRS_VERSION = "judge_validation_derived_pairs_v1"
DEFAULT_MIN_SCORE_GAP = 2.0


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _per_sample_scores(
    labels: list[dict[str, Any]],
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Group labels by (sample_id, dimension, rubric_id) and aggregate scores
    over reviewers. Reviewer-mean is the per-sample score we compare on."""

    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for label in labels:
        sample_id = str(label.get("sample_id") or "")
        dimension = str(label.get("dimension") or "")
        rubric_id = str(label.get("rubric_id") or "")
        if not sample_id or not dimension or not rubric_id:
            continue
        score = label.get("human_score")
        if score is None:
            continue
        grouped[(sample_id, dimension, rubric_id)].append(
            {
                "reviewer_id": str(label.get("reviewer_id") or "unknown"),
                "score": float(score),
            }
        )

    aggregated = {}
    for key, rows in grouped.items():
        per_reviewer: dict[str, list[float]] = defaultdict(list)
        for row in rows:
            per_reviewer[row["reviewer_id"]].append(row["score"])
        reviewer_means = {r: mean(s) for r, s in per_reviewer.items()}
        aggregated[key] = {
            "mean_score": mean(reviewer_means.values()),
            "reviewer_count": len(reviewer_means),
            "reviewer_scores": reviewer_means,
        }
    return aggregated


def derive_pairs(
    labels: list[dict[str, Any]],
    items: list[dict[str, Any]],
    min_score_gap: float = DEFAULT_MIN_SCORE_GAP,
    sample_id_map: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Build derived pairs from human labels.

    A pair is emitted when two samples share the same (task_id, dimension,
    rubric_id) and their reviewer-mean scores differ by at least
    ``min_score_gap``. Pair direction is by score: higher mean = stronger.

    ``sample_id_map`` maps blind review_sample_id → original sample_id used
    by the corpus (so label sample_ids resolve to corpus task_ids).
    """

    sample_to_task: dict[str, str] = {
        str(item.get("sample_id") or ""): str(item.get("task_id") or "")
        for item in items
    }
    sample_id_map = sample_id_map or {}
    per_sample = _per_sample_scores(labels)

    by_group: dict[
        tuple[str, str, str], list[tuple[str, dict[str, Any]]]
    ] = defaultdict(list)
    for (sample_id, dimension, rubric_id), agg in per_sample.items():
        original_sample_id = sample_id_map.get(sample_id, sample_id)
        task_id = sample_to_task.get(original_sample_id, "")
        if not task_id:
            continue
        by_group[(task_id, dimension, rubric_id)].append(
            (original_sample_id, agg)
        )

    pairs: list[dict[str, Any]] = []
    for (task_id, dimension, rubric_id), samples in by_group.items():
        if len(samples) < 2:
            continue
        samples_sorted = sorted(
            samples, key=lambda kv: kv[1]["mean_score"], reverse=True
        )
        for i in range(len(samples_sorted)):
            orig_sid_hi, agg_hi = samples_sorted[i]
            for j in range(i + 1, len(samples_sorted)):
                orig_sid_lo, agg_lo = samples_sorted[j]
                gap = agg_hi["mean_score"] - agg_lo["mean_score"]
                if gap < min_score_gap:
                    # Sorted descending by score, so subsequent j may have a
                    # larger gap; do not break here.
                    continue
                pair_id = (
                    f"derived_{task_id}_{dimension}_{orig_sid_hi}__vs__{orig_sid_lo}"
                )
                pairs.append(
                    {
                        "pair_id": pair_id,
                        "task_id": task_id,
                        "dimension": dimension,
                        "registry_rubric_id": rubric_id,
                        "stronger_sample_id": orig_sid_hi,
                        "weaker_sample_id": orig_sid_lo,
                        "score_gap": round(gap, 3),
                        "stronger_mean_score": round(agg_hi["mean_score"], 3),
                        "weaker_mean_score": round(agg_lo["mean_score"], 3),
                        "stronger_reviewer_count": agg_hi["reviewer_count"],
                        "weaker_reviewer_count": agg_lo["reviewer_count"],
                        "source": DERIVED_PAIRS_VERSION,
                    }
                )
    pairs.sort(
        key=lambda p: (p["task_id"], p["dimension"], -p["score_gap"], p["pair_id"])
    )
    return pairs


def build_corpus_payload(
    pairs: list[dict[str, Any]],
    source_items: list[dict[str, Any]],
    source_pass_threshold: Any = None,
    min_score_gap: float = DEFAULT_MIN_SCORE_GAP,
) -> dict[str, Any]:
    """Build a corpus-shape payload the existing pairwise runner can ingest.

    Items are filtered from the source corpus to only those referenced by
    a derived pair, so ``_pairwise`` can resolve sample_id → item without
    pulling in unrelated corpus content.
    """

    referenced: set[str] = set()
    for pair in pairs:
        referenced.add(str(pair.get("stronger_sample_id") or ""))
        referenced.add(str(pair.get("weaker_sample_id") or ""))
    referenced.discard("")

    items_by_id = {str(it.get("sample_id") or ""): it for it in source_items}
    filtered_items = [items_by_id[sid] for sid in sorted(referenced) if sid in items_by_id]

    payload: dict[str, Any] = {
        "version": DERIVED_PAIRS_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "derived_from": "human_labels.reviewer_mean",
        "min_score_gap": min_score_gap,
        "summary": _summarize(pairs),
        "items": filtered_items,
        "adversarial_pairs": pairs,
    }
    if source_pass_threshold is not None:
        payload["pass_threshold"] = source_pass_threshold
    return payload


def _summarize(pairs: list[dict[str, Any]]) -> dict[str, Any]:
    by_dim: dict[str, int] = defaultdict(int)
    by_task: dict[str, int] = defaultdict(int)
    for pair in pairs:
        by_dim[pair["dimension"]] += 1
        by_task[pair["task_id"]] += 1
    return {
        "total_pairs": len(pairs),
        "by_dimension": dict(sorted(by_dim.items())),
        "by_task": dict(sorted(by_task.items())),
        "score_gap_min": (
            min(p["score_gap"] for p in pairs) if pairs else None
        ),
        "score_gap_max": (
            max(p["score_gap"] for p in pairs) if pairs else None
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        default="experiments/judge_validation/results/human_labels.json",
        help="Path to human_labels.json",
    )
    parser.add_argument(
        "--corpus",
        default="experiments/judge_validation/pilot_corpus.json",
        help="Path to pilot_corpus.json (used to map sample_id → task_id)",
    )
    parser.add_argument(
        "--sample-map",
        default="experiments/judge_validation/results/human_review_packet/human_review_sample_map.json",
        help="Blind review_sample_id → original sample_id mapping",
    )
    parser.add_argument(
        "--output",
        default="experiments/judge_validation/results/derived_pairs_corpus.json",
        help="Where to write the derived corpus JSON",
    )
    parser.add_argument(
        "--min-score-gap",
        type=float,
        default=DEFAULT_MIN_SCORE_GAP,
        help="Minimum reviewer-mean score gap to emit a pair (default 2.0)",
    )
    args = parser.parse_args(argv)

    labels_payload = _load_json(Path(args.labels))
    corpus_payload = _load_json(Path(args.corpus))
    labels = labels_payload.get("labels") or []
    items = corpus_payload.get("items") or []

    sample_id_map: dict[str, str] = {}
    if args.sample_map and Path(args.sample_map).exists():
        map_payload = _load_json(Path(args.sample_map))
        for row in map_payload.get("mappings") or []:
            review_sid = str(row.get("review_sample_id") or "")
            original_sid = str(row.get("original_sample_id") or "")
            if review_sid and original_sid:
                sample_id_map[review_sid] = original_sid

    pairs = derive_pairs(
        labels=labels,
        items=items,
        min_score_gap=args.min_score_gap,
        sample_id_map=sample_id_map,
    )

    payload = build_corpus_payload(
        pairs=pairs,
        source_items=items,
        source_pass_threshold=corpus_payload.get("pass_threshold"),
        min_score_gap=args.min_score_gap,
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "output": str(output_path),
                "items": len(payload["items"]),
                **payload["summary"],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
