import json
import threading
import time
from types import SimpleNamespace

from eval.contracts.output import TrackResult
from eval.contracts.request import EvalRequest
from eval.core.coordinator import EvalCoordinator
from eval.judge_reliability import build_judge_reliability_metadata
from eval.storage.score_store import allocate_score_run, get_scores_payload


def test_eval_package_uses_canonical_module_paths():
    from eval.contracts.request import EvalRequest as CanonicalEvalRequest
    from eval.core.coordinator import EvalCoordinator as CanonicalCoordinator
    from eval.judges.process_metrics import evaluate_all_process_metrics
    from eval.programmatic.code_process import _is_code_exec
    from eval.tracks.qr import evaluate as evaluate_qr

    assert CanonicalCoordinator is EvalCoordinator
    assert CanonicalEvalRequest is EvalRequest
    assert callable(evaluate_all_process_metrics)
    assert callable(_is_code_exec)
    assert callable(evaluate_qr)


def test_coordinator_interrupt_sets_track_cancel_and_collects_completed_result(
    monkeypatch,
    tmp_path,
):
    from eval.tracks import qr

    monkeypatch.setattr(
        "eval.judges.runtime.model_resolver.require_ewan_model",
        lambda *args, **kwargs: None,
    )
    track_cancel_seen = threading.Event()

    def fake_qr_evaluate(**kwargs):
        cancel_event = kwargs["cancel_event"]
        deadline = time.time() + 1.0
        while time.time() < deadline:
            if cancel_event.is_set():
                track_cancel_seen.set()
                return TrackResult(
                    track="qr",
                    score=0.42,
                    status="success",
                    detail={"completed_after_cancel": True},
                    blocking_missing=[],
                )
            time.sleep(0.01)
        raise RuntimeError("track cancel was not propagated")

    monkeypatch.setattr(qr, "evaluate", fake_qr_evaluate)
    (tmp_path / "run_state.json").write_text(
        json.dumps(
            {
                "conversation": [
                    {"role": "user", "content": "Question"},
                    {"role": "assistant", "content": "Answer"},
                ],
                "tool_logs": [],
            }
        ),
        encoding="utf-8",
    )

    coordinator_cancel = threading.Event()

    def trigger_interrupt():
        time.sleep(0.05)
        coordinator_cancel.set()

    trigger = threading.Thread(target=trigger_interrupt)
    trigger.start()
    started = time.time()
    output = EvalCoordinator(bench_root=tmp_path).run(
        request=EvalRequest(
            session_id="a" * 32,
            eval_mode="qr",
            eval_model="fake/model",
        ),
        result_dir=tmp_path,
        score_id="score_1",
        task=SimpleNamespace(
            description="Task",
            category="implementation",
            requires_code=False,
            ground_truth=None,
        ),
        persona=SimpleNamespace(persona_id="persona"),
        run_state={
            "conversation": [
                {"role": "user", "content": "Question"},
                {"role": "assistant", "content": "Answer"},
            ],
            "tool_logs": [],
        },
        cancel_event=coordinator_cancel,
        persist=False,
    )
    trigger.join()

    assert output.interrupted is True
    assert output.score_status == "interrupted"
    assert output.qr is not None
    assert output.qr.score == 0.42
    assert output.to_dict()["judge_reliability"]["validation_run_id"]
    assert track_cancel_seen.is_set()
    assert time.time() - started < 0.8


def test_judge_reliability_reference_marks_matching_eval_model():
    metadata = build_judge_reliability_metadata("anthropic/claude-sonnet-4.6")

    assert metadata["validation_run_id"]
    assert metadata["reference_judge_model"] == "anthropic/claude-sonnet-4.6"
    assert metadata["current_eval_model"] == "anthropic/claude-sonnet-4.6"
    assert metadata["current_eval_model_matches_reference"] is True


def test_judge_reliability_reference_matches_legacy_sonnet_aliases():
    for model in ("anthropic/claude-sonnet-4-6", "claude-sonnet-4-6"):
        metadata = build_judge_reliability_metadata(model)

        assert metadata["current_eval_model"] == "anthropic/claude-sonnet-4.6"
        assert metadata["current_eval_model_matches_reference"] is True


