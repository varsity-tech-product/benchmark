"""User simulator for QuantTutorBench.

Generates user messages via a model object (resolved by
``eval.judges.runtime.model_resolver.resolve_ewan_model``).

Used by TutoringSession behind the ``send_message`` MCP tool.
"""

import base64
import difflib
import hashlib
import json
import logging
import os
import re
import textwrap
import time
import uuid

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error types for user simulator failures
# ---------------------------------------------------------------------------

_MAX_GENERATE_ATTEMPTS = 3

# Stamped on every NPC LLM call's audit row. Bump when the user-sim
# prompt-construction path changes (template, system role, retry shape).
_USER_PROMPT_VERSION = "1.0"


class UserSimError(Exception):
    """Raised when user simulator exhausts all retry attempts."""

    def __init__(self, error_type: str, attempts: list[dict], message: str = ""):
        self.error_type = error_type  # "network" | "parse" | "empty"
        self.attempts = attempts  # [{attempt, error_type, detail, ...}, ...]
        super().__init__(
            message
            or f"User sim failed ({error_type}) after {len(attempts)} attempts"
        )

    @property
    def summary(self) -> dict:
        """Compact failure summary suitable for serialising into session results."""
        return {
            "final_error_type": self.error_type,
            "attempt_count": len(self.attempts),
            "error_types": [a.get("error_type") for a in self.attempts],
            "last_detail": self.attempts[-1].get("detail", "") if self.attempts else "",
        }


class SimulatedInput(BaseModel):
    """Schema for structured user message output.

    ``task_end`` lets the persona itself signal that the conversation is
    over. The session loop sends the reply to the agent and then closes —
    no more turns. Defaults to ``False`` for backward compatibility with
    older personas that have not been re-prompted yet.
    """

    simulated_input: str
    task_end: bool = False


# ---------------------------------------------------------------------------
# Prompt template
# ---------------------------------------------------------------------------

_NEXT_MESSAGE_PROMPT = textwrap.dedent(
    """\
    You are role-playing as a real person using an LLM tutoring app.

    {user_description}

    {scenario}

    Reply format: 2-4 sentences. You may react, answer the tutor's
    question, and ask what is still unclear. Do NOT explain concepts
    back in full detail — you are a user, not a co-teacher.
    {runtime_guidance_block}

    Conversation so far:
    {transcript}

    Respond with a JSON object with two keys:
      - `simulated_input` (string): your next reply to the tutor.
      - `task_end` (boolean): set to true ONLY when you would naturally
        walk away from this conversation — your question is fully
        answered, you have what you need, and the next thing you would
        do is leave the chat. Set to false in every other case,
        including when you are still asking, still confused, still
        working through something, or just being polite.

    When `task_end` is true, your `simulated_input` should read as a
    natural goodbye / sign-off message — that is the last thing the
    tutor will see before the session closes.

    JSON Output:
"""
)

