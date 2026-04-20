"""Unit tests for all TutoringSession completion paths.

Tests TC met, max turns, repeat detection, deadline timeout,
goal checker, and force_complete — all without server or HTTP.
"""

import json
import time

from tests.conftest import FakeTCChecker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _send(session, text="Here is how to fix the bug."):
    """Send a message and return parsed JSON result."""
    return json.loads(session.handle_send_message(text))


# ---------------------------------------------------------------------------
# TC Completion
# ---------------------------------------------------------------------------


class TestTCCompletion:
    def test_tc_met_returns_completed(self, make_session):
        tc = FakeTCChecker(complete_on_check=1)
        session = make_session(tc_checker=tc)
        result = _send(session)
        assert result["status"] == "completed"
        assert result["reason"] == "objectives_met"

    def test_tc_met_sets_done_flag(self, make_session):
        tc = FakeTCChecker(complete_on_check=1)
        session = make_session(tc_checker=tc)
        _send(session)
        assert session.done is True

    def test_tc_met_appends_closing(self, make_session):
        tc = FakeTCChecker(complete_on_check=1)
        session = make_session(tc_checker=tc)
        result = _send(session)
        assert result["student_message"]  # closing message present
        # Conversation should end with a user (student) closing
        conv = session.conversation
        assert conv[-1]["role"] == "user"

    def test_tc_not_met_stays_active(self, make_session):
        tc = FakeTCChecker()  # never completes
        session = make_session(tc_checker=tc)
        result = _send(session)
        assert result["status"] == "active"
        assert session.done is False

    def test_tc_met_on_second_turn(self, make_session):
        tc = FakeTCChecker(complete_on_check=2)
        session = make_session(tc_checker=tc)
        r1 = _send(session, "First message")
        assert r1["status"] == "active"
        r2 = _send(session, "Second message")
        assert r2["status"] == "completed"
        assert r2["reason"] == "objectives_met"

    def test_send_after_tc_met_returns_completed(self, make_session):
        tc = FakeTCChecker(complete_on_check=1)
        session = make_session(tc_checker=tc)
        _send(session)
        result = _send(session, "Another message")
        assert result["status"] == "completed"


# ---------------------------------------------------------------------------
# Max Turns Completion
# ---------------------------------------------------------------------------


class TestMaxTurnsCompletion:
    def test_max_turns_1_completes(self, make_session):
        session = make_session(max_turns=1)
        result = _send(session)
        assert result["status"] == "completed"
        assert result["reason"] == "max_turns"

    def test_max_turns_3_active_then_completes(self, make_session):
        session = make_session(max_turns=3)
        for i in range(2):
            r = _send(session, f"Message {i}")
            assert r["status"] == "active"
        r = _send(session, "Last message")
        assert r["status"] == "completed"
        assert r["reason"] == "max_turns"

    def test_max_turns_sets_done(self, make_session):
        session = make_session(max_turns=1)
        _send(session)
        assert session.done is True
        assert session.completion_reason == "max_turns"


# ---------------------------------------------------------------------------
# Repeat Detection
# ---------------------------------------------------------------------------


class TestRepeatDetection:
    def test_agent_stuck_after_3_repeats(self, make_session):
        session = make_session()
        same_text = "I already told you the answer."
        _send(session, same_text)  # 1st — ok
        _send(session, same_text)  # 2nd — repeat_count=1
        result = _send(session, same_text)  # 3rd — repeat_count=2 → stuck
        assert result["status"] == "completed"
        assert result["reason"] == "agent_stuck"

    def test_different_messages_no_stuck(self, make_session):
        session = make_session()
        for i in range(5):
            r = _send(session, f"Message {i}")
            assert r["status"] == "active"

    def test_repeat_reset_on_different_message(self, make_session):
        session = make_session()
        _send(session, "A")
        _send(session, "A")  # repeat_count=1
        _send(session, "B")  # reset
        _send(session, "B")  # repeat_count=1
        r = _send(session, "C")  # reset again
        assert r["status"] == "active"

    def test_agent_stuck_sets_done(self, make_session):
        session = make_session()
        msg = "Same thing"
        _send(session, msg)
        _send(session, msg)
        _send(session, msg)
        assert session.done is True
        assert session.completion_reason == "agent_stuck"


# ---------------------------------------------------------------------------
# Deadline Timeout
# ---------------------------------------------------------------------------


class TestDeadlineCompletion:
    def test_expired_deadline_completes(self, make_session):
        session = make_session(deadline=time.time() - 10)
        result = _send(session)
        assert result["status"] == "completed"
        assert result["reason"] == "timeout"

    def test_future_deadline_stays_active(self, make_session):
        session = make_session(deadline=time.time() + 3600)
        result = _send(session)
        assert result["status"] == "active"

    def test_deadline_sets_done(self, make_session):
        session = make_session(deadline=time.time() - 1)
        _send(session)
        assert session.done is True
        assert session.completion_reason == "timeout"


# ---------------------------------------------------------------------------
# force_complete
# ---------------------------------------------------------------------------


class TestForceComplete:
    def test_force_complete_marks_done(self, make_session):
        session = make_session()
        session.force_complete("timeout")
        assert session.done is True
        assert session.completion_reason == "timeout"

    def test_force_complete_appends_closing_when_last_is_assistant(self, make_session):
        session = make_session()
        _send(session)  # generates assistant + student turn
        # Now add an assistant message as the last
        session._conversation.append({"role": "assistant", "content": "bye"})
        closing = session.force_complete("timeout")
        assert closing  # closing message returned
        assert session.conversation[-1]["role"] == "user"

    def test_force_complete_no_closing_when_last_is_user(self, make_session):
        session = make_session()
        # Last message is user (student opening)
        closing = session.force_complete("timeout")
        assert closing == ""

    def test_force_complete_idempotent(self, make_session):
        session = make_session()
        session.force_complete("timeout")
        result = session.force_complete("max_turns")
        assert result == ""
        assert session.completion_reason == "timeout"  # first reason kept


# ---------------------------------------------------------------------------
# Conversation state
# ---------------------------------------------------------------------------


class TestConversationState:
    def test_conversation_has_opening(self, make_session):
        session = make_session()
        assert len(session.conversation) == 1
        assert session.conversation[0]["role"] == "user"

    def test_send_adds_assistant_and_user(self, make_session):
        session = make_session()
        _send(session)
        conv = session.conversation
        assert conv[1]["role"] == "assistant"
        assert conv[2]["role"] == "user"

    def test_turn_count_increments(self, make_session):
        session = make_session()
        assert session.turn == 0
        _send(session)
        assert session.turn == 1
        _send(session, "Second")
        assert session.turn == 2

    def test_empty_message_rejected(self, make_session):
        session = make_session()
        result = json.loads(session.handle_send_message(""))
        assert "error" in result
        assert session.turn == 0
