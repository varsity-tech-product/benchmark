import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))

from experiments.judge_validation import run as judge_run
from experiments.judge_validation.human_alignment import (
    compute_human_alignment_stats,
    compute_inter_rater_agreement,
    compute_judge_vs_reviewer_mean,
    convert_csv_to_human_labels,
    labels_from_csv_rows,
    load_sample_id_map,
    normalize_human_label,
)
from experiments.judge_validation.review_packet import (
    build_review_packet,
    write_review_packet,
)


def test_human_alignment_stats_compute_agreement_metrics():
    corpus = {
        "pass_threshold": 3,
        "items": [
            {
                "sample_id": "strong",
                "task_id": "B03_lookahead_prevention",
                "category": "backtest",
                "persona_id": "finance_veteran",
                "transcript_source": "synthetic_adversarial",
            },
            {
                "sample_id": "weak",
                "task_id": "B03_lookahead_prevention",
                "category": "backtest",
                "persona_id": "finance_veteran",
                "transcript_source": "synthetic_adversarial",
            },
        ],
    }
    records = [
        {
            "sample_id": "strong",
            "registry_rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "status": "success",
            "raw_score": 5,
        },
        {
            "sample_id": "strong",
            "registry_rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "status": "success",
            "raw_score": 4,
        },
        {
            "sample_id": "weak",
            "registry_rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "status": "success",
            "raw_score": 2,
        },
    ]
    labels = [
        {
            "sample_id": "strong",
            "rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "human_score": 5,
            "confidence": "high",
            "human_rationale": "The timing correction is accurate.",
            "evidence_spans": ["shift by one day"],
            "failure_tags": [],
            "reviewer_id": "expert_anon_1",
            "timestamp": "2026-04-23T00:00:00Z",
        },
        {
            "sample_id": "weak",
            "rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "human_score": 1,
            "confidence": "high",
            "human_rationale": "The answer permits same-close trading.",
            "evidence_spans": ["trade happened at that close"],
            "failure_tags": ["quant_error"],
            "reviewer_id": "expert_anon_1",
            "timestamp": "2026-04-23T00:00:00Z",
        },
    ]

    stats = compute_human_alignment_stats(
        corpus=corpus,
        records=records,
        labels=labels,
    )

    assert stats["counts"]["labels"] == 2
    assert stats["counts"]["comparable_labels"] == 2
    assert stats["overall"]["exact_agreement"] == 0.5
    assert stats["overall"]["within_one_agreement"] == 1.0
    assert stats["overall"]["mean_absolute_delta"] == 0.75
    assert stats["overall"]["pass_fail_agreement"] == 1.0
    assert stats["slices"]["by_dimension"]["D4_instructional_accuracy"]["labels"] == 2
    assert stats["stage3_gate"]["status"] == "diagnostic_only"
    assert stats["absolute_alignment_diagnostic"]["status"] == "clear"


def test_human_alignment_reports_large_disagreements_and_missing_scores():
    corpus = {
        "pass_threshold": 3,
        "items": [
            {
                "sample_id": "sample",
                "category": "debug",
                "persona_id": "developer_crossover",
                "transcript_source": "real_run_excerpt",
            }
        ],
    }
    records = [
        {
            "sample_id": "sample",
            "registry_rubric_id": "code_correctness.v1",
            "dimension": "result_judge",
            "status": "success",
            "raw_score": 2,
        }
    ]
    labels = [
        {
            "sample_id": "sample",
            "rubric_id": "code_correctness.v1",
            "dimension": "result_judge",
            "human_score": 5,
            "confidence": "medium",
            "human_rationale": "The human reviewer credited the regression test.",
            "evidence_spans": ["test passed"],
            "failure_tags": ["unclear_rubric"],
            "reviewer_id": "expert_anon_2",
            "timestamp": "2026-04-23T00:00:00Z",
        },
        {
            "sample_id": "missing",
            "rubric_id": "code_correctness.v1",
            "dimension": "result_judge",
            "human_score": 4,
            "confidence": "low",
            "human_rationale": "This label has no matching judge score.",
            "evidence_spans": ["artifact exists"],
            "failure_tags": [],
            "reviewer_id": "expert_anon_2",
            "timestamp": "2026-04-23T00:00:00Z",
        },
    ]

    stats = compute_human_alignment_stats(
        corpus=corpus,
        records=records,
        labels=labels,
    )

    assert stats["counts"]["missing_judge_comparisons"] == 1
    assert stats["counts"]["large_disagreements"] == 1
    assert stats["large_disagreement_examples"][0]["absolute_delta"] == 3.0
    assert stats["large_disagreement_examples"][0]["failure_tags"] == [
        "unclear_rubric"
    ]
    assert stats["stage3_gate"]["large_disagreements_documented"] is True
    assert stats["stage3_gate"]["status"] == "diagnostic_only"
    assert stats["absolute_alignment_diagnostic"]["status"] == "needs_review"


def test_human_alignment_gate_requires_full_label_coverage():
    corpus = {"pass_threshold": 3, "items": [{"sample_id": "sample"}]}
    records = [
        {
            "sample_id": "sample",
            "registry_rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "status": "success",
            "raw_score": 5,
        }
    ]
    labels = [
        {
            "sample_id": "sample",
            "rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "human_score": 5,
            "confidence": "high",
            "human_rationale": "Correct.",
            "reviewer_id": "expert_anon_1",
        },
        {
            "sample_id": "missing",
            "rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "human_score": 5,
            "confidence": "high",
            "human_rationale": "Correct.",
            "reviewer_id": "expert_anon_1",
        },
    ]

    stats = compute_human_alignment_stats(
        corpus=corpus,
        records=records,
        labels=labels,
    )

    assert stats["overall"]["within_one_agreement"] == 1.0
    assert stats["counts"]["missing_judge_comparisons"] == 1
    assert stats["stage3_gate"]["status"] == "diagnostic_only"
    assert stats["absolute_alignment_diagnostic"]["status"] == "needs_review"


