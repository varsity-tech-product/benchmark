import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))

from experiments.judge_validation.derive_pairs import (
    build_corpus_payload,
    derive_pairs,
)


def _label(sample_id, dimension, score, reviewer="r1", rubric="quant_correctness.v1"):
    return {
        "sample_id": sample_id,
        "rubric_id": rubric,
        "dimension": dimension,
        "human_score": score,
        "reviewer_id": reviewer,
    }


def _item(sample_id, task_id, dimension="D4_instructional_accuracy"):
    return {"sample_id": sample_id, "task_id": task_id, "dimension": dimension}


def test_emits_pair_when_same_task_and_gap_meets_threshold():
    labels = [
        _label("s_high", "D4_instructional_accuracy", 5),
        _label("s_low", "D4_instructional_accuracy", 2),
    ]
    items = [_item("s_high", "T1"), _item("s_low", "T1")]
    pairs = derive_pairs(labels, items, min_score_gap=2.0)
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["stronger_sample_id"] == "s_high"
    assert pair["weaker_sample_id"] == "s_low"
    assert pair["score_gap"] == 3.0
    assert pair["task_id"] == "T1"
    assert pair["dimension"] == "D4_instructional_accuracy"


def test_skips_pair_below_score_gap_threshold():
    labels = [
        _label("a", "D4_instructional_accuracy", 4),
        _label("b", "D4_instructional_accuracy", 3),
    ]
    items = [_item("a", "T1"), _item("b", "T1")]
    assert derive_pairs(labels, items, min_score_gap=2.0) == []


def test_aggregates_multiple_reviewers_via_mean():
    labels = [
        _label("a", "D4_instructional_accuracy", 5, reviewer="r1"),
        _label("a", "D4_instructional_accuracy", 4, reviewer="r2"),
        _label("b", "D4_instructional_accuracy", 2, reviewer="r1"),
        _label("b", "D4_instructional_accuracy", 1, reviewer="r2"),
    ]
    items = [_item("a", "T1"), _item("b", "T1")]
    pairs = derive_pairs(labels, items, min_score_gap=2.0)
    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["stronger_mean_score"] == 4.5
    assert pair["weaker_mean_score"] == 1.5
    assert pair["stronger_reviewer_count"] == 2
    assert pair["weaker_reviewer_count"] == 2


def test_cross_task_pairs_are_never_emitted():
    """Only same-task pairs are valid for the existing pairwise runner
    (it uses a single shared_context taken from the stronger sample).
    Cross-task pair derivation belongs to a separate slice."""

    labels = [
        _label("a", "D4_instructional_accuracy", 5),
        _label("b", "D4_instructional_accuracy", 1),
    ]
    items = [_item("a", "T1"), _item("b", "T2")]
    assert derive_pairs(labels, items, min_score_gap=2.0) == []


def test_sample_id_map_translates_blind_ids_to_corpus_ids():
    labels = [
        _label("blind_high", "D4_instructional_accuracy", 5),
        _label("blind_low", "D4_instructional_accuracy", 1),
    ]
    items = [_item("orig_high", "T1"), _item("orig_low", "T1")]
    sample_id_map = {"blind_high": "orig_high", "blind_low": "orig_low"}
    pairs = derive_pairs(
        labels,
        items,
        min_score_gap=2.0,
        sample_id_map=sample_id_map,
    )
    assert len(pairs) == 1
    assert pairs[0]["stronger_sample_id"] == "orig_high"
    assert pairs[0]["weaker_sample_id"] == "orig_low"


def test_iterates_full_inner_loop_when_top_pair_below_threshold():
    """Sorted descending: top sample's neighbour may be below threshold but
    a later, lower-scored sample can still form a valid pair. Inner loop
    must not break early."""

    labels = [
        _label("a", "D4_instructional_accuracy", 5),
        _label("b", "D4_instructional_accuracy", 4),
        _label("c", "D4_instructional_accuracy", 1),
    ]
    items = [_item(s, "T1") for s in ("a", "b", "c")]
    pairs = derive_pairs(labels, items, min_score_gap=2.0)
    pair_ids = {(p["stronger_sample_id"], p["weaker_sample_id"]) for p in pairs}
    assert ("a", "c") in pair_ids
    assert ("b", "c") in pair_ids
    assert ("a", "b") not in pair_ids


def test_groups_separate_dimensions_independently():
    labels = [
        _label("a", "D4_instructional_accuracy", 5),
        _label("a", "D2_code_adaptation", 1),
        _label("b", "D4_instructional_accuracy", 1),
        _label("b", "D2_code_adaptation", 5),
    ]
    items = [_item("a", "T1"), _item("b", "T1")]
    pairs = derive_pairs(labels, items, min_score_gap=2.0)
    by_dim = {p["dimension"]: p for p in pairs}
    assert by_dim["D4_instructional_accuracy"]["stronger_sample_id"] == "a"
    assert by_dim["D2_code_adaptation"]["stronger_sample_id"] == "b"


def test_corpus_payload_carries_items_referenced_by_pairs():
    """The pairwise runner reads ``corpus['items']`` to resolve sample_id →
    item; build_corpus_payload must include exactly the referenced items
    so the runner can schedule comparisons."""

    labels = [
        _label("a", "D4_instructional_accuracy", 5),
        _label("b", "D4_instructional_accuracy", 1),
        _label("unused", "D4_instructional_accuracy", 3),
    ]
    source_items = [
        _item("a", "T1"),
        _item("b", "T1"),
        _item("unused", "T2"),
    ]
    pairs = derive_pairs(labels, source_items, min_score_gap=2.0)
    payload = build_corpus_payload(
        pairs=pairs,
        source_items=source_items,
        source_pass_threshold=3,
        min_score_gap=2.0,
    )
    item_ids = {it["sample_id"] for it in payload["items"]}
    assert item_ids == {"a", "b"}
    assert "unused" not in item_ids
    assert "adversarial_pairs" in payload
    assert payload["adversarial_pairs"] == pairs
    assert payload["pass_threshold"] == 3


def test_corpus_payload_with_no_pairs_yields_empty_items_and_pairs():
    payload = build_corpus_payload(
        pairs=[],
        source_items=[_item("a", "T1")],
        min_score_gap=2.0,
    )
    assert payload["items"] == []
    assert payload["adversarial_pairs"] == []
    assert payload["summary"]["total_pairs"] == 0
