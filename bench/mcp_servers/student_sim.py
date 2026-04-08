"""Student simulator for QuantTutorBench.

Generates student messages via a DeepEval model object (resolved by
``config.model_resolver.resolve_deepeval_model``).  Shares the same
model resolution pipeline as the DeepEval ConversationSimulator path,
eliminating duplicate OpenRouter client setup.

Used by TutoringSession behind the ``send_message`` MCP tool.
"""

import logging

logger = logging.getLogger(__name__)

# Prompt template for generating the next student message.
_MESSAGE_PROMPT = (
    "You are a simulated student in a tutoring conversation about "
    "quantitative finance. Stay in character at all times.\n\n"
    "{user_description}\n\n"
    "SCENARIO:\n{scenario}\n\n"
    "Conversation so far:\n{transcript}\n\n"
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
    "{transcript}\n"
    "Reply with ONLY the closing message."
)


def _format_transcript(
    conversation: list[dict[str, str]], max_chars: int | None = None
) -> str:
    """Format conversation history as a labelled transcript."""
    lines = []
    for turn in conversation:
        label = "Student" if turn["role"] == "user" else "Tutor"
        content = turn["content"][:max_chars] if max_chars else turn["content"]
        lines.append(f"{label}: {content}")
    return "\n\n".join(lines)


def _extract_text(result) -> str:
    """Extract plain text from a DeepEval model.generate() result.

    Returns (text, cost) tuple for OAuth/GPTModel, or plain string for
    fallback string models.
    """
    text = result[0] if isinstance(result, tuple) else result
    return (text or "").strip()


class StudentSimulator:
    """Generates student messages via a DeepEval model object.

    Accepts any object returned by ``resolve_deepeval_model()`` —
    ``GPTModel``, ``_OAuthAnthropicModel``, or a plain model-name string.
    When a plain string is passed, it is resolved lazily on first use.
    """

    def __init__(
        self,
        scenario: str,
        user_description: str,
        model=None,
    ):
        self.scenario = scenario
        self.user_description = user_description
        self._model = model  # DeepEval model object or string

    @property
    def model(self):
        """Lazy-resolve plain string model names on first use."""
        if isinstance(self._model, str) or self._model is None:
            from config.model_resolver import resolve_deepeval_model

            self._model = resolve_deepeval_model(self._model)
        return self._model

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
        prompt = _MESSAGE_PROMPT.format(
            user_description=self.user_description,
            scenario=self.scenario,
            transcript=_format_transcript(conversation),
        )
        result = self.model.generate(prompt)
        return _extract_text(result)

    def generate_closing(
        self,
        conversation: list[dict[str, str]],
    ) -> str:
        """Generate a natural closing message from the student."""
        prompt = _CLOSING_PROMPT.format(
            scenario=self.scenario[:400],
            transcript=_format_transcript(conversation[-4:], max_chars=300),
        )
        result = self.model.generate(prompt)
        return _extract_text(result)