def test_subjective_dimensions_use_relaxed_within_one_target():
    """Subjective dimensions (D1-D4) hit the 0.70 target; objective ones
    (D6 / result_judge) keep the 0.85 target.

    Both samples here have judge=3 vs human=4 → within-one=1.0 per
    sample but exact_agreement=0. With one out-of-band sample we can
    drop within-one below 0.85 but stay above 0.70, isolating the
    threshold flip per dimension."""

    def _make(sample_id, dimension, judge_score, human_score, rubric_id):
        record = {
            "sample_id": sample_id,
            "registry_rubric_id": rubric_id,
            "dimension": dimension,
            "status": "success",
            "raw_score": judge_score,
        }
        label = {
            "sample_id": sample_id,
            "rubric_id": rubric_id,
            "dimension": dimension,
            "human_score": human_score,
            "confidence": "high",
            "human_rationale": "ok",
            "reviewer_id": "expert_anon_1",
        }
        return record, label

    corpus_items = []
    records = []
    labels = []

    # 4 close pairs + 1 far pair on D2 → within-one 0.80, between 0.70 and 0.85.
    for i in range(4):
        rec, lab = _make(
            f"d2_close_{i}",
            "D2_code_adaptation",
            judge_score=3,
            human_score=4,
            rubric_id="student_adaptation.v1",
        )
        records.append(rec)
        labels.append(lab)
        corpus_items.append({"sample_id": f"d2_close_{i}"})
    rec, lab = _make(
        "d2_far",
        "D2_code_adaptation",
        judge_score=1,
        human_score=4,
        rubric_id="student_adaptation.v1",
    )
    records.append(rec)
    labels.append(lab)
    corpus_items.append({"sample_id": "d2_far"})

    # Same 4-close + 1-far pattern on result_judge (objective).
    for i in range(4):
        rec, lab = _make(
            f"rj_close_{i}",
            "result_judge",
            judge_score=4,
            human_score=5,
            rubric_id="code_correctness.v1",
        )
        records.append(rec)
        labels.append(lab)
        corpus_items.append({"sample_id": f"rj_close_{i}"})
    rec, lab = _make(
        "rj_far",
        "result_judge",
        judge_score=2,
        human_score=5,
        rubric_id="code_correctness.v1",
    )
    records.append(rec)
    labels.append(lab)
    corpus_items.append({"sample_id": "rj_far"})

    stats = compute_human_alignment_stats(
        corpus={"pass_threshold": 3, "items": corpus_items},
        records=records,
        labels=labels,
    )

    weak = {row["dimension"]: row for row in stats["stage3_gate"]["weak_dimensions"]}
    assert "D2_code_adaptation" not in weak, (
        "D2 within-one 0.80 should pass the 0.70 subjective target"
    )
    assert "result_judge" in weak, (
        "result_judge within-one 0.80 should fail the 0.85 objective target"
    )
    assert weak["result_judge"]["within_one_target"] == 0.85
    assert weak["result_judge"]["is_subjective"] is False

    targets = stats["targets"]
    assert targets["subjective_within_one_agreement"] == 0.70
    assert targets["subjective_pass_fail_agreement"] == 0.75
    assert "D2_code_adaptation" in targets["subjective_dimensions"]


def test_strict_objective_dimension_failure_blocks_gate_even_if_overall_passes():
    """A small objective slice (D6 or result_judge) failing 0.85 must not
    be masked by a large subjective slice pulling the overall within-one
    above 0.85."""

    records = []
    labels = []
    corpus_items = []

    # 20 subjective D3 samples all matching exactly → within-one=1.0,
    # pass-fail=1.0. Pulls overall up.
    for i in range(20):
        records.append(
            {
                "sample_id": f"d3_{i}",
                "registry_rubric_id": "teaching_quality.v1",
                "dimension": "D3_pedagogical_method",
                "status": "success",
                "raw_score": 4,
            }
        )
        labels.append(
            {
                "sample_id": f"d3_{i}",
                "rubric_id": "teaching_quality.v1",
                "dimension": "D3_pedagogical_method",
                "human_score": 4,
                "confidence": "high",
                "human_rationale": "ok",
                "evidence_spans": ["x"],
                "failure_tags": [],
                "reviewer_id": "expert_anon_1",
            }
        )
        corpus_items.append({"sample_id": f"d3_{i}"})

    # 4 result_judge samples: 2 within-one, 2 with abs delta 3 → within-one=0.5
    # which fails objective 0.85 target.
    for i in range(2):
        records.append(
            {
                "sample_id": f"rj_close_{i}",
                "registry_rubric_id": "code_correctness.v1",
                "dimension": "result_judge",
                "status": "success",
                "raw_score": 5,
            }
        )
        labels.append(
            {
                "sample_id": f"rj_close_{i}",
                "rubric_id": "code_correctness.v1",
                "dimension": "result_judge",
                "human_score": 5,
                "confidence": "high",
                "human_rationale": "ok",
                "evidence_spans": ["x"],
                "failure_tags": [],
                "reviewer_id": "expert_anon_1",
            }
        )
        corpus_items.append({"sample_id": f"rj_close_{i}"})
    for i in range(2):
        records.append(
            {
                "sample_id": f"rj_far_{i}",
                "registry_rubric_id": "code_correctness.v1",
                "dimension": "result_judge",
                "status": "success",
                "raw_score": 1,
            }
        )
        labels.append(
            {
                "sample_id": f"rj_far_{i}",
                "rubric_id": "code_correctness.v1",
                "dimension": "result_judge",
                "human_score": 5,
                "confidence": "high",
                "human_rationale": "ok",
                "evidence_spans": ["x"],
                "failure_tags": ["code_error"],
                "reviewer_id": "expert_anon_1",
            }
        )
        corpus_items.append({"sample_id": f"rj_far_{i}"})

    stats = compute_human_alignment_stats(
        corpus={"pass_threshold": 3, "items": corpus_items},
        records=records,
        labels=labels,
    )

    # Sanity: overall within-one should clear 0.85 because 22/24 samples
    # are within-one — confirms the masking risk would exist.
    assert stats["overall"]["within_one_agreement"] >= 0.85
    weak = {row["dimension"] for row in stats["stage3_gate"]["weak_dimensions"]}
    assert "result_judge" in weak
    assert stats["stage3_gate"]["status"] == "diagnostic_only"
    assert stats["absolute_alignment_diagnostic"]["status"] == "needs_review"


