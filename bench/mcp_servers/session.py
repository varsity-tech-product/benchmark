"""Tutoring session state manager for QuantTutorBench.

Manages the conversation between agent and simulated user.
Backs the ``send_message`` and ``get_session_info`` MCP tools.

Defense layers aligned with Legacy path (simulation.py create_model_callback):
- User simulator fallback on failure
- Closing generation fallback (hardcoded text)
- Timeout graceful wrap-up with closing
- Agent repeat detection (force-stop after consecutive identical messages)
- Max-turns user closing (aligned with _append_user_closing)
"""

import json
import logging
import re
import textwrap
import time
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Hardcoded closing fallback aligned with UserSimulator._CLOSING_FALLBACK.
_CLOSING_FALLBACK = (
    "Thanks for walking me through all of this — "
    "I have a much clearer picture now. "
    "I'll try applying these techniques to my own data."
)

# Fallback user message when generate_message() fails — aligned with
# model_callback exception handler (simulation.py:586).
_USER_FALLBACK = "Could you explain that in a bit more detail?"

# Repeat detection threshold — aligned with model_callback _MAX_REPEATS
# (simulation.py:493).
_MAX_REPEATS = 2


# ---------------------------------------------------------------------------
# GoalChecker — goal-based completion fallback
# ---------------------------------------------------------------------------


class ConversationCompletion(BaseModel):
    """Schema for GoalChecker LLM response."""

    is_complete: bool
    reason: str = ""


