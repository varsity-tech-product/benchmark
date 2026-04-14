"""Tutoring session state manager for QuantTutorBench.

Manages the conversation between agent and simulated student.
Backs the ``send_message`` session flow.

Defense layers aligned with Legacy path (simulation.py create_model_callback):
- TC checker exception isolation
- Student simulator fallback on failure
- Closing generation fallback (hardcoded text)
- Timeout graceful wrap-up with closing
- Agent repeat detection (force-stop after consecutive identical messages)
- Max-turns student closing (aligned with _append_student_closing)
"""

import json
import logging
import re
import textwrap
import time
from typing import Optional

from pydantic import BaseModel

from server.core.artifact_digest import build_visible_artifact_digest
from server.core.tc_evidence import build_turn_evidence
from server.core.workspace_delta import scan_workspace_snapshot

logger = logging.getLogger(__name__)

# Hardcoded closing fallback — aligned with _EfficientSimulator._generate_closing
# (simulation.py:401-405) and StudentSimulator._CLOSING_FALLBACK.
_CLOSING_FALLBACK = (
    "Thanks for walking me through all of this — "
    "I have a much clearer picture now. "
    "I'll try applying these techniques to my own data."
)

# Fallback student message when generate_message() fails — aligned with
# model_callback exception handler (simulation.py:586).
_STUDENT_FALLBACK = "Could you explain that in a bit more detail?"

# Repeat detection threshold — aligned with model_callback _MAX_REPEATS
# (simulation.py:493).
_MAX_REPEATS = 2
_FILE_LIMIT_RE = re.compile(
    r"(can't|cannot|can not|do not|don't|unable to).{0,40}"
    r"(file|files|artifact|artifacts|workspace|open|access|see|read|paste)",
    re.IGNORECASE,
)
# ---------------------------------------------------------------------------
# Session background builder — factual environment description, no directives
# ---------------------------------------------------------------------------


def build_background(task) -> str:
    """Build a factual background description of the session environment.

    Content is determined by what the container actually provides — derived
    from task definition fields, not hardcoded per category.  Contains NO
    behavioural directives or scoring hints.
    """
    env = task.environment if task.environment else None
    sandbox_image = (env.sandbox_image or "") if env else ""
    is_lean = "lean" in sandbox_image
    has_student_code = bool(task.sample_code)
    has_docs = bool(env.docs_available) if env else False
    has_data = bool(
        (env.data_files if env else None)
        or getattr(task, "series", None)
        or getattr(task, "custom_data_key", None)
    )

    parts: list[str] = [
        "You are operating inside a sandboxed tutoring environment for "
        "quantitative finance. A Python runtime with common data-science "
        "packages is available.",
        "To communicate with the student, you MUST use the send_message "
        "tool. This is the only way your words reach the student. Your "
        "text output outside of send_message is NOT visible to them. "
        "The student also cannot see your tool calls, file operations, "
        "or raw command output.",
    ]

    if is_lean:
        parts.append(
            "An algorithmic trading engine is available in this environment. "
            "You can compile and execute C# trading algorithms, run backtests "
            "against historical market data, and inspect detailed results "
            "including trade logs and performance metrics. Backtest executions "
            "are tracked and budget-limited."
        )

    if has_student_code:
        parts.append("The student's existing code is mounted at /student_code/.")

    if has_docs:
        parts.append("Reference documentation is mounted at /docs/.")

    if has_data:
        parts.append("Market data files are pre-loaded at /data/.")

    parts.append(
        "Call get_environment_info for detailed directory listings, "
        "available packages, and session constraints."
    )

    return "\n\n".join(parts)