def test_subjective_dimension_below_relaxed_target_still_flags_weak():
    """If a subjective dimension drops below even the relaxed 0.70
    target, it should still appear in weak_dimensions."""

    records = []
    labels = []
    corpus_items = []
    # 5 D1 samples, only 2 within-one (40%) — below 0.70.
    for i in range(2):
        records.append(
            {
                "sample_id": f"close_{i}",
                "registry_rubric_id": "student_adaptation.v1",
                "dimension": "D1_finance_adaptation",
                "status": "success",
                "raw_score": 3,
            }
        )
        labels.append(
            {
                "sample_id": f"close_{i}",
                "rubric_id": "student_adaptation.v1",
                "dimension": "D1_finance_adaptation",
                "human_score": 4,
                "confidence": "high",
                "human_rationale": "ok",
                "reviewer_id": "expert_anon_1",
            }
        )
        corpus_items.append({"sample_id": f"close_{i}"})
    for i in range(3):
        records.append(
            {
                "sample_id": f"far_{i}",
                "registry_rubric_id": "student_adaptation.v1",
                "dimension": "D1_finance_adaptation",
                "status": "success",
                "raw_score": 1,
            }
        )
        labels.append(
            {
                "sample_id": f"far_{i}",
                "rubric_id": "student_adaptation.v1",
                "dimension": "D1_finance_adaptation",
                "human_score": 5,
                "confidence": "high",
                "human_rationale": "ok",
                "reviewer_id": "expert_anon_1",
            }
        )
        corpus_items.append({"sample_id": f"far_{i}"})

    stats = compute_human_alignment_stats(
        corpus={"pass_threshold": 3, "items": corpus_items},
        records=records,
        labels=labels,
    )
    weak = {row["dimension"]: row for row in stats["stage3_gate"]["weak_dimensions"]}
    assert "D1_finance_adaptation" in weak
    assert weak["D1_finance_adaptation"]["within_one_target"] == 0.70
    assert weak["D1_finance_adaptation"]["is_subjective"] is True


def test_human_label_score_must_be_integral():
    base = {
        "sample_id": "sample",
        "rubric_id": "quant_correctness.v1",
        "dimension": "D4_instructional_accuracy",
        "confidence": "high",
        "human_rationale": "Correct.",
        "reviewer_id": "expert_anon_1",
    }

    assert normalize_human_label({**base, "human_score": "5.0"})["human_score"] == 5

    try:
        normalize_human_label({**base, "human_score": "4.9"})
    except ValueError as exc:
        assert "human_score must be an integer" in str(exc)
    else:
        raise AssertionError("fractional human score should fail validation")


def test_google_form_csv_rows_convert_to_human_labels():
    rows = [
        {
            "Reviewer ID": "expert_anon_1",
            "Sample ID": "jv_quant_correct_good",
            "Rubric ID": "quant_correctness.v1",
            "Dimension": "D4_instructional_accuracy",
            "Human Score": "5",
            "Confidence": "High",
            "Human Rationale": "Correct lookahead explanation.",
            "Evidence Spans": "shift by one day\nnext bar",
            "Failure Tags": "quant_error, other",
            "Timestamp": "2026-04-23T00:00:00Z",
        }
    ]

    labels = labels_from_csv_rows(rows)

    assert labels[0]["human_score"] == 5
    assert labels[0]["confidence"] == "high"
    assert labels[0]["evidence_spans"] == ["shift by one day", "next bar"]
    assert labels[0]["failure_tags"] == ["quant_error", "other"]
    assert labels[0]["transcript_id"] == "jv_quant_correct_good"


def test_google_form_email_address_can_supply_reviewer_id():
    rows = [
        {
            "Email Address": "expert@example.com",
            "Sample ID": "sample",
            "Rubric ID": "quant_correctness.v1",
            "Dimension": "D4_instructional_accuracy",
            "Human Score": "5",
            "Confidence": "High",
            "Reviewer Rationale": "Correct lookahead explanation.",
        }
    ]

    labels = labels_from_csv_rows(rows)

    assert labels[0]["reviewer_id"] == "expert@example.com"
    assert labels[0]["human_rationale"] == "Correct lookahead explanation."


