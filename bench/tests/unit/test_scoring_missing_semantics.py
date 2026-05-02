from server.eval.core.scoring import (
    compute_benchmark_kpis,
    compute_overall,
    compute_task_score,
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


def test_single_track_mode_uses_only_requested_track():
    score = compute_task_score(
        quant_result_score=0.8,
        quant_process_score=None,
        eval_mode="qr",
    )

    assert score["overall_score"] == 0.8


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
