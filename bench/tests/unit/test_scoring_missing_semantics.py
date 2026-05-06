from eval.core.scoring import (
    PASS_THRESHOLD,
    PASS_THRESHOLD_CALIBRATED,
    PASS_THRESHOLD_VERSION,
    compute_benchmark_kpis,
    compute_overall,
    compute_task_pass,
    compute_task_score,
    task_pass_threshold_metadata,
)


def test_full_mode_missing_requested_component_is_not_computable():
    score = compute_task_score(
        quant_result_score=0.8,
        quant_process_score=None,
        eval_mode="full",
    )

    assert score["quant_result_score"] == 0.8
    assert score["quant_process_score"] is None
    assert score["quant_agent_score"] is None
    assert score["overall_score"] is None
    assert score["task_pass"] is None


def test_single_track_mode_uses_only_requested_track():
    score = compute_task_score(
        quant_result_score=0.8,
        quant_process_score=None,
        eval_mode="qr",
    )

    assert score["overall_score"] == 0.8
    assert score["task_pass"] is True


def test_calibrated_task_pass_threshold_is_versioned():
    assert PASS_THRESHOLD_CALIBRATED is True
    assert PASS_THRESHOLD_VERSION == "task_pass_threshold_v1"
    assert PASS_THRESHOLD == 0.5

    below = compute_task_score(
        quant_result_score=0.49,
        quant_process_score=0.49,
    )
    at_threshold = compute_task_score(
        quant_result_score=0.5,
        quant_process_score=0.5,
    )
    metadata = task_pass_threshold_metadata()

    assert below["task_pass"] is False
    assert at_threshold["task_pass"] is True
    assert metadata["version"] == PASS_THRESHOLD_VERSION
    assert metadata["value"] == PASS_THRESHOLD
    assert metadata["source"]["validation_run_id"] == "jv_20260429_stage3_combined"


def test_task_pass_compares_against_exposed_score_value():
    assert compute_task_pass(0.49996) is False
    assert compute_task_pass(0.5) is True


def test_compute_task_score_applies_threshold_before_rounding():
    score = compute_task_score(
        quant_result_score=0.49996,
        quant_process_score=0.49996,
    )

    assert score["task_score"] == 0.5
    assert score["task_pass"] is False


def test_benchmark_kpis_skip_not_computable_results():
    computable = compute_task_score(
        quant_result_score=0.8,
        quant_process_score=0.6,
    )
    not_computable = compute_task_score(
        quant_result_score=0.8,
        quant_process_score=None,
    )

    kpis = compute_benchmark_kpis([computable, not_computable])

    assert kpis["total_tasks_evaluated"] == 1
    assert kpis["total_tasks_not_computable"] == 1
    assert kpis["overall_agent_score"] == computable["overall_score"]


def test_compute_overall_preserves_missing_requested_track():
    assert (
        compute_overall(
            qr={"score": 0.8},
            qp={"score": None},
            eval_mode="full",
        )
        is None
    )
