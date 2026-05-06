from __future__ import annotations

import json
from pathlib import Path

from eval.backfill.run_state_to_bundle import backfill
from eval.contracts import bundle_io
from eval.storage.human_reviews import HumanReviewStore
from server.auth import UserContext


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _user(n: int) -> UserContext:
    return UserContext(
        user_id=f"github:reviewer-{n}",
        github_login=f"reviewer-{n}",
        github_user_id=str(1000 + n),
        email=f"reviewer-{n}@example.com",
        display_name=f"Reviewer {n}",
        avatar_url="",
        role="reviewer",
    )


def _payload(session_id: str, task_id: str, score_a: int, score_b: int) -> dict:
    return {
        "session_id": session_id,
        "bundle_id": session_id,
        "task_id": task_id,
        "criteria": [
            {
                "criterion_id": "task_completion.v1",
                "score": score_a,
                "justification": "Task completion evidence is present.",
            },
            {
                "criterion_id": "quant_correctness.v1",
                "score": score_b,
                "justification": "Quant reasoning evidence is present.",
            },
        ],
    }


def test_human_reviews_compute_irr_and_export_with_bundle(tmp_path):
    result_dir = tmp_path / "results" / "server" / "L1_DEMO" / "persona" / "run"
    session_id = "sess-human-review"
    task_id = "L1_DEMO"
    _write_json(
        result_dir / "run_state.json",
        {
            "session_id": session_id,
            "task_id": task_id,
            "persona_id": "persona",
            "timestamp": "2026-05-06T01:00:00Z",
            "conversation": [{"role": "assistant", "content": "Done"}],
            "tool_logs": [],
        },
    )
    _write_json(
        result_dir / "evaluations" / "index.json",
        {
            "version": "2.0",
            "latest_completed_score_id": "score_1",
            "scores": [{"score_id": "score_1", "status": "completed_scored"}],
        },
    )
    _write_json(
        result_dir / "evaluations" / "score_1" / "score.json",
        {
            "score_id": "score_1",
            "score_status": "completed_scored",
            "overall_score": 0.8,
        },
    )

    store = HumanReviewStore()
    store.submit_review(result_dir, "score_1", _user(1), _payload(session_id, task_id, 4, 3))
    store.submit_review(result_dir, "score_1", _user(2), _payload(session_id, task_id, 4, 3))
    store.submit_review(result_dir, "score_1", _user(3), _payload(session_id, task_id, 5, 3))

    summary = store.summary(result_dir, "score_1")
    assert summary["review_count"] == 3
    assert summary["reviewer_count"] == 3
    assert summary["irr"]["status"] == "computed"
    assert len(summary["irr"]["per_criterion"]) == 2

    bundle_path = backfill(result_dir / "run_state.json", bench_root=tmp_path)
    raw = json.loads(bundle_path.read_text(encoding="utf-8"))
    assert "human_reviews" in raw
    assert len(raw["human_reviews"]) == 4
    assert all("result_dir" not in item for item in raw["human_reviews"])
    assert raw["human_reviews"][-1]["record_type"] == "irr_summary"

    bundle = bundle_io.read(bundle_path)
    assert len(bundle.human_reviews) == 4


def test_human_review_requires_completed_score_json(tmp_path):
    result_dir = tmp_path / "results" / "server" / "L1_DEMO" / "persona" / "run"
    (result_dir / "evaluations" / "score_1").mkdir(parents=True)

    store = HumanReviewStore()
    try:
        store.submit_review(
            result_dir,
            "score_1",
            _user(1),
            _payload("session", "task", 4, 3),
        )
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("review write accepted a score directory without score.json")