_HIDDEN_ARTIFACT_RE = re.compile(
    r"(/workspace|saved to|written to|created (?:a|an)? file|see the file|"
    r"open the file|report file|artifact file|workspace artifact)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# GoalChecker — for non-TC categories that still use outcome-level completion.
# ---------------------------------------------------------------------------


class ConversationCompletion(BaseModel):
    """Schema for GoalChecker LLM response."""

    is_complete: bool
    reason: str = ""


class GoalChecker:
    """Goal-based termination for non-incremental-TC categories.

    Replicates DeepEval's ``stop_conversation()`` behavior: each turn,
    sends the full conversation + expected_outcome to an LLM that judges
    whether the conversation should end.

    Prompt is copied verbatim from deepeval/simulator/template.py:105-140.
    """

    _STOP_PROMPT = textwrap.dedent(
        """\
        You are a Conversation Completion Checker.
        Your task is to determine whether the conversation has achieved \
        the expected outcome and should be terminated.

        Guidelines:
        1. Review the entire conversation and decide if the expected \
        outcome has been met and the conversation has ended.
        2. If the expected outcome has been met, mark the conversation \
        as complete.
        3. If not, mark it as incomplete and briefly describe what \
        remains to be done.

        IMPORTANT: The output must be formatted as a JSON object with \
        two keys: `is_complete` (a boolean) and `reason` (a string).

        Expected Outcome: "{expected_outcome}"
        Conversation History:
        {previous_conversation}
        JSON Output:
    """
    )

    def __init__(self, expected_outcome: str, model):
        self.expected_outcome = expected_outcome
        self._model = model

    def check(self, conversation: list[dict]) -> bool:
        """Return True if the expected outcome has been achieved."""
        if not self.expected_outcome:
            return False
        conv_json = json.dumps(conversation, indent=4, ensure_ascii=False)
        prompt = self._STOP_PROMPT.format(
            expected_outcome=self.expected_outcome,
            previous_conversation=conv_json,
        )
        try:
            result = self._model.generate(prompt, schema=ConversationCompletion)
            obj = result[0] if isinstance(result, tuple) else result
            if hasattr(obj, "is_complete"):
                return obj.is_complete
            return False
        except (TypeError, AttributeError):
            # Fallback: plain text + JSON extraction.
            try:
                result = self._model.generate(prompt)
                text = result[0] if isinstance(result, tuple) else result
                match = re.search(r"\{.*\}", text or "", re.DOTALL)
                if match:
                    data = json.loads(match.group())
                    return bool(data.get("is_complete", False))
            except Exception:
                pass
            return False
        except Exception as exc:
            logger.debug("GoalChecker failed: %s", exc)
            return False


# ---------------------------------------------------------------------------
# TutoringSession
# ---------------------------------------------------------------------------


class TutoringSession:
    """Manages a single tutoring session.

    The agent interacts with the student exclusively through
    ``send_message`` tool calls.  This class:

    - Maintains the conversation history
    - Generates student replies via StudentSimulator
    - Checks termination criteria via TCChecker (incremental-TC categories)
    - Checks goal achievement via GoalChecker (remaining outcome-level categories)
    - Tracks turn count and enforces limits
    - Detects stuck agents (repeat detection)
    """

    def __init__(
        self,
        task,
        persona,
        student_sim,
        tc_checker,
        max_turns: int,
        deadline: Optional[float] = None,
        proxy=None,
        goal_checker: Optional[GoalChecker] = None,
        workspace_path: Optional[str] = None,
    ):
        self._task = task
        self._persona = persona
        self._student_sim = student_sim
        self._tc_checker = tc_checker  # None if category doesn't use incremental TC
        self._goal_checker = goal_checker  # None if category uses incremental TC
        self._max_turns = max_turns
        self._deadline = deadline
        self._proxy = proxy  # For set_turn() calls
        self._workspace_path = workspace_path

        self._conversation: list[dict[str, str]] = []
        self._turn: int = 0
        self._done: bool = False
        self._session_info_called: bool = False
        self._completion_reason: str | None = None
        self._workspace_snapshot = scan_workspace_snapshot(workspace_path)
        self._artifact_debug_history: list[dict] = []
        self._pending_visibility_gap: dict[str, object] = {
            "needs_code": False,
            "needs_output": False,
            "since_turn": None,
        }

        # Repeat detection — aligned with model_callback (simulation.py:491-493)
        self._last_agent_msg: str = ""
        self._repeat_count: int = 0

    # ------------------------------------------------------------------
    # Tool handlers
    # ------------------------------------------------------------------

    def handle_start_session(self) -> str:
        """Start the session — return background + student's first message.

        Returns {background, student_message}.  ``background`` is a factual
        description of the environment (no directives, no scoring hints).
        The client decides how to present it to the agent.

        Can only be called once per session.
        """
        if self._session_info_called:
            return json.dumps({"error": "Session already started"})

        opening = self._get_student_opening()
        self._conversation.append({"role": "user", "content": opening})
        self._session_info_called = True

        background = build_background(self._task)
        return json.dumps(
            {
                "background": background,
                "student_message": opening,
            }
        )

    def handle_send_message(self, text: str) -> str:
        """Process agent message, generate student reply.

        Execution order aligned with Legacy model_callback
        (simulation.py:495-637) and DeepEval _simulate_single_conversation
        (conversation_simulator.py:226-272):

        1. Pre-checks (done, empty)
        2. Repeat detection (model_callback:606-624)
        3. Record + advance turn
        4. TC check (_EfficientSimulator.stop_conversation)
        5. Goal check (DeepEval stop_conversation + stop_simulation)
        6. Deadline check
        7. Max turns (_append_student_closing:642-678)
        8. Generate student reply (generate_next_user_input)

        Returns JSON: {student_message, status[, reason]}
        """
        # ── Pre-checks ──
        if self._done:
            return self._result("", "completed", reason=self._completion_reason)

        if not text or not text.strip():
            return json.dumps(
                {
                    "error": "Empty message. Provide text to send to the student.",
                    "status": "active",
                    "turn": self._turn,
                    "max_turns": self._max_turns,
                }
            )

        # ── Repeat detection ──  (aligned: model_callback:606-624)
        if text == self._last_agent_msg:
            self._repeat_count += 1
            if self._repeat_count >= _MAX_REPEATS:
                logger.warning(
                    "Agent repeated identical message %d times, force-stopping.",
                    self._repeat_count + 1,
                )
                self._done = True
                self._completion_reason = "agent_stuck"
                return self._result("", "completed", reason="agent_stuck")
        else:
            self._repeat_count = 0
        self._last_agent_msg = text

        # ── Record agent message + advance turn ──
        self._conversation.append({"role": "assistant", "content": text})
        self._turn += 1

        # Update proxy turn index for tool log attribution
        if self._proxy is not None:
            self._proxy.set_turn(self._turn)

        tc_turn_index = self._current_tool_turn_index()
        turn_evidence = self._build_turn_evidence(tc_turn_index)
        artifact_digest = self._build_artifact_digest(text)

        # ── TC check ──  (aligned: _EfficientSimulator.stop_conversation)
        try:
            tc_met = self._tc_checker is not None and self._tc_checker.check(
                self._conversation,
                turn_evidence=turn_evidence,
                turn_index=tc_turn_index,
            )
        except Exception as exc:
            logger.warning("TC check failed: %s", exc)
            tc_met = False

        if tc_met:
            closing = self._safe_closing()
            if closing:
                self._conversation.append({"role": "user", "content": closing})
            self._done = True
            self._completion_reason = "objectives_met"
            logger.info("TC fully covered at turn %d.", self._turn)
            return self._result(closing, "completed", reason="objectives_met")

        # ── Goal check ──  (aligned: DeepEval stop_conversation + stop_simulation)
        try:
            goals_met = self._goal_checker is not None and self._goal_checker.check(
                self._conversation
            )
        except Exception as exc:
            logger.warning("GoalChecker failed: %s", exc)
            goals_met = False

        if goals_met:
            closing = self._safe_closing()
            if closing:
                self._conversation.append({"role": "user", "content": closing})
            self._done = True
            self._completion_reason = "goals_met"
            logger.info("Goals met at turn %d.", self._turn)
            return self._result(closing, "completed", reason="goals_met")

        # ── Deadline check ──
        # Let the just-sent tutor message contribute to TC / goal completion
        # before converting the session into a timeout.
        if self._deadline is not None and time.time() > self._deadline:
            self._done = True
            self._completion_reason = "timeout"
            logger.info("Session timed out at turn %d.", self._turn)
            closing = self._safe_closing()
            if closing:
                self._conversation.append({"role": "user", "content": closing})
            return self._result(closing, "completed", reason="timeout")

        # ── Max turns check ──  (aligned: _append_student_closing:642-678)
        if self._turn >= self._max_turns:
            self._done = True
            self._completion_reason = "max_turns"
            logger.info("Max turns (%d) reached.", self._max_turns)
            closing = self._safe_closing()
            if closing:
                self._conversation.append({"role": "user", "content": closing})
            return self._result(closing, "completed", reason="max_turns")

        # ── Generate student reply ──  (aligned: generate_next_user_input)
        try:
            reply = self._student_sim.generate_message(
                self._conversation,
                runtime_guidance=self._build_student_runtime_guidance(
                    text,
                    artifact_digest,
                ),
            )
        except Exception as exc:
            logger.warning("StudentSimulator.generate_message failed: %s", exc)
            reply = _STUDENT_FALLBACK
        self._conversation.append({"role": "user", "content": reply})

        return self._result(reply, "active")

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def conversation(self) -> list[dict[str, str]]:
        """Full conversation history (student=user, tutor=assistant)."""
        return list(self._conversation)

    @property
    def done(self) -> bool:
        return self._done

    @property
    def turn(self) -> int:
        return self._turn

    @property
    def completion_reason(self) -> Optional[str]:
        return self._completion_reason

    @property
    def session_status(self) -> str:
        if self._done:
            return "completed"
        if self._session_info_called:
            return "active"
        return "registered"

    @property
    def tc_debug_history(self) -> list[dict]:
        if self._tc_checker is None:
            return []
        return self._tc_checker.debug_history

    @property
    def tc_coverage_summary(self) -> Optional[dict]:
        if self._tc_checker is None:
            return None
        return self._tc_checker.coverage_summary

    @property
    def artifact_debug_history(self) -> list[dict]:
        return list(self._artifact_debug_history)

    def force_complete(
        self,
        reason: str,
        *,
        append_closing: bool = True,
    ) -> str:
        """Mark the session complete outside ``send_message``.

        Used by server-side timeout sweepers. We only append a student closing
        when the latest visible message came from the tutor; if the student is
        already the last speaker, a second consecutive student message would
        look artificial in the saved transcript.
        """
        if self._done:
            if not self._completion_reason:
                self._completion_reason = reason
            return ""

        self._done = True
        self._completion_reason = reason

        if (
            append_closing
            and self._conversation
            and self._conversation[-1].get("role") == "assistant"
        ):
            closing = self._safe_closing()
            if closing:
                self._conversation.append({"role": "user", "content": closing})
                return closing
        return ""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _result(
        self,
        student_message: str,
        status: str,
        reason: str | None = None,
    ) -> str:
        """Build JSON result dict.

        New architecture: returns {student_message, status} only.
        Does NOT expose turn or max_turns to Client.
        """
        d: dict = {
            "student_message": student_message or "",
            "status": status,
        }
        if reason:
            d["reason"] = reason
        return json.dumps(d)

    def _current_tool_turn_index(self) -> int:
        return max(self._turn - 1, 0)

    def _build_turn_evidence(self, turn_index: int) -> Optional[dict]:
        if self._proxy is None:
            return None
        try:
            return build_turn_evidence(self._proxy.get_logs(), turn_index)
        except Exception as exc:
            logger.debug("Failed to build TC turn evidence: %s", exc)
            return None

    def _build_artifact_digest(self, latest_agent_text: str) -> dict:
        latest_student_text = ""
        if len(self._conversation) >= 2 and self._conversation[-2]["role"] == "user":
            latest_student_text = self._conversation[-2]["content"]

        try:
            new_snapshot, digest = build_visible_artifact_digest(
                workspace_path=self._workspace_path,
                previous_snapshot=self._workspace_snapshot,
                latest_student_text=latest_student_text,
                latest_agent_text=latest_agent_text,
            )
        except Exception as exc:
            logger.debug("Failed to build artifact digest: %s", exc)
            return {}

        self._workspace_snapshot = new_snapshot
        digest = self._merge_pending_visibility_gap(digest)
        self._artifact_debug_history.append(
            {
                "turn_index": self._current_tool_turn_index(),
                **digest,
            }
        )
        return digest

    def _merge_pending_visibility_gap(self, digest: dict) -> dict:
        """Persist unresolved "artifact exists but was not shown" gaps across turns."""
        request_signals = dict(digest.get("request_signals", {}))
        chat_signals = dict(digest.get("chat_signals", {}))
        artifact_signals = dict(digest.get("artifact_signals", {}))
        steering_signals = dict(digest.get("steering_signals", {}))

        pending = dict(self._pending_visibility_gap)
        turn_index = self._current_tool_turn_index()

        if chat_signals.get("assistant_pasted_code"):
            pending["needs_code"] = False
        elif steering_signals.get("student_should_request_literal_code") or (
            request_signals.get("asks_for_code")
            and chat_signals.get("assistant_refers_to_hidden_artifacts")
            and not chat_signals.get("assistant_pasted_code")
        ):
            pending["needs_code"] = True

        if chat_signals.get("assistant_pasted_output"):
            pending["needs_output"] = False
        elif steering_signals.get("student_should_request_literal_output") or (
            request_signals.get("asks_for_output")
            and chat_signals.get("assistant_refers_to_hidden_artifacts")
            and not chat_signals.get("assistant_pasted_output")
        ):
            pending["needs_output"] = True

        if pending["needs_code"] or pending["needs_output"]:
            if pending.get("since_turn") is None:
                pending["since_turn"] = turn_index
        else:
            pending["since_turn"] = None

        self._pending_visibility_gap = pending

        steering_signals["artifact_ready_but_not_shown"] = bool(
            steering_signals.get("artifact_ready_but_not_shown")
            or pending["needs_code"]
            or pending["needs_output"]
        )
        steering_signals["student_should_request_literal_code"] = bool(
            steering_signals.get("student_should_request_literal_code")
            or pending["needs_code"]
        )
        steering_signals["student_should_request_literal_output"] = bool(
            steering_signals.get("student_should_request_literal_output")
            or pending["needs_output"]
        )
        steering_signals["avoid_new_branch"] = bool(
            steering_signals.get("avoid_new_branch")
            or steering_signals["artifact_ready_but_not_shown"]
        )

        return {
            **digest,
            "artifact_signals": artifact_signals,
            "chat_signals": chat_signals,
            "request_signals": request_signals,
            "steering_signals": steering_signals,
            "pending_visibility_gap": {
                "needs_code": bool(pending["needs_code"]),
                "needs_output": bool(pending["needs_output"]),
                "since_turn": pending.get("since_turn"),
            },
        }

    def _recent_student_mentions_file_limit(self) -> bool:
        recent_users = [
            msg["content"]
            for msg in reversed(self._conversation[:-1])
            if msg["role"] == "user"
        ][:3]
        return any(_FILE_LIMIT_RE.search(text) for text in recent_users)

    def _assistant_refers_to_hidden_artifacts(self, text: str) -> bool:
        return bool(_HIDDEN_ARTIFACT_RE.search(text or ""))

    def _build_student_runtime_guidance(
        self,
        latest_agent_text: str,
        artifact_digest: Optional[dict] = None,
    ) -> str:
        if self._tc_checker is None:
            return ""

        signals: list[str] = []
        coverage = self._tc_checker.coverage_summary
        covered = coverage.get("covered", 0)
        total = coverage.get("total", 0)
        stalled_turns = self._tc_checker.stalled_turns

        if total and covered >= max(total - 1, 1):
            signals.append(
                "- Your main learning goal appears mostly satisfied. If you still need something, ask one focused clarification. Otherwise, wrap up naturally."
            )
        if stalled_turns >= 2:
            signals.append(
                "- The conversation has not made visible progress for multiple tutor turns. Do not repeat the same complaint indefinitely; ask one narrower question or close if the core idea is already clear."
            )
        if self._turn >= max(self._max_turns - 2, 1):
            signals.append(
                "- The session is nearing its natural limit. Prioritize one final concrete clarification over opening a brand-new branch."
            )
        if (
            self._recent_student_mentions_file_limit()
            and self._assistant_refers_to_hidden_artifacts(latest_agent_text)
        ):
            signals.append(
                "- The tutor may be relying on files or artifacts you cannot access directly. Ask once for the key literal code, output, or number in the chat, then keep the next request narrow."
            )

        artifact_signals = (artifact_digest or {}).get("steering_signals", {})
        request_signals = (artifact_digest or {}).get("request_signals", {})
        if artifact_signals.get("artifact_ready_but_not_shown"):
            signals.append(
                "- The tutor appears to have generated a relevant artifact already, but has not shown the key part directly in chat. Keep your next message focused on asking for the smallest literal code block or concrete output inline."
            )
        if artifact_signals.get("student_should_request_literal_code"):
            signals.append(
                "- Ask for the exact minimal code snippet directly in the chat instead of accepting more file references."
            )
        if artifact_signals.get("student_should_request_literal_output"):
            signals.append(
                "- Ask for the specific output table, number, or printed result directly in the chat."
            )
        if artifact_signals.get("avoid_new_branch") and not request_signals.get(
            "asks_for_comparison"
        ):
            signals.append(
                "- Stay on the same concrete example rather than opening a new library, tool, or advanced side branch."
            )

        if not signals:
            return ""

        return "\n".join(
            [
                "Guide the student's next reply with these hidden steering notes:",
                *signals,
            ]
        )

    def _safe_closing(self) -> str:
        """Generate student closing with fallback on failure.

        Aligned with _EfficientSimulator._generate_closing (simulation.py:372-405).
        """
        try:
            closing = self._student_sim.generate_closing(self._conversation)
            if closing and closing.strip():
                return closing.strip()
        except Exception as exc:
            logger.warning("Failed to generate closing: %s", exc)
        return _CLOSING_FALLBACK

    def inject_student_opening(self, opening: str) -> None:
        """Inject the student opening into conversation without start_session.

        Used by the reference harness when the opening is already in the
        agent's bootstrap prompt.
        """
        if not self._session_info_called and not self._conversation:
            self._conversation.append({"role": "user", "content": opening})
            self._session_info_called = True

    def _get_student_opening(self) -> str:
        """Get persona-specific opening message for this task.

        Openings are stored on the *task* as ``student_openings: dict[persona_id, str]``.
        """
        openings = getattr(self._task, "student_openings", {}) or {}
        persona_id = getattr(self._persona, "persona_id", "")
        opening = openings.get(persona_id, "")
        return opening or "Hi, I need help with this topic."
