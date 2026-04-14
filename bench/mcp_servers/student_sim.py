"""Student simulator for QuantTutorBench.

Generates student messages via a DeepEval model object (resolved by
``config.model_resolver.resolve_deepeval_model``).  Prompt templates and
output parsing are aligned with DeepEval's ConversationSimulator to ensure
bit-exact student behavior across Legacy and MCP paths.

Used by TutoringSession behind the ``send_message`` MCP tool.
"""

import json
import logging
import re
import textwrap

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class SimulatedInput(BaseModel):
    """Schema for structured student message output.

    Aligned with DeepEval's SimulatedInput (simulator/schema.py).
    Defined at module level to avoid repeated class creation.
    """

    simulated_input: str


# ---------------------------------------------------------------------------
# Prompt templates — copied verbatim from DeepEval template.py to ensure
# identical student message distributions across Legacy and MCP paths.
# ---------------------------------------------------------------------------

# Multimodal rules block — from DeepEval template.py:10-16.
_MULTIMODAL_RULES = textwrap.dedent(
    """\
    --- MULTIMODAL INPUT RULES ---
    - Treat image content as factual evidence.
    - Only reference visual details that are explicitly and clearly visible.
    - Do not infer or guess objects, text, or details not visibly present.
    - If an image is unclear or ambiguous, mark uncertainty explicitly.
"""
)

_FIRST_MESSAGE_PROMPT = textwrap.dedent(
    """\
    Pretend you are a user of an LLM app. Your goal is to start a conversation \
    in English based on a scenario and user profile. The scenario defines your \
    context and motivation for interacting with the LLM, while the user profile \
    provides additional personal details to make the conversation realistic \
    and relevant.

    Guidelines:
    1. The opening message should clearly convey the user's intent or need \
    within the scenario.
    2. Keep the tone warm, conversational, and natural, as if it's from a \
    real person seeking assistance.
    3. Avoid providing excessive details upfront; the goal is to initiate \
    the conversation and build rapport, not to solve it in the first message.
    4. The message should be concise, ideally no more than 1-3 sentences.

    {multimodal_rules}

    IMPORTANT: The output must be formatted as a JSON object with a single \
    key `simulated_input`, where the value is the generated opening message \
    in English.

    Example Language: english
    Example User Profile: "Jeff Seid, is available Monday and Thursday \
    afternoons, and their phone number is 0010281839. He suffers from \
    chronic migraines."
    Example Scenario: "A sick person trying to get a diagnosis for \
    persistent headaches and fever."
    Example JSON Output:
    {{
        "simulated_input": "Hi, I haven't been feeling well lately. \
    I've had these headaches and a fever that just won't go away. \
    Could you help me figure out what's going on?"
    }}

    Language: English
    User Profile: "{user_description}"
    Scenario: "{scenario}"
    JSON Output:
"""
)

