"""Tutoring session state manager for QuantTutorBench.

Manages the conversation between agent and simulated student.
Backs the ``send_message`` and ``get_session_info`` MCP tools.
"""

import json
import logging
import time
from typing import Optional

logger = logging.getLogger(__name__)


class TutoringSession:
    """Manages a single tutoring session.

    The agent interacts with the student exclusively through
    ``send_message`` tool calls.  This class:

    - Maintains the conversation history
    - Generates student replies via StudentSimulator
    - Checks termination criteria via TCChecker
    - Tracks turn count and enforces limits
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
    ):
        self._task = task
        self._persona = persona
        self._student_sim = student_sim
        self._tc_checker = tc_checker  # None if category doesn't use incremental TC
        self._max_turns = max_turns
        self._deadline = deadline
        self._proxy = proxy  # For set_turn() calls

        self._conversation: list[dict[str, str]] = []
        self._turn: int = 0
        self._done: bool = False
        self._session_info_called: bool = False

    # ------------------------------------------------------------------
    # Tool handlers (registered on MCPProxy as tool implementations)
    # ------------------------------------------------------------------

    def handle_get_session_info(self) -> str:
        """Return task description + student opening. Idempotent."""
        opening = self._get_student_opening()

        # Append opening to conversation only on first call
        if not self._session_info_called:
            self._conversation.append({"role": "user", "content": opening})
            self._session_info_called = True

        return json.dumps({
            "task_description": self._task.description,
            "category": self._task.category.value,
            "difficulty": self._task.difficulty.value,
            "student_level": self._persona.knowledge_level,
            "student_opening": opening,
            "max_turns": self._max_turns,
        })

    def handle_send_message(self, text: str) -> str:
        """Process agent message, generate student reply.

        Returns JSON: {student_reply, status, turn, max_turns}
        """
        if self._done:
            return json.dumps({
                "student_reply": "",
                "status": "completed",
                "turn": self._turn,
                "max_turns": self._max_turns,
            })

        if not text or not text.strip():
            return json.dumps({
                "error": "Empty message. Provide text to send to the student.",
                "status": "active",
                "turn": self._turn,
                "max_turns": self._max_turns,
            })

        # Record agent message
        self._conversation.append({"role": "assistant", "content": text})
        self._turn += 1

        # Update proxy turn index for tool log attribution
        if self._proxy is not None:
            self._proxy.set_turn(self._turn)

        # Check deadline
        if self._deadline is not None and time.time() > self._deadline:
            self._done = True
            logger.info("Session timed out at turn %d.", self._turn)
            return json.dumps({
                "student_reply": "",
                "status": "completed",
                "reason": "timeout",
                "turn": self._turn,
                "max_turns": self._max_turns,
            })

        # TC check
        if self._tc_checker is not None and self._tc_checker.check(self._conversation):
            closing = self._student_sim.generate_closing(self._conversation)
            self._conversation.append({"role": "user", "content": closing})
            self._done = True
            logger.info("TC fully covered at turn %d.", self._turn)
            return json.dumps({
                "student_reply": closing,
                "status": "completed",
                "reason": "objectives_met",
                "turn": self._turn,
                "max_turns": self._max_turns,
            })

        # Max turns check
        if self._turn >= self._max_turns:
            self._done = True
            logger.info("Max turns (%d) reached.", self._max_turns)
            return json.dumps({
                "student_reply": "",
                "status": "completed",
                "reason": "max_turns",
                "turn": self._turn,
                "max_turns": self._max_turns,
            })

        # Generate student reply
        reply = self._student_sim.generate_message(self._conversation)
        self._conversation.append({"role": "user", "content": reply})

        return json.dumps({
            "student_reply": reply,
            "status": "active",
            "turn": self._turn,
            "max_turns": self._max_turns,
        })

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

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def inject_student_opening(self, opening: str) -> None:
        """Inject the student opening into conversation without get_session_info.

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