def test_qp_model_unavailable_preserves_programmatic_dimensions(monkeypatch):
    from eval.tracks import qp

    monkeypatch.setattr(
        "eval.programmatic.tool_usage.evaluate_tool_usage",
        lambda **kwargs: {"score": 0.8, "status": "success", "reason": "ok"},
    )
    monkeypatch.setattr(
        "eval.programmatic.code_process.evaluate_code_lifecycle",
        lambda logs: {"score": 0.6, "status": "success", "reason": "code used"},
    )
    task = SimpleNamespace(
        description="Implement a function.",
        category="implementation",
        requires_code=True,
        ground_truth=SimpleNamespace(
            expected_mcp_tools=[],
            convenient_tools=[],
            required_capabilities=[],
        ),
    )

    result = qp.evaluate(
        task=task,
        conversation=[{"role": "assistant", "content": "I wrote code."}],
        enriched_conversation=[],
        tool_logs=[],
        distractor_names=[],
        eval_model=None,
        preflight={
            "track_blockers": {
                "qp": [
                    {
                        "code": "eval_model_unavailable",
                        "track": "qp",
                        "reason": "missing key",
                    }
                ]
            }
        },
    )

    assert result.score is None
    assert result.detail["code_lifecycle"]["score"] == 0.6
    assert result.detail["code_lifecycle"]["status"] == "success"
    assert result.detail["task_planning"]["status"] == "failed"
    assert result.detail["_weights_used"]["code_lifecycle"] == 0.15
    assert result.detail["_weights_effective"] == {}


def test_required_tool_coverage_is_post_hoc_programmatic():
    from eval.judges.process_metrics import compute_required_tool_coverage

    logs = [
        SimpleNamespace(name="file_read"),
        SimpleNamespace(name="shell_exec"),
        {"name": "file_read"},
    ]

    result = compute_required_tool_coverage(
        proxy_logs=logs,
        required_tools=["file_read", "shell_exec", "run_backtest"],
    )

    assert result["coverage_ratio"] == 0.6667
    assert result["covered_tools"] == ["file_read", "shell_exec"]
    assert result["missing_tools"] == ["run_backtest"]


def test_required_tool_coverage_exposes_failed_attempts():
    from eval.judges.process_metrics import compute_required_tool_coverage

    logs = [
        SimpleNamespace(name="run_backtest", success=False),
        SimpleNamespace(name="file_read", success=True),
    ]

    result = compute_required_tool_coverage(
        proxy_logs=logs,
        required_tools=["run_backtest", "file_read"],
    )

    assert result["coverage_ratio"] == 1.0
    assert result["covered_tools"] == ["file_read", "run_backtest"]
    assert result["failed_required_tools"] == ["run_backtest"]
    assert result["required_tools_only_failed"] == ["run_backtest"]


def test_multi_score_read_reports_running_entry(tmp_path):
    allocate_score_run(
        tmp_path,
        eval_mode="full",
        eval_model="fake/model",
    )

    payload = get_scores_payload(tmp_path, score_ids=["score_1"])

    assert payload["status"] == "running"
    assert payload["scores"][0]["score_id"] == "score_1"
    assert payload["scores"][0]["score_status"] == "running"
    assert payload["scores"][0]["score"] is None


def test_eval_single_persists_failed_score_for_invalid_run_state(tmp_path):
    from server.scripts import eval_single

    sid = "b" * 32
    results_root = tmp_path / "results"
    result_dir = results_root / "task" / "persona" / f"20260422_000000_{sid[:12]}"
    result_dir.mkdir(parents=True)
    (result_dir / ".session_id").write_text(sid, encoding="utf-8")
    (result_dir / "run_state.json").write_text("{not json", encoding="utf-8")

    code = eval_single.main(
        [
            "--session",
            sid,
            "--results-root",
            str(results_root),
            "--bench-root",
            str(tmp_path),
        ]
    )

    assert code == 2
    score = json.loads(
        (result_dir / "evaluations" / "score_1" / "score.json").read_text(
            encoding="utf-8"
        )
    )
    index = json.loads(
        (result_dir / "evaluations" / "index.json").read_text(encoding="utf-8")
    )
    assert score["score_status"] == "failed"
    assert score["judge_reliability"]["validation_run_id"]
    assert score["preflight"]["hard_errors"][0]["code"] == "run_state_invalid"
    assert index["scores"][0]["status"] == "failed"
