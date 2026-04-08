"""Lightweight student simulator for QuantTutorBench.

Generates student messages via direct LLM calls (OpenRouter).
No DeepEval dependency -- used by TutoringSession behind the send_message tool.
"""

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# System prompt template for the student simulator LLM.
_STUDENT_SYSTEM = (
    "You are a simulated student in a tutoring conversation about "
    "quantitative finance. Stay in character at all times.\n\n"
    "{user_description}\n\n"
    "SCENARIO:\n{scenario}\n\n"
    "Generate only the student's next message. Do NOT include any "
    "metadata, role labels, or stage directions — just the student's "
    "words as they would type them in a chat."
)

_CLOSING_PROMPT = (
    "You are the student in the conversation below. The tutor just "
    "finished answering your last question. Write a brief closing "
    "message (1-2 sentences) that thanks the tutor and mentions one "
    "specific thing you learned or plan to try. Stay in character.\n\n"
    "Scenario: {scenario}\n\n"
    "Reply with ONLY the closing message."
)


class StudentSimulator:
    """Generates student messages via OpenRouter API calls.

    Uses the same persona/scenario prompt structure as the DeepEval
    simulator but without the ConversationSimulator framework.
    """

    def __init__(
        self,
        scenario: str,
        user_description: str,
        model: str = "openai/gpt-5.2",
        temperature: float = 0.0,
    ):
        self.scenario = scenario
        self.user_description = user_description
        self.model = model
        self.temperature = temperature

        self._system_prompt = _STUDENT_SYSTEM.format(
            user_description=user_description,
            scenario=scenario,
        )
        self._client = None
        self._total_cost: float = 0.0

    @property
    def client(self):
        """Lazy-init OpenAI client pointed at OpenRouter."""
        if self._client is None:
            try:
                import openai
            except ImportError:
                raise ImportError(
                    "openai package required for student simulation. "
                    "Install with: pip install openai"
                )
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                raise RuntimeError(
                    "OPENROUTER_API_KEY not set. Required for student simulation."
                )
            self._client = openai.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        return self._client

    def generate_message(
        self,
        conversation: list[dict[str, str]],
    ) -> str:
        """Generate the next student message given conversation history.

        Args:
            conversation: [{"role": "user"|"assistant", "content": "..."}]
                "user" = student, "assistant" = tutor.

        Returns:
            The student's next message as a string.
        """
        messages = [{"role": "system", "content": self._system_prompt}]

        # Map our roles to the LLM call roles:
        # student = "assistant" (we want the LLM to generate the student's message)
        # tutor = "user" (from the student-LLM's perspective, tutor messages are input)
        for turn in conversation:
            if turn["role"] == "user":
                # Student's prior messages -> assistant (student LLM generated these)
                messages.append({"role": "assistant", "content": turn["content"]})
            else:
                # Tutor's messages -> user (input to the student LLM)
                messages.append({"role": "user", "content": turn["content"]})

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=1024,
        )

        text = response.choices[0].message.content or ""
        return text.strip()

    def generate_closing(
        self,
        conversation: list[dict[str, str]],
    ) -> str:
        """Generate a natural closing message from the student."""
        prompt = _CLOSING_PROMPT.format(scenario=self.scenario[:400])

        # Include last 2 turns for context
        context = ""
        for turn in conversation[-4:]:
            role_label = "Student" if turn["role"] == "user" else "Tutor"
            context += f"{role_label}: {turn['content'][:300]}\n\n"

        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": context + "Write the student's closing message:"},
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=self.temperature,
            max_tokens=256,
        )

        text = response.choices[0].message.content or ""
        return text.strip()
