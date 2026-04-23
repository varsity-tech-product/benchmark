import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))

from experiments.judge_validation.report import compute_reliability_stats
from server.eval.inputs.rubric_builder import build_eval_params, load_rubric
from server.eval.judges.runtime.conv_geval import EvalTestCase, EwanConvGEval
from server.eval.rubrics.registry import (
    get_registry_rubric,
    load_rubric_registry,
    mapped_registry_ids,
)


def test_rubric_registry_covers_required_stage1_dimensions():
    registry = load_rubric_registry()
    ids = {entry["rubric_id"] for entry in registry["rubrics"]}
    expected = {
        "task_completion.v1",
        "quant_correctness.v1",
        "code_correctness.v1",
        "teaching_quality.v1",
        "student_adaptation.v1",
        "tool_workspace_use.v1",
        "failure_handling.v1",
        "final_outcome_quality.v1",
    }
    assert expected <= ids
    for rubric_id in expected:
        entry = get_registry_rubric(rubric_id)
        assert entry
        assert set(entry["score_anchors"]) == {"1", "2", "3", "4", "5"}
        assert entry["required_evidence"]
        assert entry["common_failure_cases"]
        assert {"high", "medium", "low"} <= set(entry["examples"])


def test_implemented_dimension_maps_to_registry_ids():
    assert "quant_correctness.v1" in mapped_registry_ids(
        "tutor", "D4_instructional_accuracy"
    )
    assert "code_correctness.v1" in mapped_registry_ids("qr", "result_judge")


def test_pilot_corpus_items_match_registry_mappings():
    root = Path(__file__).parent.parent
    corpus = json.loads(
        (
            root / "bench/experiments/judge_validation/pilot_corpus.json"
        ).read_text(encoding="utf-8")
    )
    registry = load_rubric_registry()
    mapped = {
        entry["rubric_id"]: {
            (item["track"], item["dimension"])
            for item in entry.get("mapped_judge_dimensions", [])
        }
        for entry in registry["rubrics"]
    }

    for item in corpus["items"]:
        key = (item["track"], item["dimension"])
        assert key in mapped[item["registry_rubric_id"]]


def test_conv_geval_prompt_and_metadata_include_rubric_version():
    rubric = load_rubric("qr")
    params = build_eval_params(rubric, "result_judge", rubric_name="qr")
    metric = EwanConvGEval(name="result_judge", model="judge-model", **params)
    metadata = metric.judge_metadata()
    prompt = metric.render_prompt(EvalTestCase(context="1. test"))

    assert metadata["rubric_id"] == "qr.result_judge"
    assert metadata["rubric_version"] == "qr_v1"
    assert metadata["prompt_template_version"] == "conv_geval_score_prompt_v1"
    assert metadata["output_schema"]["version"] == "conv_geval_score_json_v1"
    assert metadata["output_schema"]["required_fields"] == ["score"]
    assert set(metadata["output_schema"]["optional_fields"]) == {"evidence", "reason"}
    assert "Rubric ID: qr.result_judge" in prompt
    assert "Rubric Version: qr_v1" in prompt


def test_reliability_stats_compute_stability_and_adversarial_pass_rate():
    corpus = {
        "pass_threshold": 3,
        "items": [{"sample_id": "strong"}, {"sample_id": "weak"}],
        "adversarial_pairs": [
            {
                "pair_id": "p1",
                "registry_rubric_id": "quant_correctness.v1",
                "dimension": "D4_instructional_accuracy",
                "stronger_sample_id": "strong",
                "weaker_sample_id": "weak",
            }
        ],
    }
    records = [
        {
            "sample_id": "strong",
            "registry_rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "judge_model": "judge",
            "run_index": 0,
            "status": "success",
            "raw_score": 5,
        },
        {
            "sample_id": "strong",
            "registry_rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "judge_model": "judge",
            "run_index": 1,
            "status": "success",
            "raw_score": 4,
        },
        {
            "sample_id": "weak",
            "registry_rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "judge_model": "judge",
            "run_index": 0,
            "status": "success",
            "raw_score": 2,
        },
        {
            "sample_id": "weak",
            "registry_rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "judge_model": "judge",
            "run_index": 1,
            "status": "success",
            "raw_score": 2,
        },
    ]
    stats = compute_reliability_stats(corpus=corpus, records=records)

    assert stats["stability"]["mean_absolute_score_delta"] == 0.5
    assert stats["stability"]["within_one_score_rate"] == 1.0
    assert stats["stability"]["pass_fail_flip_rate"] == 0.0
    assert stats["adversarial"]["ranking_pass_rate"] == 1.0


def test_adversarial_stats_filter_by_pair_rubric_and_dimension():
    corpus = {
        "pass_threshold": 3,
        "items": [{"sample_id": "strong"}, {"sample_id": "weak"}],
        "adversarial_pairs": [
            {
                "pair_id": "p1",
                "registry_rubric_id": "rubric_a.v1",
                "dimension": "dimension_a",
                "stronger_sample_id": "strong",
                "weaker_sample_id": "weak",
            },
            {
                "pair_id": "p2",
                "registry_rubric_id": "rubric_b.v1",
                "dimension": "dimension_b",
                "stronger_sample_id": "strong",
                "weaker_sample_id": "weak",
            },
        ],
    }
    records = [
        {
            "sample_id": "strong",
            "registry_rubric_id": "rubric_a.v1",
            "dimension": "dimension_a",
            "judge_model": "judge",
            "status": "success",
            "raw_score": 5,
        },
        {
            "sample_id": "weak",
            "registry_rubric_id": "rubric_a.v1",
            "dimension": "dimension_a",
            "judge_model": "judge",
            "status": "success",
            "raw_score": 1,
        },
        {
            "sample_id": "strong",
            "registry_rubric_id": "rubric_b.v1",
            "dimension": "dimension_b",
            "judge_model": "judge",
            "status": "success",
            "raw_score": 1,
        },
        {
            "sample_id": "weak",
            "registry_rubric_id": "rubric_b.v1",
            "dimension": "dimension_b",
            "judge_model": "judge",
            "status": "success",
            "raw_score": 5,
        },
    ]
    stats = compute_reliability_stats(corpus=corpus, records=records)
    pairs = {row["pair_id"]: row for row in stats["adversarial"]["pairs"]}

    assert pairs["p1"]["status"] == "pass"
    assert pairs["p1"]["score_margin"] == 4.0
    assert pairs["p2"]["status"] == "fail"
    assert pairs["p2"]["score_margin"] == -4.0
