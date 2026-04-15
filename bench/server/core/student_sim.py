"""Student simulator for QuantTutorBench.

Generates student messages via a DeepEval model object (resolved by
``server.eval.ewan_eval.model_resolver.resolve_ewan_model``).  Prompt templates and
output parsing are aligned with DeepEval's ConversationSimulator to ensure
bit-exact student behavior across Legacy and MCP paths.

Used by TutoringSession behind the ``send_message`` MCP tool.
"""

import difflib
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

_FIRST_MESSAGE_PROMPT = textwrap.dedent(
    """\
    --- BACKGROUND ---
    You are role-playing as a real person using an LLM tutoring app.
    Your profile: {user_description}
    Your situation: {scenario}
    --- END BACKGROUND ---

    Generate your opening message to the tutor.

    Guidelines:
    1. Clearly convey your intent or need within the situation above.
    2. Keep the tone warm, conversational, and natural.
    3. Do not dump all details upfront — start the conversation, don't solve it.
    4. 1-3 sentences maximum.

    Example:
    {{
        "simulated_input": "Hi, I haven't been feeling well lately. \
    I've had these headaches and a fever that just won't go away. \
    Could you help me figure out what's going on?"
    }}

    Respond with a JSON object containing a single key `simulated_input`.
    JSON Output:
"""
)

_NEXT_MESSAGE_PROMPT = textwrap.dedent(
    """\
    --- BACKGROUND ---
    You are role-playing as a real person using an LLM tutoring app.
    Your profile: {user_description}
    Your situation: {scenario}
    --- END BACKGROUND ---

    Generate your next message to the tutor based on the conversation so far.

    Guidelines:
    1. Stay in character and respond naturally to the tutor's last reply.
    2. Keep tone consistent with your earlier messages.
    3. 1-2 sentences maximum.

    {runtime_guidance_block}

    Conversation so far:
    {transcript}

    Respond with a JSON object containing a single key `simulated_input`.
    JSON Output:
"""
)

# Pre-written closing messages — zero LLM cost, selected by hash of
# conversation length to keep deterministic per session.
_CLOSING_POOL = [
    "Thanks for walking me through all of this — I have a much clearer picture now. I'll try applying these techniques to my own data.",
    "This was really helpful, I think I understand the core idea now. Let me go try it out.",
    "Got it, that makes a lot more sense now. Thanks for being so patient with my questions!",
    "I appreciate the detailed explanations. I'm going to revisit my code with this in mind.",
    "That clears things up — I wasn't thinking about it the right way before. Thanks!",
    "This has been great, I learned a lot. I'll experiment with what you showed me.",
    "Thanks for the help! I feel much more confident about tackling this now.",
    "Really appreciate you breaking it down step by step. I'll give it another shot.",
]

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


_LEDGER_BUDGET = 20_000


def _compute_file_diff(
    base_content: str, current_content: str, filename: str
) -> str:
    """Compute unified diff between two file versions."""
    base_lines = base_content.splitlines(keepends=True)
    current_lines = current_content.splitlines(keepends=True)
    diff = difflib.unified_diff(
        base_lines,
        current_lines,
        fromfile=f"a/{filename}",
        tofile=f"b/{filename}",
    )
    return "".join(diff)


def _format_transcript_with_files(
    conversation: list[dict],
    file_ledger: dict[str, dict],
    budget: int = _LEDGER_BUDGET,
) -> str:
    """Format conversation with inlined file content/diffs.

    Files are shown inline with the conversation turn that shared them.
    First share of a file shows full content; subsequent shares of the
    same file show a unified diff from the base version.  When total
    file content exceeds *budget*, oldest turns degrade to reference-only.
    """
    if not file_ledger:
        return _format_transcript(conversation)

    # Phase 1: compute file display info for each entry with attachments.
    # [(conv_idx, filename, display_text, char_count), ...]
    file_entries: list[tuple[int, str, str, int]] = []

    for idx, entry in enumerate(conversation):
        atts = entry.get("attachments")
        if not atts:
            continue
        for att in atts:
            fname = att["filename"]
            ledger = file_ledger.get(fname)

            # Images — text reference only (actual data goes via multimodal API)
            if (ledger and ledger.get("is_image")) or att.get("is_image"):
                text = f"[Image: {fname}]"
                file_entries.append((idx, fname, text, 0))
                continue

            base = ledger["base_content"] if ledger else None

            if base is not None and att["content"] != base:
                # Update — show diff (or full if diff is larger)
                diff_text = _compute_file_diff(base, att["content"], fname)
                if diff_text and len(diff_text) < len(att["content"]):
                    text = f"[File: {fname} (updated)]\n{diff_text}"
                    file_entries.append((idx, fname, text, len(diff_text)))
                    continue

            # First share or diff-larger-than-full fallback
            text = f"[File: {fname}]\n{att['content']}"
            file_entries.append((idx, fname, text, len(att["content"])))

    # Phase 2: budget — degrade oldest entries first when over budget.
    total_chars = sum(e[3] for e in file_entries)
    degraded: set[tuple[int, str]] = set()

    if total_chars > budget:
        sorted_oldest_first = sorted(file_entries, key=lambda e: e[0])
        excess = total_chars - budget
        for entry in sorted_oldest_first:
            if excess <= 0:
                break
            degraded.add((entry[0], entry[1]))
            excess -= entry[3]

    # Phase 3: build conv-index → file-text map.
    file_map: dict[int, list[str]] = {}
    for idx, fname, text, _chars in file_entries:
        if (idx, fname) in degraded:
            file_map.setdefault(idx, []).append(f"[Attached: {fname}]")
        else:
            file_map.setdefault(idx, []).append(text)

    # Phase 4: assemble transcript JSON.
    result = []
    for idx, entry in enumerate(conversation):
        item: dict = {"role": entry["role"], "content": entry["content"]}
        if idx in file_map:
            item["files"] = "\n".join(file_map[idx])
        result.append(item)

    return json.dumps(result, indent=4, ensure_ascii=False)


