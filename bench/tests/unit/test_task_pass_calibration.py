import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from eval.core.scoring import (
    PASS_THRESHOLD,
    PASS_THRESHOLD_HUMAN_PASS_RAW_SCORE,
)


BENCH_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: str) -> dict[str, Any]:
    return json.loads((BENCH_ROOT / path).read_text(encoding="utf-8"))


def _record_key(record: dict[str, Any]) -> tuple[str, str, str]:
    metadata = record.get("judge_metadata") or {}
    return (
        str(record.get("sample_id") or ""),
        str(record.get("registry_rubric_id") or metadata.get("rubric_id") or ""),
        str(record.get("dimension") or metadata.get("dimension") or ""),
    )


def test_task_pass_threshold_matches_human_alignment_corpus():
    runs = _load_json("experiments/judge_validation/results/judge_runs.json")
    labels = _load_json("experiments/judge_validation/human_labels.json")
    sample_map = _load_json("experiments/judge_validation/human_review_sample_map.json")

    review_to_original = {
        row["review_sample_id"]: row["original_sample_id"]
        for row in sample_map["mappings"]
    }
    judge_scores: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    for record in runs["records"]:
        score = record.get("score")
        if (
            record.get("status") == "success"
            and isinstance(score, (int, float))
            and not isinstance(score, bool)
        ):
            judge_scores[_record_key(record)].append(float(score))

    comparisons: list[tuple[float, bool]] = []
    for label in labels["labels"]:
        key = (
            review_to_original.get(label["sample_id"], label["sample_id"]),
            label["rubric_id"],
            label["dimension"],
        )
        scores = judge_scores.get(key)
        if scores:
            comparisons.append(
                (
                    sum(scores) / len(scores),
                    int(label["human_score"]) >= PASS_THRESHOLD_HUMAN_PASS_RAW_SCORE,
                )
            )

    agreements = [
        (judge_score >= PASS_THRESHOLD) == human_pass
        for judge_score, human_pass in comparisons
    ]

    assert len(comparisons) == 68
    assert PASS_THRESHOLD == 0.5
    assert round(sum(agreements) / len(agreements), 4) == 0.7794
