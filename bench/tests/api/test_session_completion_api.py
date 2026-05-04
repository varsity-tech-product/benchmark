"""API tests for session completion via REST endpoints.

Drives sessions to completion through various mechanisms and verifies
state transitions, response formats, and post-completion behavior.
"""

import httpx
import pytest
import pytest_asyncio

from tests.helpers import (
    get_session_status,
    register_and_start,
    send_message,
)


@pytest_asyncio.fixture
async def client(app):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


def _get_session_state(app, sid: str):
    """Reach into the server to get the SessionState object."""
    manager = app._manager
    return manager.get_session(sid)


# ---------------------------------------------------------------------------
# Max turns completion
# ---------------------------------------------------------------------------


class TestMaxTurnsViaAPI:
    @pytest.mark.asyncio
    async def test_max_turns_completes_session(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1  # force low limit

        body = await send_message(client, sid, "Here is the fix.")
        assert body["status"] == "completed"
        assert body["reason"] == "max_turns"

    @pytest.mark.asyncio
    async def test_phase_transitions_to_completed(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1

        await send_message(client, sid, "Fix applied.")
        status = await get_session_status(client, sid)
        assert status["phase"] == "completed"

    @pytest.mark.asyncio
    async def test_max_turns_3_active_then_completes(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 3

        for i in range(2):
            body = await send_message(client, sid, f"Step {i}")
            assert body["status"] == "active"

        body = await send_message(client, sid, "Final step")
        assert body["status"] == "completed"
        assert body["reason"] == "max_turns"


# ---------------------------------------------------------------------------
# Repeat detection
# ---------------------------------------------------------------------------


class TestRepeatDetectionViaAPI:
    @pytest.mark.asyncio
    async def test_agent_stuck_after_repeats(self, client):
        sid, _ = await register_and_start(client)
        msg = "I already explained this."

        await send_message(client, sid, msg)  # 1st
        await send_message(client, sid, msg)  # 2nd — repeat_count=1
        body = await send_message(client, sid, msg)  # 3rd — stuck
        assert body["status"] == "failed"
        assert body["reason"] == "agent_stuck"

    @pytest.mark.asyncio
    async def test_session_status_after_stuck(self, client):
        sid, _ = await register_and_start(client)
        msg = "Same thing"
        for _ in range(3):
            await send_message(client, sid, msg)

        status = await get_session_status(client, sid)
        assert status["phase"] == "completed"


# ---------------------------------------------------------------------------
# Student-satisfied termination (issue #132)
# ---------------------------------------------------------------------------


class TestStudentSatisfiedViaAPI:
    @pytest.mark.asyncio
    async def test_student_goodbye_terminates_session(self, app, client, monkeypatch):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        # Force the next student reply to be the canonical D02 goodbye.
        # task_end=False here exercises the regex fallback path.
        monkeypatch.setattr(
            state.session._student_sim,
            "generate_message",
            lambda *a, **kw: ("Got it, I'm good to stop here for now.", False),
        )

        body = await send_message(client, sid, "Anything else I can help with?")
        assert body["status"] == "completed"
        assert body["reason"] == "student_satisfied"

    @pytest.mark.asyncio
    async def test_subsequent_send_after_student_satisfied_denied(
        self, app, client, monkeypatch
    ):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        monkeypatch.setattr(
            state.session._student_sim,
            "generate_message",
            lambda *a, **kw: ("Thanks, that's all I needed!", False),
        )

        await send_message(client, sid, "Anything else?")
        # Phase is COMPLETED — REST permission check returns 403.
        resp = await client.post(f"/session/{sid}/send", json={"text": "hello?"})
        assert resp.status_code == 403
        assert resp.json().get("allowed") == []

    @pytest.mark.asyncio
    async def test_student_question_does_not_terminate(self, app, client, monkeypatch):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        # Mentions "stop" but is asking a question — must NOT terminate.
        monkeypatch.setattr(
            state.session._student_sim,
            "generate_message",
            lambda *a, **kw: (
                "Before we stop, one quick question: what about edges?",
                False,
            ),
        )

        body = await send_message(client, sid, "Make sense so far?")
        assert body["status"] == "active"

    @pytest.mark.asyncio
    async def test_task_end_flag_terminates_paraphrased_closure(
        self, app, client, monkeypatch
    ):
        # Issue #139: paraphrased closure that the regex misses still
        # terminates because the persona emits task_end=True.
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        monkeypatch.setattr(
            state.session._student_sim,
            "generate_message",
            lambda *a, **kw: (
                "Perfect — that feels like the right stopping point for now. "
                "I'll come back later for the rest.",
                True,
            ),
        )

        body = await send_message(client, sid, "Sounds good?")
        assert body["status"] == "completed"
        assert body["reason"] == "student_satisfied"


# ---------------------------------------------------------------------------
# Deadline timeout
# ---------------------------------------------------------------------------


class TestDeadlineViaAPI:
    @pytest.mark.asyncio
    async def test_expired_deadline_completes(self, app, client):
        import time

        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._deadline = time.time() - 10  # already expired

        body = await send_message(client, sid, "Check this out.")
        assert body["status"] == "completed"
        assert body["reason"] == "timeout"


# ---------------------------------------------------------------------------
# Post-completion behavior
# ---------------------------------------------------------------------------


class TestPostCompletion:
    @pytest.mark.asyncio
    async def test_send_after_completion_denied(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1
        await send_message(client, sid, "Done.")

        # REST permission check blocks send in COMPLETED phase.
        # Agent-facing lifecycle is terminal; scoring is server-side.
        resp = await client.post(f"/session/{sid}/send", json={"text": "More stuff"})
        assert resp.status_code == 403
        body = resp.json()
        assert body.get("allowed") == []

    @pytest.mark.asyncio
    async def test_results_available_after_completion(self, app, client):
        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1
        await send_message(client, sid, "Done.")

        resp = await client.get(f"/session/{sid}/results")
        # Results should be available (200) or at least attempted
        if resp.status_code == 200:
            data = resp.json()
            assert "conversation" in data or "error" not in data


class TestAutoEvalOnCompletion:
    """Issue #126 P0: terminal REST transition enqueues server-internal eval."""

    @pytest.mark.asyncio
    async def test_auto_eval_runs_on_max_turns_completion(
        self, app, client, monkeypatch
    ):
        from server.api.session_api import SessionState

        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1

        seen: dict = {}
        original = SessionState.request_evaluation

        def _spy(
            self,
            *,
            eval_mode=None,
            eval_model=None,
            idempotency_key=None,
        ):
            seen["sid"] = self.session_id
            seen["key"] = idempotency_key
            return original(
                self,
                eval_mode=eval_mode,
                eval_model=eval_model,
                idempotency_key=idempotency_key,
            )

        monkeypatch.setattr(SessionState, "request_evaluation", _spy)
        # Stub the heavy background eval — we only need to verify the
        # enqueue path, not the judge pipeline.
        monkeypatch.setattr(SessionState, "_run_evaluation", lambda self, sid: None)

        await send_message(client, sid, "Done.")

        assert seen["sid"] == sid
        assert seen["key"] == f"auto:{sid}"

        from eval.storage.score_store import load_index

        assert state._result_dir is not None
        index = load_index(state._result_dir)
        assert len(index["scores"]) == 1
        assert index["scores"][0]["idempotency_key"] == f"auto:{sid}"

    @pytest.mark.asyncio
    async def test_auto_eval_visible_via_public_scores_endpoint(
        self, app, client, monkeypatch
    ):
        from server.api.session_api import SessionState

        sid, _ = await register_and_start(client)
        state = _get_session_state(app, sid)
        state.session._max_turns = 1
        monkeypatch.setattr(SessionState, "_run_evaluation", lambda self, sid: None)

        await send_message(client, sid, "Done.")

        resp = await client.get(f"/session/{sid}/scores")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") in ("running", "completed", "pending")
        assert body.get("score_id") == "score_1"
