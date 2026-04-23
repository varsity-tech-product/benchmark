import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "bench"))

from experiments.judge_validation import run as judge_run
from experiments.judge_validation.human_alignment import (
    compute_human_alignment_stats,
    convert_csv_to_human_labels,
    labels_from_csv_rows,
    normalize_human_label,
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
    assert stats["stage3_gate"]["status"] == "pass"


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
    assert stats["stage3_gate"]["status"] == "needs_review"


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
    assert stats["stage3_gate"]["status"] == "needs_review"


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
            "Human Rationale": "Correct lookahead explanation.",
        }
    ]

    labels = labels_from_csv_rows(rows)

    assert labels[0]["reviewer_id"] == "expert@example.com"


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