_NEXT_MESSAGE_PROMPT = textwrap.dedent(
    """\
    Pretend you are a user of an LLM app. Your task is to generate the next \
    user input in English based on the provided scenario, user profile, and \
    the previous conversation.

    Guidelines:
    1. Use the scenario and user profile as the guiding context for the \
    user's next input.
    2. Ensure the next input feels natural, conversational, and relevant \
    to the last assistant reply in the conversation.
    3. Keep the tone consistent with the previous user inputs.
    4. The generated user input should be concise, ideally no more than \
    1-2 sentences.

    {multimodal_rules}

    IMPORTANT: The output must be formatted as a JSON object with a single \
    key `simulated_input`, where the value is the generated user input \
    in English.

    Example Language: english
    Example User Profile: "Jeff Seid, is available Monday and Thursday \
    afternoons, and their phone number is 0010281839."
    Example Scenario: "A user seeking tips for securing a funding round."
    Example Previous Conversation:
    [
        {{"role": "user", "content": "Hi, I need help preparing for my \
    funding pitch."}},
        {{"role": "assistant", "content": "Of course! Can you share more \
    about your business and the type of investors you are targeting?"}}
    ]
    Example JSON Output:
    {{
        "simulated_input": "Sure, we are a SaaS startup focusing on \
    productivity tools for small businesses."
    }}

    Language: English
    User Profile: "{user_description}"
    Scenario: "{scenario}"
    Previous Conversation:
    {transcript}

    JSON Output:
"""
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

# Hardcoded fallback when closing generation fails (aligned with
# _EfficientSimulator._generate_closing fallback in simulation.py:401-405).
_CLOSING_FALLBACK = (
    "Thanks for walking me through all of this — "
    "I have a much clearer picture now. "
    "I'll try applying these techniques to my own data."
)

# Regex for extracting JSON from LLM output.
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_transcript(
    conversation: list[dict[str, str]], max_chars: int | None = None
) -> str:
    """Format conversation history as JSON array (aligned with DeepEval).

    DeepEval's simulate_user_turn serialises turns via
    ``json.dumps([t.model_dump() for t in turns], indent=4)``.
    We replicate the same format so the LLM sees identical input.
    """
    trimmed = [
        {
            "role": t["role"],
            "content": t["content"][:max_chars] if max_chars else t["content"],
        }
        for t in conversation
    ]
    return json.dumps(trimmed, indent=4, ensure_ascii=False)


def _parse_simulated_input(raw: str) -> str:
    """Extract ``simulated_input`` from JSON output, with fallback.

    Aligned with DeepEval's generate_schema() + trimAndLoadJson fallback
    (conversation_simulator.py:606-623).
    """
    match = _JSON_RE.search(raw or "")
    if match:
        try:
            data = json.loads(match.group())
            text = data.get("simulated_input")
            if text and text.strip():
                return text.strip()
        except (json.JSONDecodeError, TypeError):
            pass
    # Fallback: treat raw output as plain text.
    return (raw or "").strip()


# ---------------------------------------------------------------------------
# StudentSimulator
# ---------------------------------------------------------------------------


class StudentSimulator:
    """Generates student messages via a DeepEval model object.

    Prompt templates, conversation history format, and output parsing are
    aligned with DeepEval's ConversationSimulator to produce identical
    student message distributions across Legacy and MCP paths.

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
        self.total_cost: float = 0.0

    @property
    def model(self):
        """Lazy-resolve plain string model names on first use."""
        if isinstance(self._model, str) or self._model is None:
            from config.model_resolver import resolve_deepeval_model

            self._model = resolve_deepeval_model(self._model)
        return self._model

    def _generate_parsed(self, prompt: str) -> str:
        """Generate text via model, parse JSON output, track cost.

        Tries structured output (schema=) first, falls back to plain
        text + JSON extraction.  Aligned with DeepEval's generate_schema()
        (conversation_simulator.py:606-623).
        """
        # Try structured output path (GPTModel / _OAuthAnthropicModel).
        # Only catch TypeError/AttributeError/NotImplementedError — these
        # indicate the model doesn't support schema=.  Network errors,
        # rate limits, etc. should propagate (aligned with DeepEval's
        # generate_schema which only catches TypeError).
        try:
            result = self.model.generate(prompt, schema=SimulatedInput)
            if isinstance(result, tuple):
                obj, cost = result[0], result[1] if len(result) > 1 else None
                if cost is not None:
                    self.total_cost += cost
            else:
                obj = result
            if hasattr(obj, "simulated_input"):
                return obj.simulated_input.strip()
        except (TypeError, AttributeError, NotImplementedError) as exc:
            logger.debug("Structured output failed (%s), falling back to text.", exc)

        # Fallback: plain text generation + JSON extraction.
        result = self.model.generate(prompt)
        if isinstance(result, tuple):
            text = result[0]
            cost = result[1] if len(result) > 1 else None
            if cost is not None:
                self.total_cost += cost
        else:
            text = result
        return _parse_simulated_input(text)

    def generate_message(
        self,
        conversation: list[dict[str, str]],
    ) -> str:
        """Generate the next student message given conversation history.

        Uses ``_FIRST_MESSAGE_PROMPT`` when the conversation has no
        assistant turns yet (aligned with DeepEval's
        ``generate_first_user_input``), otherwise uses
        ``_NEXT_MESSAGE_PROMPT`` (aligned with ``generate_next_user_input``).

        Args:
            conversation: [{"role": "user"|"assistant", "content": "..."}]
                "user" = student, "assistant" = tutor.

        Returns:
            The student's next message as a string.
        """
        is_first = not any(t["role"] == "assistant" for t in conversation)
        if is_first:
            prompt = _FIRST_MESSAGE_PROMPT.format(
                user_description=self.user_description,
                scenario=self.scenario,
                multimodal_rules=_MULTIMODAL_RULES,
            )
        else:
            prompt = _NEXT_MESSAGE_PROMPT.format(
                user_description=self.user_description,
                scenario=self.scenario,
                transcript=_format_transcript(conversation),
                multimodal_rules=_MULTIMODAL_RULES,
            )
        return self._generate_parsed(prompt)

    def generate_closing(
        self,
        conversation: list[dict[str, str]],
    ) -> str:
        """Generate a natural closing message from the student.

        Closing uses a simpler prompt (no JSON output) aligned with
        _EfficientSimulator._generate_closing (simulation.py:372-405).
        """
        prompt = _CLOSING_PROMPT.format(
            scenario=self.scenario[:400],
            transcript=_format_transcript(conversation[-4:], max_chars=300),
        )
        try:
            result = self.model.generate(prompt)
            if isinstance(result, tuple):
                text = result[0]
                cost = result[1] if len(result) > 1 else None
                if cost is not None:
                    self.total_cost += cost
            else:
                text = result
            if text and text.strip():
                return text.strip()
        except Exception as exc:
            logger.warning("Failed to generate closing: %s", exc)
        return _CLOSING_FALLBACK