def _collect_images_from_ledger(
    file_ledger: dict[str, dict],
) -> list[dict]:
    """Extract image data from file ledger for multimodal API calls.

    Returns list of ``{"filename", "data", "media_type"}`` where
    *data* is a base64-encoded string.  Only the latest version
    (``current_content``) of each image is returned.
    """
    images: list[dict] = []
    for fname, entry in file_ledger.items():
        if entry.get("is_image"):
            images.append(
                {
                    "filename": fname,
                    "data": entry["current_content"],
                    "media_type": entry.get("media_type", "image/png"),
                }
            )
    return images


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
    ``GPTModel``, ``GPTModel``, or a plain model-name string.
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
            from server.eval.ewan_eval.model_resolver import (
                resolve_ewan_model as resolve_deepeval_model,
            )

            self._model = resolve_deepeval_model(self._model)
        return self._model

    def _generate_parsed(
        self, prompt: str, images: list[dict] | None = None
    ) -> str:
        """Generate text via model, parse JSON output, track cost.

        Tries structured output (schema=) first, falls back to plain
        text + JSON extraction.  Aligned with DeepEval's generate_schema()
        (conversation_simulator.py:606-623).
        """
        # Try structured output path (GPTModel / GPTModel).
        # Only catch TypeError/AttributeError/NotImplementedError — these
        # indicate the model doesn't support schema=.  Network errors,
        # rate limits, etc. should propagate (aligned with DeepEval's
        # generate_schema which only catches TypeError).
        try:
            result = self.model.generate(
                prompt, schema=SimulatedInput, images=images or None
            )
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
        result = self.model.generate(prompt, images=images or None)
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
        runtime_guidance: str = "",
        file_ledger: dict[str, dict] | None = None,
    ) -> str:
        """Generate the next student message given conversation history.

        Uses ``_FIRST_MESSAGE_PROMPT`` when the conversation has no
        assistant turns yet (aligned with DeepEval's
        ``generate_first_user_input``), otherwise uses
        ``_NEXT_MESSAGE_PROMPT`` (aligned with ``generate_next_user_input``).

        Args:
            conversation: [{"role": "user"|"assistant", "content": "..."}]
                "user" = student, "assistant" = tutor.
            file_ledger: Mapping of filename → {base_content, current_content, ...}.
                Used to inline file content/diffs into the transcript so the
                student can see shared workspace files in temporal context.

        Returns:
            The student's next message as a string.
        """
        is_first = not any(t["role"] == "assistant" for t in conversation)
        if is_first:
            prompt = _FIRST_MESSAGE_PROMPT.format(
                user_description=self.user_description,
                scenario=self.scenario,
            )
        else:
            runtime_guidance_block = ""
            if runtime_guidance.strip():
                runtime_guidance_block = (
                    "\n--- INTERNAL STEERING NOTES ---\n"
                    "Use these notes to shape the student's next message naturally. "
                    "Do NOT quote or reveal them directly.\n"
                    f"{runtime_guidance.strip()}\n"
                )
            if file_ledger:
                transcript = _format_transcript_with_files(
                    conversation, file_ledger
                )
                images = _collect_images_from_ledger(file_ledger)
            else:
                transcript = _format_transcript(conversation)
                images = []
            prompt = _NEXT_MESSAGE_PROMPT.format(
                user_description=self.user_description,
                scenario=self.scenario,
                transcript=transcript,
                runtime_guidance_block=runtime_guidance_block,
            )
        return self._generate_parsed(prompt, images=images or None)

    def generate_closing(
        self,
        conversation: list[dict[str, str]],
    ) -> str:
        """Select a pre-written closing message. Zero LLM cost.

        Uses conversation length as a deterministic seed so the same
        session always gets the same closing, but different sessions
        get variety.
        """
        idx = len(conversation) % len(_CLOSING_POOL)
        return _CLOSING_POOL[idx]