class GoalChecker:
    """Goal-based termination.

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

    The agent interacts with the user exclusively through
    ``send_message`` tool calls.  This class:

    - Maintains the conversation history
    - Generates user replies via UserSimulator
    - Checks goal achievement via GoalChecker
    - Tracks turn count and enforces limits
    - Detects stuck agents (repeat detection)
    """

    def __init__(
        self,
        task,
        persona,
        user_sim,
        max_turns: int,
        deadline: Optional[float] = None,
        proxy=None,
        goal_checker: Optional[GoalChecker] = None,
        max_steps_per_turn: int = 50,
    ):
        self._task = task
        self._persona = persona
        self._user_sim = user_sim
        self._goal_checker = goal_checker
        self._max_turns = max_turns
        self._deadline = deadline
        self._proxy = proxy  # For set_turn() calls
        self._max_steps_per_turn = max_steps_per_turn

        self._conversation: list[dict[str, str]] = []
        self._turn: int = 0
        self._done: bool = False
        self._session_info_called: bool = False

        # Repeat detection — aligned with model_callback (simulation.py:491-493)
        self._last_agent_msg: str = ""
        self._repeat_count: int = 0

        # Per-turn step counter — prevents infinite tool loops within a turn.
        # Resets to 0 each time send_message is called.
        self._turn_step_count: int = 0

    # ------------------------------------------------------------------
    # Tool handlers (registered on MCPProxy as tool implementations)
    # ------------------------------------------------------------------

    def handle_get_session_info(self) -> str:
        """Return task description + user opening. Idempotent."""
        opening = self._get_user_opening()

        # Append opening to conversation only on first call
        if not self._session_info_called:
            self._conversation.append({"role": "user", "content": opening})
            self._session_info_called = True

        return json.dumps(
            {
                "task_description": self._task.description,
                "category": self._task.category.value,
                "difficulty": self._task.difficulty.value,
                "user_level": self._persona.knowledge_level,
                "user_opening": opening,
                "max_turns": self._max_turns,
            }
        )

    def handle_send_message(
        self, text: str, reasoning: str | None = None
    ) -> str:
        """Process agent message, generate user reply.

        ``reasoning`` is the agent's private rationale for this turn.
        It is stored as metadata on the conversation entry for trace
        analysis but is NEVER passed to the user simulator (which
        only reads ``entry["content"]``).

        Execution order aligned with Legacy model_callback
        (simulation.py:495-637) and DeepEval _simulate_single_conversation
        (conversation_simulator.py:226-272):

        1. Pre-checks (done, empty)
        2. Repeat detection (model_callback:606-624)
        3. Record + advance turn
        4. Deadline check (model_callback:517-537)
        5. Goal check (DeepEval stop_conversation + stop_simulation)
        6. Max turns (_append_user_closing:642-678)
        7. Generate user reply (generate_next_user_input)

        Returns JSON: {user_reply, status, turn, max_turns[, reason]}
        """
        # ── Pre-checks ──
        if self._done:
            return self._result("", "completed")

        if not text or not text.strip():
            return json.dumps(
                {
                    "error": "Empty message. Provide text to send to the user.",
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
                return self._result("", "completed", reason="agent_stuck")
        else:
            self._repeat_count = 0
        self._last_agent_msg = text

        # ── Record agent message + advance turn ──
        msg_entry: dict = {"role": "assistant", "content": text}
        # Stash private rationale on the entry for trace analysis. User
        # simulator reads only ``content``, so this never leaks.
        if reasoning and reasoning.strip():
            msg_entry["reasoning"] = reasoning.strip()
        self._conversation.append(msg_entry)
        self._turn += 1
        self.reset_step_count()  # New turn starts with fresh step budget

        # Update proxy turn index for tool log attribution
        if self._proxy is not None:
            self._proxy.set_turn(self._turn)

        # ── Deadline check ──  (aligned: model_callback:517-537)
        if self._deadline is not None and time.time() > self._deadline:
            self._done = True
            logger.info("Session timed out at turn %d.", self._turn)
            closing = self._safe_closing()
            if closing:
                self._conversation.append({"role": "user", "content": closing})
            return self._result(closing, "completed", reason="timeout")

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
            logger.info("Goals met at turn %d.", self._turn)
            return self._result(closing, "completed", reason="goals_met")

        # ── Max turns check ──  (aligned: _append_user_closing:642-678)
        if self._turn >= self._max_turns:
            self._done = True
            logger.info("Max turns (%d) reached.", self._max_turns)
            closing = self._safe_closing()
            if closing:
                self._conversation.append({"role": "user", "content": closing})
            return self._result(closing, "completed", reason="max_turns")

        # ── Generate user reply ──  (aligned: generate_next_user_input)
        try:
            reply = self._user_sim.generate_message(
                self._conversation,
                tool_logs=(self._proxy.get_logs() if self._proxy is not None else []),
            )
        except Exception as exc:
            logger.warning("UserSimulator.generate_message failed: %s", exc)
            reply = _USER_FALLBACK
        self._conversation.append({"role": "user", "content": reply})

        return self._result(reply, "active")

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    @property
    def conversation(self) -> list[dict[str, str]]:
        """Full conversation history (user=user, tutor=assistant)."""
        return list(self._conversation)

    @property
    def done(self) -> bool:
        return self._done

    @property
    def turn(self) -> int:
        return self._turn

    @property
    def max_steps_per_turn(self) -> int:
        return self._max_steps_per_turn

    # ------------------------------------------------------------------
    # Step limiting (per-turn tool call budget)
    # ------------------------------------------------------------------

    def check_step_limit(self) -> str | None:
        """Check if the current turn has exceeded its tool call budget.

        Called by MCPProxy.call_tool() BEFORE executing non-send_message tools.
        Returns an error JSON string if limit exceeded, or None if OK.
        """
        self._turn_step_count += 1
        if self._turn_step_count > self._max_steps_per_turn:
            return json.dumps(
                {
                    "error": (
                        f"Step limit ({self._max_steps_per_turn}) reached for this turn. "
                        f"Call send_message to end this turn and proceed."
                    ),
                    "status": "step_limit_exceeded",
                    "turn": self._turn,
                    "steps_used": self._turn_step_count,
                }
            )
        return None

    def reset_step_count(self) -> None:
        """Reset step counter. Called when send_message ends a turn."""
        self._turn_step_count = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _result(
        self,
        user_reply: str,
        status: str,
        reason: str | None = None,
    ) -> str:
        """Build JSON result dict."""
        d: dict = {
            "user_reply": user_reply or "",
            "status": status,
            "turn": self._turn,
            "max_turns": self._max_turns,
        }
        if reason:
            d["reason"] = reason
        return json.dumps(d)

    def _safe_closing(self) -> str:
        """Generate user closing with fallback on failure.

        Uses the user simulator closing prompt, with a fixed fallback.
        """
        try:
            closing = self._user_sim.generate_closing(self._conversation)
            if closing and closing.strip():
                return closing.strip()
        except Exception as exc:
            logger.warning("Failed to generate closing: %s", exc)
        return _CLOSING_FALLBACK

    def inject_user_opening(self, opening: str) -> None:
        """Inject the user opening into conversation without get_session_info.

        Used by the reference harness when the opening is already in the
        agent's bootstrap prompt.
        """
        if not self._session_info_called and not self._conversation:
            self._conversation.append({"role": "user", "content": opening})
            self._session_info_called = True

    def _get_user_opening(self) -> str:
        """Get the opening message for this task."""
        opening = getattr(self._task, "user_opening", "")
        return opening or "Hi, I need help with this topic."