def test_bilingual_google_form_headers_and_options_convert_to_labels():
    rows = [
        {
            "Email Address": "expert@example.com",
            "reviewer_id / 专家 ID": "expert_anon_1",
            "sample_id / 样本 ID": "sample",
            "rubric_id / 评分规则 ID": "quant_correctness.v1",
            "dimension / 评分维度": "D4_instructional_accuracy",
            "human_score / 人类专家评分": "5",
            "confidence / 评分信心": "high / 高",
            "human_rationale / 专家理由": "Correct timing explanation.",
            "failure_tags / 失败标签": "quant_error / quant error, other / other",
        }
    ]

    labels = labels_from_csv_rows(rows)

    assert labels[0]["confidence"] == "high"
    assert labels[0]["failure_tags"] == ["quant_error", "other"]
    assert labels[0]["reviewer_id"] == "expert_anon_1"


def test_convert_human_labels_and_human_alignment_cli(tmp_path):
    csv_path = tmp_path / "labels.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "reviewer_id",
                "sample_id",
                "rubric_id",
                "dimension",
                "human_score",
                "confidence",
                "human_rationale",
                "evidence_spans",
                "failure_tags",
                "timestamp",
            ],
        )
        writer.writeheader()
        writer.writerow(
            {
                "reviewer_id": "expert_anon_1",
                "sample_id": "sample",
                "rubric_id": "quant_correctness.v1",
                "dimension": "D4_instructional_accuracy",
                "human_score": "4",
                "confidence": "high",
                "human_rationale": "Correct with a small omission.",
                "evidence_spans": "shift by one day",
                "failure_tags": "",
                "timestamp": "2026-04-23T00:00:00Z",
            }
        )
    payload = convert_csv_to_human_labels(csv_path)
    assert payload["labels"][0]["sample_id"] == "sample"

    corpus_path = tmp_path / "corpus.json"
    runs_path = tmp_path / "judge_runs.json"
    labels_path = tmp_path / "human_labels.json"
    output_dir = tmp_path / "reports"
    corpus_path.write_text(
        json.dumps(
            {
                "pass_threshold": 3,
                "items": [
                    {
                        "sample_id": "sample",
                        "category": "backtest",
                        "persona_id": "finance_veteran",
                        "transcript_source": "synthetic_adversarial",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    runs_path.write_text(
        json.dumps(
            {
                "run_id": "jv_test",
                "records": [
                    {
                        "sample_id": "sample",
                        "registry_rubric_id": "quant_correctness.v1",
                        "dimension": "D4_instructional_accuracy",
                        "status": "success",
                        "raw_score": 4,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    labels_path.write_text(json.dumps(payload), encoding="utf-8")

    rc = judge_run.main(
        [
            "--corpus",
            str(corpus_path),
            "--output-dir",
            str(output_dir),
            "human-alignment",
            "--runs",
            str(runs_path),
            "--labels",
            str(labels_path),
        ]
    )

    assert rc == 0
    stats = json.loads((output_dir / "human_alignment_stats.json").read_text())
    assert stats["overall"]["exact_agreement"] == 1.0
    assert (output_dir / "human_alignment_report.md").exists()
    assert (output_dir / "human_alignment_report.html").exists()


def test_human_alignment_uses_blind_sample_map():
    corpus = {"pass_threshold": 3, "items": [{"sample_id": "original"}]}
    records = [
        {
            "sample_id": "original",
            "registry_rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "status": "success",
            "raw_score": 5,
        }
    ]
    labels = [
        {
            "sample_id": "jv_review_001",
            "rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "human_score": 5,
            "confidence": "high",
            "human_rationale": "Correct.",
            "reviewer_id": "expert_anon_1",
        }
    ]

    stats = compute_human_alignment_stats(
        corpus=corpus,
        records=records,
        labels=labels,
        sample_id_map={"jv_review_001": "original"},
    )

    assert stats["counts"]["comparable_labels"] == 1
    assert stats["comparisons"][0]["sample_id"] == "original"
    assert stats["comparisons"][0]["review_sample_id"] == "jv_review_001"
    assert stats["stage3_gate"]["status"] == "diagnostic_only"
    assert stats["absolute_alignment_diagnostic"]["status"] == "clear"


def test_review_packet_exports_reviewer_materials_without_expected_scores(tmp_path):
    corpus = {
        "items": [
            {
                "sample_id": "sample",
                "pair_id": "pair",
                "pair_role": "stronger",
                "task_id": "B03_lookahead_prevention",
                "category": "backtest",
                "persona_id": "finance_veteran",
                "transcript_source": "synthetic_adversarial",
                "track": "tutor",
                "dimension": "D4_instructional_accuracy",
                "registry_rubric_id": "quant_correctness.v1",
                "expected_score_band": "high",
                "conversation": [
                    {"role": "user", "content": "Is same-close trading valid?"},
                    {"role": "assistant", "content": "Shift the signal one bar."},
                ],
            }
        ]
    }
    registry = {
        "rubrics": [
            {
                "rubric_id": "quant_correctness.v1",
                "version": "v1",
                "dimension": "quant_correctness",
                "score_scale": {"min": 1, "max": 5},
                "score_anchors": {"1": "wrong", "5": "excellent"},
                "required_evidence": ["formula"],
                "common_failure_cases": ["lookahead"],
                "examples": {"high": "accurate"},
            }
        ]
    }

    packet = build_review_packet(corpus=corpus, rubric_registry=registry)
    item = packet["items"][0]
    paths = write_review_packet(packet=packet, output_dir=tmp_path)

    assert packet["counts"]["items"] == 1
    assert packet["private_sample_map"][0]["review_sample_id"] == "jv_review_001"
    assert packet["private_sample_map"][0]["original_sample_id"] == "sample"
    assert item["sample_id"] == "jv_review_001"
    assert item["rubric_id"] == "quant_correctness.v1"
    assert "Shift the signal one bar" in item["review_context"]
    assert "sample" not in item["sample_id"]
    assert "expected_score_band" not in item
    assert "pair_role" not in item
    assert Path(paths["json"]).exists()
    assert Path(paths["markdown"]).exists()
    assert Path(paths["google_form_bilingual"]).exists()
    sample_map = load_sample_id_map(Path(paths["sample_map"]))
    assert sample_map == {"jv_review_001": "sample"}
    public_json = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
    assert "private_sample_map" not in public_json
    csv_text = Path(paths["csv"]).read_text(encoding="utf-8")
    assert "human_score" in csv_text
    assert "jv_review_001" in csv_text
    form_text = Path(paths["google_form_bilingual"]).read_text(encoding="utf-8")
    assert "Judge Validation Human Labels" in form_text
    assert "裁判验证人类专家标注" in form_text


def test_review_packet_blind_ids_use_corpus_position_for_targeted_exports():
    corpus = {
        "items": [
            {
                "sample_id": "first",
                "dimension": "result_judge",
                "registry_rubric_id": "code_correctness.v1",
                "context": "first context",
            },
            {
                "sample_id": "second",
                "dimension": "result_judge",
                "registry_rubric_id": "code_correctness.v1",
                "context": "second context",
            },
        ]
    }
    registry = {
        "rubrics": [
            {
                "rubric_id": "code_correctness.v1",
                "version": "v1",
                "dimension": "code_correctness",
                "score_scale": {"min": 1, "max": 5},
                "score_anchors": {"1": "broken", "5": "robust"},
            }
        ]
    }

    packet = build_review_packet(
        corpus=corpus,
        rubric_registry=registry,
        sample_ids=["second"],
    )

    assert packet["items"][0]["sample_id"] == "jv_review_002"
    assert packet["private_sample_map"][0]["original_sample_id"] == "second"


def test_export_review_packet_cli(tmp_path):
    corpus_path = tmp_path / "corpus.json"
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "packet"
    corpus_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "sample_id": "sample",
                        "task_id": "task",
                        "category": "debug",
                        "persona_id": "developer_crossover",
                        "transcript_source": "synthetic_adversarial",
                        "track": "qr",
                        "dimension": "result_judge",
                        "registry_rubric_id": "code_correctness.v1",
                        "context": "## Task\nFix the bug.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "rubrics": [
                    {
                        "rubric_id": "code_correctness.v1",
                        "version": "v1",
                        "dimension": "code_correctness",
                        "score_scale": {"min": 1, "max": 5},
                        "score_anchors": {"1": "broken", "5": "robust"},
                        "required_evidence": ["test output"],
                        "common_failure_cases": ["runtime error"],
                        "examples": {"high": "tested fix"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rc = judge_run.main(
        [
            "--corpus",
            str(corpus_path),
            "export-review-packet",
            "--rubric-registry",
            str(registry_path),
            "--packet-output-dir",
            str(output_dir),
        ]
    )

    assert rc == 0
    assert (output_dir / "human_review_packet.json").exists()
    assert (output_dir / "human_review_packet.md").exists()
    assert (output_dir / "human_label_template.csv").exists()
    assert (output_dir / "google_form_bilingual.md").exists()
    assert (output_dir / "human_review_sample_map.json").exists()


_QUANT_CORRECTNESS_CANONICAL_REGISTRY = {
    "rubrics": [
        {
            "rubric_id": "quant_correctness.v1",
            "version": "v1",
            "dimension": "quant_correctness",
            "score_scale": {"min": 1, "max": 5},
            "score_anchors": {
                "1": "Quant formulas, definitions, or conclusions are materially wrong.",
                "2": "Quant reasoning has notable omissions or imprecise claims.",
                "3": "Quant reasoning is basically correct for the requested task.",
                "4": "Quant reasoning is correct and flags relevant assumptions or pitfalls.",
                "5": "Quant reasoning is correct, nuanced, and grounded in computed evidence.",
            },
            "required_evidence": [
                "stated formula or method",
                "computed result or trace evidence",
                "explanation of assumptions",
            ],
            "common_failure_cases": [
                "lookahead bias described as acceptable",
                "Sharpe ratio or return formula misstated",
                "backtest result treated as live-trading proof",
            ],
            "examples": {"high": "accurate"},
        }
    ]
}


def _make_zh_corpus():
    return {
        "items": [
            {
                "sample_id": "sample",
                "task_id": "B03_lookahead_prevention",
                "category": "backtest",
                "persona_id": "finance_veteran",
                "transcript_source": "synthetic_adversarial",
                "track": "tutor",
                "dimension": "D4_instructional_accuracy",
                "registry_rubric_id": "quant_correctness.v1",
                "conversation": [
                    {"role": "user", "content": "Is today-close sizing lookahead bias?"},
                    {"role": "assistant", "content": "Yes. Shift the signal one bar."},
                ],
            }
        ]
    }


def test_review_packet_zh_language_translates_rubric_metadata_and_form(tmp_path):
    packet = build_review_packet(
        corpus=_make_zh_corpus(),
        rubric_registry=_QUANT_CORRECTNESS_CANONICAL_REGISTRY,
        language="zh",
    )
    item = packet["items"][0]
    paths = write_review_packet(packet=packet, output_dir=tmp_path)

    assert packet["language"] == "zh"
    assert "前视偏差" in item["common_failure_cases"][0]
    assert item["score_anchors"]["1"].endswith("。")
    assert "Shift the signal one bar" in item["review_context"]
    assert "Turn 1 - User" in item["review_context"]
    assert "Turn 2 - Assistant" in item["review_context"]
    assert item["content_kind"] == "conversation"

    md_path = Path(paths["markdown"])
    assert md_path.name == "human_review_packet_zh.md"
    md_text = md_path.read_text(encoding="utf-8")
    assert "裁判验证人工评审包" in md_text
    assert "题目 1 / 1" in md_text
    assert "B03 · 防止前视偏差" in md_text
    assert "资深金融从业者" in md_text
    assert "教学准确度" in md_text
    assert "对话内容（请阅读下面这段英文对话）" in md_text
    assert "User` = 用户" in md_text
    assert "Turn 1 - User" in md_text
    assert "Turn 2 - Assistant" in md_text
    assert "评分锚点" in md_text
    assert "填 Google Form 时复制以下字段" in md_text
    assert "Shift the signal one bar" in md_text
    assert "jv_review_001" in md_text

    form_path = Path(paths["google_form_zh"])
    assert form_path.name == "google_form_zh.md"
    form_text = form_path.read_text(encoding="utf-8")
    assert "专家 ID (reviewer_id)" in form_text
    assert "高" in form_text and "低" in form_text
    assert "reviewer_id / 专家 ID" not in form_text
    assert "答非所问" not in form_text or "直接倾泻答案" in form_text


def test_review_packet_zh_evaluation_context_uses_context_heading(tmp_path):
    corpus = {
        "items": [
            {
                "sample_id": "qr_sample",
                "task_id": "X01_ma_offbyone",
                "category": "debug",
                "persona_id": "developer_crossover",
                "transcript_source": "synthetic_adversarial",
                "track": "qr",
                "dimension": "result_judge",
                "registry_rubric_id": "quant_correctness.v1",
                "context": "## Task\nFix the lookahead bug.\n## Acceptance criteria\nSharpe > 0.",
            }
        ]
    }

    packet = build_review_packet(
        corpus=corpus,
        rubric_registry=_QUANT_CORRECTNESS_CANONICAL_REGISTRY,
        language="zh",
    )
    paths = write_review_packet(packet=packet, output_dir=tmp_path)

    assert packet["items"][0]["content_kind"] == "evaluation_context"
    md_text = Path(paths["markdown"]).read_text(encoding="utf-8")
    assert "评估上下文" in md_text
    assert "对话内容（请阅读下面这段英文对话）" not in md_text
    assert "Fix the lookahead bug" in md_text


def test_review_packet_zh_raises_when_registry_text_diverges_from_translation():
    registry = {
        "rubrics": [
            {
                "rubric_id": "quant_correctness.v1",
                "version": "v1",
                "dimension": "quant_correctness",
                "score_scale": {"min": 1, "max": 5},
                "score_anchors": {"1": "wrong", "5": "excellent"},
                "required_evidence": ["formula"],
                "common_failure_cases": ["lookahead"],
            }
        ]
    }

    try:
        build_review_packet(
            corpus=_make_zh_corpus(),
            rubric_registry=registry,
            language="zh",
        )
    except ValueError as exc:
        assert "quant_correctness.v1" in str(exc)
        assert "score_anchors" in str(exc)
        return
    raise AssertionError("expected ValueError when rubric text diverges from bundled ZH baseline")


def test_review_packet_zh_writes_suffixed_sample_map_to_preserve_en_exports(tmp_path):
    write_review_packet(
        packet=build_review_packet(
            corpus=_make_zh_corpus(),
            rubric_registry=_QUANT_CORRECTNESS_CANONICAL_REGISTRY,
            language="en",
        ),
        output_dir=tmp_path,
    )
    en_sample_map_path = tmp_path / "human_review_sample_map.json"
    assert en_sample_map_path.exists()
    en_payload = json.loads(en_sample_map_path.read_text(encoding="utf-8"))

    paths = write_review_packet(
        packet=build_review_packet(
            corpus=_make_zh_corpus(),
            rubric_registry=_QUANT_CORRECTNESS_CANONICAL_REGISTRY,
            language="zh",
        ),
        output_dir=tmp_path,
    )

    zh_sample_map_path = Path(paths["sample_map"])
    assert zh_sample_map_path.name == "human_review_sample_map_zh.json"
    assert en_sample_map_path.read_text(encoding="utf-8") == json.dumps(
        en_payload, indent=2, ensure_ascii=False
    )


def test_review_packet_zh_csv_merges_with_en_labels_via_header_fallback(tmp_path):
    rows = [
        {
            "专家 ID (reviewer_id)": "expert_zh_1",
            "样本 ID (sample_id)": "jv_review_001",
            "评分规则 ID (rubric_id)": "quant_correctness.v1",
            "评分维度 (dimension)": "D4_instructional_accuracy",
            "专家评分 (human_score)": "4",
            "评分信心 (confidence)": "高",
            "专家理由 (human_rationale)": "shift one bar 的结论正确",
            "证据片段 (evidence_spans)": "Shift the signal one bar.",
            "失败标签 (failure_tags)": "",
            "备注 (notes)": "",
        }
    ]

    labels = labels_from_csv_rows(rows)

    assert len(labels) == 1
    label = labels[0]
    assert label["reviewer_id"] == "expert_zh_1"
    assert label["sample_id"] == "jv_review_001"
    assert label["rubric_id"] == "quant_correctness.v1"
    assert label["human_score"] == 4
    assert label["confidence"] == "high"


def test_review_packet_zh_language_rejects_invalid_language(tmp_path):
    corpus = {"items": []}
    registry = {"rubrics": []}
    try:
        build_review_packet(
            corpus=corpus,
            rubric_registry=registry,
            language="fr",
        )
    except ValueError as exc:
        assert "language" in str(exc)
        return
    raise AssertionError("expected ValueError for unsupported language")


def test_export_review_packet_cli_honors_output_dir(tmp_path):
    corpus_path = tmp_path / "corpus.json"
    registry_path = tmp_path / "registry.json"
    output_dir = tmp_path / "results"
    corpus_path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "sample_id": "sample",
                        "task_id": "task",
                        "category": "debug",
                        "persona_id": "developer_crossover",
                        "transcript_source": "synthetic_adversarial",
                        "track": "qr",
                        "dimension": "result_judge",
                        "registry_rubric_id": "code_correctness.v1",
                        "context": "## Task\nFix the bug.",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    registry_path.write_text(
        json.dumps(
            {
                "rubrics": [
                    {
                        "rubric_id": "code_correctness.v1",
                        "version": "v1",
                        "dimension": "code_correctness",
                        "score_scale": {"min": 1, "max": 5},
                        "score_anchors": {"1": "broken", "5": "robust"},
                        "required_evidence": ["test output"],
                        "common_failure_cases": ["runtime error"],
                        "examples": {"high": "tested fix"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    rc = judge_run.main(
        [
            "--corpus",
            str(corpus_path),
            "--output-dir",
            str(output_dir),
            "export-review-packet",
            "--rubric-registry",
            str(registry_path),
        ]
    )

    assert rc == 0
    assert (output_dir / "human_review_packet" / "google_form_bilingual.md").exists()


def _label(
    sample_id: str,
    dimension: str,
    score: int,
    reviewer_id: str,
    *,
    rubric_id: str = "quant_correctness.v1",
    rationale: str = "ok",
):
    return {
        "sample_id": sample_id,
        "transcript_id": sample_id,
        "rubric_id": rubric_id,
        "dimension": dimension,
        "human_score": score,
        "confidence": "medium",
        "human_rationale": rationale,
        "reviewer_id": reviewer_id,
    }


def test_inter_rater_skips_single_reviewer_samples():
    labels = [
        normalize_human_label(_label("s1", "D1_finance_adaptation", 3, "a")),
        normalize_human_label(_label("s2", "D1_finance_adaptation", 4, "a")),
    ]
    stats = compute_inter_rater_agreement(labels)
    assert stats["counts"]["overlapping_groups"] == 0
    assert stats["counts"]["reviewer_pair_comparisons"] == 0
    assert stats["overall"]["comparisons"] == 0


def test_inter_rater_perfect_agreement_when_scores_match():
    labels = [
        normalize_human_label(_label("s1", "D1_finance_adaptation", 4, "a")),
        normalize_human_label(_label("s1", "D1_finance_adaptation", 4, "b")),
    ]
    stats = compute_inter_rater_agreement(labels)
    assert stats["counts"]["overlapping_groups"] == 1
    assert stats["counts"]["reviewer_pair_comparisons"] == 1
    assert stats["counts"]["unique_reviewers"] == 2
    assert stats["overall"]["exact_agreement"] == 1.0
    assert stats["overall"]["within_one_agreement"] == 1.0
    assert stats["overall"]["mean_absolute_delta"] == 0.0


def test_inter_rater_flags_large_disagreements():
    labels = [
        normalize_human_label(_label("s1", "D2_code_adaptation", 5, "a")),
        normalize_human_label(_label("s1", "D2_code_adaptation", 1, "b")),
        normalize_human_label(_label("s2", "D2_code_adaptation", 4, "a")),
        normalize_human_label(_label("s2", "D2_code_adaptation", 3, "b")),
    ]
    stats = compute_inter_rater_agreement(labels)
    assert stats["counts"]["reviewer_pair_comparisons"] == 2
    assert stats["overall"]["exact_agreement"] == 0.0
    assert stats["overall"]["within_one_agreement"] == 0.5
    assert stats["overall"]["mean_absolute_delta"] == 2.5
    assert len(stats["disagreements"]) == 1
    disagreement = stats["disagreements"][0]
    assert disagreement["sample_id"] == "s1"
    assert disagreement["absolute_delta"] == 4


def test_inter_rater_groups_by_dimension_and_reviewer_pair():
    labels = [
        normalize_human_label(_label("s1", "D1_finance_adaptation", 4, "a")),
        normalize_human_label(_label("s1", "D1_finance_adaptation", 5, "b")),
        normalize_human_label(_label("s2", "D3_pedagogical_method", 3, "a")),
        normalize_human_label(_label("s2", "D3_pedagogical_method", 3, "c")),
    ]
    stats = compute_inter_rater_agreement(labels)
    by_dim = stats["by_dimension"]
    assert set(by_dim.keys()) == {"D1_finance_adaptation", "D3_pedagogical_method"}
    assert by_dim["D1_finance_adaptation"]["mean_absolute_delta"] == 1.0
    assert by_dim["D3_pedagogical_method"]["mean_absolute_delta"] == 0.0

    by_pair = stats["by_reviewer_pair"]
    assert set(by_pair.keys()) == {"a | b", "a | c"}
    assert by_pair["a | b"]["comparisons"] == 1
    assert by_pair["a | c"]["comparisons"] == 1


def test_judge_vs_reviewer_mean_excludes_single_reviewer_samples():
    comparisons = [
        {
            "sample_id": "s1",
            "rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "reviewer_id": "a",
            "human_score": 4,
            "judge_mean_score": 3.0,
        },
        {
            "sample_id": "s1",
            "rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "reviewer_id": "b",
            "human_score": 5,
            "judge_mean_score": 3.0,
        },
        {
            "sample_id": "s2",
            "rubric_id": "quant_correctness.v1",
            "dimension": "D4_instructional_accuracy",
            "reviewer_id": "a",
            "human_score": 4,
            "judge_mean_score": 4.0,
        },
    ]
    stats = compute_judge_vs_reviewer_mean(comparisons)
    # Only s1 has 2 reviewers
    assert stats["counts"]["sample_dim_groups"] == 1
    assert stats["counts"]["distinct_samples"] == 1
    sample_row = stats["sample_dim_groups"][0]
    assert sample_row["sample_id"] == "s1"
    # mean human score = (4 + 5) / 2 = 4.5; judge_mean = 3.0; signed_delta = -1.5
    assert sample_row["human_mean_score"] == 4.5
    assert sample_row["signed_delta"] == -1.5
    assert sample_row["absolute_delta"] == 1.5
    assert sample_row["within_one_agreement"] is False  # |1.5| > 1


def test_inter_rater_skips_same_reviewer_duplicates():
    """A reviewer submitting two labels on the same (sample,dim) is not an
    inter-rater overlap and must not be counted as one."""

    labels = [
        normalize_human_label(_label("s1", "D1_finance_adaptation", 4, "a")),
        normalize_human_label(_label("s1", "D1_finance_adaptation", 2, "a")),
    ]
    stats = compute_inter_rater_agreement(labels)
    assert stats["counts"]["overlapping_groups"] == 0
    assert stats["counts"]["reviewer_pair_comparisons"] == 0
    assert stats["counts"]["unique_reviewers"] == 0
    assert stats["overall"]["comparisons"] == 0


def test_inter_rater_ignores_same_reviewer_row_in_mixed_group():
    """Mixed group with one reviewer duplicating + another reviewer:
    only the cross-reviewer comparisons count."""

    labels = [
        normalize_human_label(_label("s1", "D1_finance_adaptation", 4, "a")),
        normalize_human_label(_label("s1", "D1_finance_adaptation", 2, "a")),
        normalize_human_label(_label("s1", "D1_finance_adaptation", 3, "b")),
    ]
    stats = compute_inter_rater_agreement(labels)
    assert stats["counts"]["overlapping_groups"] == 1
    # Two cross-reviewer comparisons: (a=4, b=3) and (a=2, b=3).
    # Skip the (a, a) pair.
    assert stats["counts"]["reviewer_pair_comparisons"] == 2
    assert stats["by_reviewer_pair"]["a | b"]["comparisons"] == 2


def test_judge_vs_reviewer_mean_skips_same_reviewer_duplicates():
    """Duplicate comparable rows from one reviewer do not constitute a
    multi-reviewer sample."""

    comparisons = [
        {
            "sample_id": "s1",
            "rubric_id": "rubric.v1",
            "dimension": "D4_instructional_accuracy",
            "reviewer_id": "a",
            "human_score": 4,
            "judge_mean_score": 3.0,
        },
        {
            "sample_id": "s1",
            "rubric_id": "rubric.v1",
            "dimension": "D4_instructional_accuracy",
            "reviewer_id": "a",
            "human_score": 2,
            "judge_mean_score": 3.0,
        },
    ]
    stats = compute_judge_vs_reviewer_mean(comparisons)
    assert stats["counts"]["sample_dim_groups"] == 0
    assert stats["counts"]["distinct_samples"] == 0


def test_judge_vs_reviewer_mean_collapses_same_reviewer_duplicates_before_mean():
    """If reviewer a submits twice (4, 2) and reviewer b submits once (3),
    the reviewer mean uses a's average 3.0, not the per-label mean."""

    comparisons = [
        {
            "sample_id": "s1",
            "rubric_id": "rubric.v1",
            "dimension": "D4_instructional_accuracy",
            "reviewer_id": "a",
            "human_score": 4,
            "judge_mean_score": 3.0,
        },
        {
            "sample_id": "s1",
            "rubric_id": "rubric.v1",
            "dimension": "D4_instructional_accuracy",
            "reviewer_id": "a",
            "human_score": 2,
            "judge_mean_score": 3.0,
        },
        {
            "sample_id": "s1",
            "rubric_id": "rubric.v1",
            "dimension": "D4_instructional_accuracy",
            "reviewer_id": "b",
            "human_score": 3,
            "judge_mean_score": 3.0,
        },
    ]
    stats = compute_judge_vs_reviewer_mean(comparisons)
    assert stats["counts"]["sample_dim_groups"] == 1
    row = stats["sample_dim_groups"][0]
    # a_mean = 3, b_mean = 3, group mean = 3; judge 3 → signed 0
    assert row["human_scores_per_reviewer"] == {"a": 3.0, "b": 3.0}
    assert row["human_mean_score"] == 3.0
    assert row["signed_delta"] == 0.0


def test_judge_vs_reviewer_mean_handles_reviewer_agreement():
    comparisons = [
        {
            "sample_id": "s1",
            "rubric_id": "rubric.v1",
            "dimension": "D1_finance_adaptation",
            "reviewer_id": "a",
            "human_score": 4,
            "judge_mean_score": 3.5,
        },
        {
            "sample_id": "s1",
            "rubric_id": "rubric.v1",
            "dimension": "D1_finance_adaptation",
            "reviewer_id": "b",
            "human_score": 4,
            "judge_mean_score": 3.5,
        },
    ]
    stats = compute_judge_vs_reviewer_mean(comparisons)
    assert stats["counts"]["sample_dim_groups"] == 1
    assert stats["counts"]["distinct_samples"] == 1
    sample_row = stats["sample_dim_groups"][0]
    assert sample_row["human_mean_score"] == 4.0
    assert sample_row["human_score_span"] == 0.0
    assert sample_row["signed_delta"] == -0.5
    assert sample_row["within_one_agreement"] is True