# Repair prompt sent on parse failure instead of the original prompt.
# Includes the bad output so the model can see what went wrong.
_REPAIR_PROMPT = textwrap.dedent(
    """\
    Your previous response could not be parsed as a user reply.
    Your output was:
    ---
    {bad_output}
    ---
    Return ONLY valid JSON in this exact shape, with no markdown, commentary, or extra keys:
    {{"simulated_input": "...", "task_end": false}}
    Set `task_end` to true only if you would naturally end the chat now;
    otherwise leave it false.
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
# User end-of-session signal detection
# ---------------------------------------------------------------------------

# Phrases the user persona uses when satisfied and ready to end the session.
# Conservative list — only clear closing signals. False negatives (one extra
# turn) are cheaper than false positives (truncating a still-active session).
_GOODBYE_PATTERNS = (
    # "good to stop / end / wrap up / call it"
    r"\bgood to (?:stop|end|wrap up|leave it|move on|call it)\b",
    # "stop here for now", "stop here." — clause-final stop signal
    r"\b(?:stop|wrap up|leave it|call it)(?:\s+here)?\s+for now\b",
    r"\bstop here(?:\.|!|$)",
    # "I'm done" — must end the clause or carry closing context
    r"\b(?:I[' ]?m|I am)\s+done(?:\.|!|$)",
    r"\b(?:I[' ]?m|I am)\s+done\s+(?:for now|here|with (?:this|that|today|the (?:topic|material|explanation)))",
    # "I'm all set" — clause-final or explicit closing context.
    # Avoids matching "I'm all set with the rolling window, but stuck on Y."
    r"\b(?:I[' ]?m|I am)\s+all set(?:\s+(?:for now|now|here|with (?:that|this)))?(?:\.|!|$)",
    # "I'm good" — only with closing context (avoids "I'm good at math")
    r"\b(?:I[' ]?m|I am)\s+good\s+(?:to (?:stop|end|wrap up|go|leave|move on|call it)|for now|here|with (?:this|that))",
    # "I think I'm done|all set|all good" — clause-final or closing context.
    # Avoids matching "I think I'm done loading the data, what's next?" or
    # "I think I'm all good with the basics but unsure about Y."
    r"\bI think (?:I[' ]?m|I am|we[' ]?re|we are)\s+(?:done|all set|all good)(?:\s+(?:for now|now|here|with (?:that|this)))?(?:\.|!|$)",
    # "I think I'm good|fine" — must be clause-final, with the optional
    # closing-context phrase itself ending the clause. Avoids matching
    # "I think I'm good with the rolling window..." or "fine with that part".
    r"\bI think (?:I[' ]?m|I am|we[' ]?re|we are)\s+(?:good|fine)(?:\s+(?:for now|here|with (?:that|this)))?(?:\.|!|$)",
    # "That's all (I needed)"
    r"\bthat[' ]?s all (?:I needed|I had|for now|the help I needed|I was after)\b",
    r"\bthanks,? that[' ]?s all\b",
    r"\bthat[' ]?s (?:all|it),? thanks\b",
    # "No more questions"
    r"\bno (?:more|further|other) questions\b",
    # "Call it a day", "let's wrap up"
    r"\bcall it (?:a day|done|here)\b",
    r"\blet[' ]?s (?:wrap|call) (?:up|it up|things up|it (?:done|a day))\b",
    # Intent to apply independently — only with explicit "this/that/it" object
    # and clause-final marker (avoids "let me try this on the data")
    r"\b(?:I[' ]?ll|let me)\s+(?:go )?(?:try|run|test|apply|implement|practice|revisit|experiment with)\s+(?:this|that|it)(?:\s+(?:on my own|myself|now|out))?(?:\.|!|$)",
)

_GOODBYE_RE = re.compile("|".join(_GOODBYE_PATTERNS), re.IGNORECASE)


def signaled_end_of_session(text: str) -> bool:
    """Heuristic: did the user's reply signal session-end satisfaction?

    Triggered by phrases the user persona uses to wind down — "I'm good
    to stop here for now", "I'm done", "thanks, that's all", "no more
    questions", "let me try this on my own". A trailing question mark
    disqualifies the match (a user still asking is not actually done).

    Conservative by design: false negatives are cheaper than false positives.
    Returns False on empty input.
    """
    if not text or not text.strip():
        return False
    # The LLM-driven user persona emits Unicode curly apostrophes
    # (U+2019) in contractions like "I'm". The goodbye patterns use the
    # ASCII apostrophe, so normalize before matching to avoid false negatives.
    normalized = text.replace("’", "'").replace("‘", "'").replace("ʼ", "'")
    stripped = normalized.strip()
    # Trailing question — user is still asking, not done.
    if stripped.endswith("?"):
        return False
    return bool(_GOODBYE_RE.search(stripped))


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


def _compute_file_diff(base_content: str, current_content: str, filename: str) -> str:
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
    same file show a unified diff from the previous version.  When total
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

            prev = ledger["prev_content"] if ledger else None

            if prev is not None and att["content"] != prev:
                # Update — show diff (or full if diff is larger)
                diff_text = _compute_file_diff(prev, att["content"], fname)
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
    workspace_path: str | None = None,
) -> list[dict]:
    """Extract image data from file ledger for multimodal API calls.

    Returns list of ``{"filename", "data", "media_type"}`` where
    *data* is a base64-encoded string.  Images are read from disk
    on demand to avoid holding base64 data in the ledger permanently.
    """
    if not workspace_path:
        return []
    images: list[dict] = []
    for fname, entry in file_ledger.items():
        if not entry.get("is_image"):
            continue
        path = os.path.join(workspace_path, entry.get("path", fname))
        real = os.path.realpath(path)
        try:
            fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
        except OSError:
            continue
        images.append(
            {
                "filename": fname,
                "data": data,
                "media_type": entry.get("media_type", "image/png"),
            }
        )
    return images


def _parse_simulated_input_strict(raw: str) -> tuple[str, bool, str]:
    """Extract ``simulated_input`` and ``task_end`` from JSON output.

    Returns ``(text, task_end, parse_method)`` on success where
    ``parse_method`` is one of:
    - ``"json"``: extracted from ``simulated_input`` key
    - ``"json_single_value"``: extracted from the only string value in a JSON object
    - ``"fallback_text"``: accepted as natural-language user message

    ``task_end`` defaults to ``False`` whenever the field is missing,
    not a real Python bool, or the canonical key path is bypassed
    (single-value / fallback-text). Bias to False so a malformed
    response never accidentally terminates a still-active session.

    Raises ``ValueError`` if parsing fails.  Empty-output errors have the
    prefix ``"empty:"`` so callers can distinguish them from structural parse
    failures.
    """
    if not raw or not raw.strip():
        raise ValueError("empty: LLM returned empty output")

    match = _JSON_RE.search(raw)
    if match:
        try:
            data = json.loads(match.group())
            text = data.get("simulated_input")
            if text and text.strip():
                raw_end = data.get("task_end", False)
                # Strict bool: only Python True terminates. Anything else
                # (string "true", 1, missing) is treated as False.
                task_end = raw_end is True
                return text.strip(), task_end, "json"
            # simulated_input missing or empty — try single-value fallback
            # before giving up: if the object has exactly one string value and
            # it passes the usability check, accept it with a warning.
            string_values = [
                v for v in data.values() if isinstance(v, str) and v.strip()
            ]
            if len(string_values) == 1 and _is_usable_user_message(
                string_values[0].strip()
            ):
                logger.warning(
                    "User sim: simulated_input key missing; "
                    "accepted single string value from JSON (key drift). raw=%r",
                    raw[:120],
                )
                return string_values[0].strip(), False, "json_single_value"
        except (json.JSONDecodeError, TypeError):
            pass
        # JSON found but no usable value — not usable as-is
        raise ValueError(f"JSON found but no valid simulated_input: {raw[:120]!r}")

    # No JSON — check if raw text is usable as a user message
    stripped = raw.strip()
    if _is_usable_user_message(stripped):
        return stripped, False, "fallback_text"

    raise ValueError(f"No JSON and text not usable as user message: {raw[:120]!r}")


# Patterns that indicate prompt leakage or meta content — not a real user message
_META_PATTERNS = re.compile(
    r"simulated_input|JSON Output:|You are role-playing|"
    r"Respond with a JSON|Reply format:|Conversation so far:|"
    r"^```\w*\n",  # Code fence at start of output
    re.IGNORECASE | re.MULTILINE,
)


def _is_usable_user_message(text: str) -> bool:
    """Check if non-JSON text is usable as a user message.

    Accepts natural user text (e.g., when model skips JSON wrapper),
    including short replies like "Why?" or "Got it." that a real user
    might send.
    Rejects prompt leakage, meta content, empty, or degenerate output.
    """
    if not text or not text.strip():
        return False
    if len(text) > 2000:
        return False
    if _META_PATTERNS.search(text):
        return False
    # Must contain at least one alphabetic character — rejects pure
    # punctuation, digits, or brace-heavy garbled output.
    if not any(c.isalpha() for c in text):
        return False
    # For longer text, also require a minimum alpha density to reject
    # outputs dominated by special characters or JSON fragments.
    if len(text) >= 10:
        alpha_count = sum(c.isalpha() for c in text)
        if alpha_count / len(text) < 0.4:
            return False
    return True


# ---------------------------------------------------------------------------
# UserSimulator
# ---------------------------------------------------------------------------


class UserSimulator:
    """Generates user messages via an LLM client.

    Accepts any object returned by ``resolve_ewan_model()`` —
    ``EwanLLMClient`` or a plain model-name string.
    When a plain string is passed, it is resolved lazily on first use.

    Uses a unified retry budget (``_MAX_GENERATE_ATTEMPTS``) for both
    network errors and parse failures.  Raises ``UserSimError`` when
    all attempts are exhausted.
    """

    def __init__(
        self,
        scenario: str,
        user_description: str,
        model=None,
    ):
        self.scenario = scenario
        self.user_description = user_description
        self._model = model
        self.total_cost: float = 0.0

    @property
    def model(self):
        """Lazy-resolve plain string model names on first use."""
        if isinstance(self._model, str) or self._model is None:
            from eval.judges.runtime.model_resolver import resolve_ewan_model

            self._model = resolve_ewan_model(self._model)
        return self._model

    def _generate_parsed(
        self, prompt: str, images: list[dict] | None = None
    ) -> tuple[str, bool]:
        """Generate text via model with retry, parse JSON output, track cost.

        Returns ``(text, task_end)``.  ``task_end`` is the persona-emitted
        end-of-session flag (defaults to ``False`` for any non-canonical
        parse path or missing key).

        Retry strategy (budget: ``_MAX_GENERATE_ATTEMPTS``):
        - network error  → retry with original prompt (transient failure)
        - empty output   → retry once with original prompt, then fail
        - parse error    → retry with repair prompt containing the bad output

        After all attempts are exhausted, raises ``UserSimError`` with full
        attempt history.
        """
        attempts: list[dict] = []
        # The prompt actually sent on the current attempt.  Starts as the
        # original prompt; becomes a repair prompt after a parse/empty failure.
        current_prompt = prompt
        last_bad_output: str = ""

        for attempt_idx in range(_MAX_GENERATE_ATTEMPTS):
            attempt_record: dict = {"attempt": attempt_idx + 1, "ts": time.time()}

            # --- Network layer ---
            try:
                result = self.model.generate(
                    current_prompt,
                    images=images or None,
                    call_id=f"npc.user-{uuid.uuid4().hex[:8]}-attempt{attempt_idx}",
                    prompt_id="npc.user",
                    prompt_version=_USER_PROMPT_VERSION,
                )
            except Exception as exc:
                attempt_record.update(
                    error_type="network",
                    detail=f"{type(exc).__name__}: {exc}",
                )
                attempts.append(attempt_record)
                logger.warning(
                    "User sim network error (attempt %d/%d): %s",
                    attempt_idx + 1,
                    _MAX_GENERATE_ATTEMPTS,
                    exc,
                )
                # Network failure — keep original prompt, don't build repair
                current_prompt = prompt
                continue

            if isinstance(result, tuple):
                text = result[0]
                cost = result[1] if len(result) > 1 else None
                if cost is not None:
                    self.total_cost += cost
                    attempt_record["cost"] = cost
            else:
                text = result
            attempt_record["output_len"] = len(text or "")

            # --- Parse layer ---
            try:
                parsed, task_end, parse_method = _parse_simulated_input_strict(text)
                attempt_record.update(
                    error_type=None,
                    parse_method=parse_method,
                    task_end=task_end,
                )
                attempts.append(attempt_record)
                if len(attempts) > 1:
                    logger.info(
                        "User sim succeeded on attempt %d/%d",
                        attempt_idx + 1,
                        _MAX_GENERATE_ATTEMPTS,
                    )
                return parsed, task_end
            except ValueError as exc:
                detail = str(exc)
                # Distinguish empty output from structural parse failure so
                # downstream filtering and retry logic can treat them differently.
                error_type = "empty" if detail.startswith("empty:") else "parse"
                attempt_record.update(error_type=error_type, detail=detail)
                attempts.append(attempt_record)
                logger.warning(
                    "User sim %s error (attempt %d/%d): %s",
                    error_type,
                    attempt_idx + 1,
                    _MAX_GENERATE_ATTEMPTS,
                    exc,
                )
                if error_type == "parse":
                    # Build a repair prompt for the next attempt.
                    # Include the bad output (truncated) so the model can see
                    # what it produced and correct course.  Do NOT re-send the
                    # full original prompt — that wastes tokens and doesn't help.
                    last_bad_output = (text or "")[:300]
                    current_prompt = _REPAIR_PROMPT.format(bad_output=last_bad_output)
                else:
                    # Empty output — likely a transient API issue.  Retry with
                    # the original prompt; a repair prompt with nothing to show
                    # the model would be nonsensical.
                    current_prompt = prompt
                continue

        # All attempts exhausted
        last_type = attempts[-1].get("error_type", "unknown") if attempts else "unknown"
        raise UserSimError(
            error_type=last_type,
            attempts=attempts,
        )

    def generate_message(
        self,
        conversation: list[dict[str, str]],
        runtime_guidance: str = "",
        file_ledger: dict[str, dict] | None = None,
        workspace_path: str | None = None,
    ) -> tuple[str, bool]:
        """Generate the next user message given conversation history.

        Args:
            conversation: [{"role": "user"|"assistant", "content": "..."}]
                "user" = user, "assistant" = tutor.
            file_ledger: Mapping of filename → {base_content, current_content, ...}.
                Used to inline file content/diffs into the transcript so the
                user can see shared workspace files in temporal context.

        Returns:
            ``(text, task_end)`` — the user's next message and the
            persona-emitted end-of-session flag. ``task_end=True`` means
            the session loop should send this reply to the agent and
            then close (no further turns).
        """
        runtime_guidance_block = ""
        if runtime_guidance.strip():
            runtime_guidance_block = (
                "\n--- INTERNAL STEERING NOTES ---\n"
                "Use these notes to shape the user's next message naturally. "
                "Do NOT quote or reveal them directly.\n"
                f"{runtime_guidance.strip()}\n"
            )
        if file_ledger:
            transcript = _format_transcript_with_files(conversation, file_ledger)
            images = _collect_images_from_ledger(file_ledger, workspace_path)
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

        Uses a hash of the last message for even distribution across
        the pool while remaining deterministic per session.
        """
        last = conversation[-1]["content"] if conversation else ""
        seed = hashlib.md5(last.encode()).digest()[0]
        return _CLOSING_POOL[seed % len(_CLOSING_POOL)]


def require_user_model(model=None, *, temperature: float = 0.0):
    """Resolve an NPC user-simulator model — must be vision-capable.

    Restricted to ``USER_MODEL_POOL_ALL`` (configured in
    ``server.config.llm_config``) so the user can ingest image
    attachments. Lives here rather than in ``bench/eval/`` because
    user-model selection is an NPC-runtime concern, not a scoring
    concern.
    """
    from eval.judges.runtime.model_resolver import require_ewan_model
    from server.config.llm_config import (
        SIMULATOR_DEFAULT_MODEL,
        USER_MODEL_POOL_ALL,
    )

    model = model or SIMULATOR_DEFAULT_MODEL
    if isinstance(model, str) and model not in USER_MODEL_POOL_ALL:
        raise RuntimeError(
            f"User simulator model {model!r} is not in the "
            "vision-capable model pool. Choose from: "
            + ", ".join(sorted(USER_MODEL_POOL_ALL))
        )
    return require_ewan_model(
        model, purpose="user simulator", temperature=temperature
    )
