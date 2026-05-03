"""Unit tests for SessionState._trigger_auto_eval (issue #126 / P0).

Auto-eval enqueues a server-internal eval after a terminal transition.
It is idempotent on the in-memory ``_eval_status`` guard and on the
``auto:{session_id}`` key in the score store, so duplicate triggers
collapse onto the same score record.
"""

import pytest

from server.api.protocol import SessionPhase


@pytest.fixture
def state(bench_root):
    from server.api.session_api import SessionState

    return SessionState(
        session_id="auto-eval-test-001",
        use_docker=False,
        bench_root=bench_root,
        eval_model="fake-model",
    )


class TestAutoEvalGuards:
    def test_no_op_before_completed(self, state, monkeypatch):
        calls = []
        monkeypatch.setattr(state, "request_evaluation", lambda: calls.append(1))
        state.phase = SessionPhase.IN_SESSION
        state._result_dir = "/tmp/x"
        state._trigger_auto_eval()
        assert calls == []

    def test_no_op_without_result_dir(self, state, monkeypatch):
        calls = []
        monkeypatch.setattr(state, "request_evaluation", lambda: calls.append(1))
        state.phase = SessionPhase.COMPLETED
        state._result_dir = None
        state._trigger_auto_eval()
        assert calls == []

    def test_no_op_when_eval_already_started(self, state, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(
            state, "request_evaluation", lambda: calls.append(1) or {}
        )
        state.phase = SessionPhase.COMPLETED
        state._result_dir = tmp_path
        state._eval_status = "running"
        state._trigger_auto_eval()
        assert calls == []


class TestAutoEvalEnqueue:
    def test_sets_auto_idempotency_key(self, state, monkeypatch, tmp_path):
        seen = {}

        def _fake_eval(*, eval_mode=None, eval_model=None, idempotency_key=None):
            seen["key"] = idempotency_key
            seen["mode"] = eval_mode
            return {"status": "running", "score_id": "score_1"}

        monkeypatch.setattr(state, "request_evaluation", _fake_eval)
        state.phase = SessionPhase.COMPLETED
        state._result_dir = tmp_path
        state._trigger_auto_eval()
        assert seen["key"] == f"auto:{state.session_id}"
        assert seen["mode"] == "full"

    def test_swallows_request_evaluation_exception(
        self, state, monkeypatch, tmp_path, caplog
    ):
        def _boom(*, eval_mode=None, eval_model=None, idempotency_key=None):
            raise RuntimeError("eval pipeline broken")

        monkeypatch.setattr(state, "request_evaluation", _boom)
        state.phase = SessionPhase.COMPLETED
        state._result_dir = tmp_path
        state._trigger_auto_eval()  # must not raise
        assert "auto-eval" in caplog.text


class TestAutoEvalIdempotency:
    def test_second_trigger_after_running_is_noop(
        self, state, monkeypatch, tmp_path
    ):
        calls = []

        def _fake_eval(*, eval_mode=None, eval_model=None, idempotency_key=None):
            calls.append(idempotency_key)
            state._eval_status = "running"
            return {"status": "running", "score_id": "score_1"}

        monkeypatch.setattr(state, "request_evaluation", _fake_eval)
        state.phase = SessionPhase.COMPLETED
        state._result_dir = tmp_path

        state._trigger_auto_eval()
        state._trigger_auto_eval()
        assert calls == [f"auto:{state.session_id}"]

    def test_ignores_concurrent_operator_param_mutation(
        self, state, monkeypatch, tmp_path
    ):
        """Operator mutation of _eval_mode / _eval_idempotency_key just before
        auto-eval fires must not leak into score_1."""
        seen = {}

        def _fake_eval(*, eval_mode=None, eval_model=None, idempotency_key=None):
            seen["mode"] = eval_mode
            seen["key"] = idempotency_key
            return {"status": "running", "score_id": "score_1"}

        monkeypatch.setattr(state, "request_evaluation", _fake_eval)
        state.phase = SessionPhase.COMPLETED
        state._result_dir = tmp_path
        # Simulate ops_evaluate that mutated the instance fields outside
        # of _request_lock just before completion fired.
        state._eval_mode = "qp"
        state._eval_idempotency_key = "operator-key"
        state._trigger_auto_eval()
        assert seen["mode"] == "full"
        assert seen["key"] == f"auto:{state.session_id}"
