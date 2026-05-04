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

    Session context visible to you:
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

    When tool logs show failures or repeated tool calls without progress,
    you may either give a hint in your reply or set `task_end` to true if
    continuing would waste turns.

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


_LEDGER_BUDGET = 6_000
_TOOL_LOG_BUDGET = 10_000
_TOOL_RESULT_MAX_CHARS = 500
_TOOL_LOG_DETAILED_TURNS = 5
_TOOL_LOG_MIN_DETAILED_TURNS = 3
_PROTOCOL_TOOL_NAMES = frozenset(
    {
        "register_session",
        "start_session",
        "send_message",
        "get_background",
        "get_session_info",
    }
)


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
    """Format conversation with inlined file content.

    Files are shown inline with the conversation turn that shared them.
    First share of a file shows full content; subsequent shares degrade to
    reference-only. When total file content exceeds *budget*, oldest first
    shares also degrade to reference-only.
    """
    if not file_ledger:
        return _format_transcript(conversation)

    file_map = _build_file_display_map(conversation, file_ledger, budget)

    result = []
    for idx, entry in enumerate(conversation):
        item: dict = {"role": entry["role"], "content": entry["content"]}
        if idx in file_map:
            item["files"] = "\n".join(file_map[idx])
        result.append(item)

    return json.dumps(result, indent=4, ensure_ascii=False)


def _build_file_display_map(
    conversation: list[dict],
    file_ledger: dict[str, dict],
    budget: int = _LEDGER_BUDGET,
) -> dict[int, list[str]]:
    """Return conversation-indexed attachment text under the file budget."""
    if not file_ledger:
        return {}

    file_entries: list[dict] = []
    seen_filenames: set[str] = set()

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
                file_entries.append(
                    {
                        "idx": idx,
                        "filename": fname,
                        "text": text,
                        "char_count": 0,
                        "degraded_text": text,
                    }
                )
                seen_filenames.add(fname)
                continue

            content = str(att.get("content", ""))
            if fname in seen_filenames:
                file_entries.append(
                    {
                        "idx": idx,
                        "filename": fname,
                        "text": f"[Attached: {fname}]",
                        "char_count": 0,
                        "degraded_text": f"[Attached: {fname}]",
                    }
                )
                continue

            text = f"[File: {fname}]\n{content}"
            file_entries.append(
                {
                    "idx": idx,
                    "filename": fname,
                    "text": text,
                    "char_count": len(content),
                    "degraded_text": f"[Attached: {fname}]",
                }
            )
            seen_filenames.add(fname)

    total_chars = sum(int(e["char_count"]) for e in file_entries)
    degraded: set[int] = set()

    if total_chars > budget:
        excess = total_chars - budget
        for entry_idx, entry in enumerate(file_entries):
            if excess <= 0:
                break
            char_count = int(entry["char_count"])
            if char_count <= 0:
                continue
            degraded.add(entry_idx)
            excess -= char_count

    file_map: dict[int, list[str]] = {}
    for entry_idx, entry in enumerate(file_entries):
        text = entry["degraded_text"] if entry_idx in degraded else entry["text"]
        file_map.setdefault(int(entry["idx"]), []).append(str(text))

    return file_map


def _tool_log_get(log, key: str, default=None):
    if isinstance(log, dict):
        return log.get(key, default)
    return getattr(log, key, default)


def _coerce_tool_success(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() not in {"false", "0", "no", "failed"}


def _normalize_tool_log(log) -> dict | None:
    name = str(
        _tool_log_get(log, "name")
        or _tool_log_get(log, "tool_name")
        or _tool_log_get(log, "tool")
        or ""
    ).strip()
    if not name or name in _PROTOCOL_TOOL_NAMES:
        return None

    args = _tool_log_get(log, "args", {})
    if not isinstance(args, dict):
        args = {"value": args}

    try:
        turn_index = int(_tool_log_get(log, "turn_index", 0) or 0)
    except (TypeError, ValueError):
        turn_index = 0

    return {
        "name": name,
        "args": args,
        "result": str(_tool_log_get(log, "result", "") or ""),
        "success": _coerce_tool_success(_tool_log_get(log, "success", True)),
        "turn": max(turn_index, 0) + 1,
    }


def _truncate_success_result(result: str, max_chars: int = _TOOL_RESULT_MAX_CHARS) -> str:
    if len(result) <= max_chars:
        return result
    omitted = len(result) - max_chars
    return f"{result[:max_chars]}... [{omitted:,} chars omitted]"


def _format_tool_detail(log: dict, ordinal: int) -> str:
    args_text = json.dumps(log["args"], ensure_ascii=False, sort_keys=True, default=str)
    if log["success"]:
        result_text = json.dumps(
            _truncate_success_result(log["result"]),
            ensure_ascii=False,
        )
        return f"    {ordinal}. {log['name']}({args_text}) -> ok, {result_text}"
    return f"    {ordinal}. {log['name']}({args_text}) -> FAILED: {log['result']}"


def _format_tool_summary(turn: int, logs: list[dict]) -> str:
    counts: dict[str, int] = {}
    for log in logs:
        counts[log["name"]] = counts.get(log["name"], 0) + 1
    count_text = ", ".join(f"{name}x{count}" for name, count in counts.items())
    failures = sum(1 for log in logs if not log["success"])
    status = "all success" if failures == 0 else f"{failures} failed"
    return f"[turn {turn}: {count_text}, {status}]"


def _group_tool_logs_by_turn(tool_logs: list) -> list[tuple[int, list[dict]]]:
    grouped: dict[int, list[dict]] = {}
    order: list[int] = []
    for raw in tool_logs or []:
        log = _normalize_tool_log(raw)
        if log is None:
            continue
        turn = int(log["turn"])
        if turn not in grouped:
            grouped[turn] = []
            order.append(turn)
        grouped[turn].append(log)
    return [(turn, grouped[turn]) for turn in order]


def _render_tool_log_context(
    groups: list[tuple[int, list[dict]]],
    detailed_turns: int,
) -> str:
    if not groups:
        return ""

    detailed_turns = max(0, min(detailed_turns, len(groups)))
    older = groups[:-detailed_turns] if detailed_turns else groups
    recent = groups[-detailed_turns:] if detailed_turns else []

    lines: list[str] = []
    if older:
        lines.append("Older tool turns:")
        lines.extend(_format_tool_summary(turn, logs) for turn, logs in older)
    if recent:
        if lines:
            lines.append("")
        lines.append("Recent tool turns:")
        for turn, logs in recent:
            lines.append(f"[turn {turn} - agent]")
            lines.append("  tools:")
            for i, log in enumerate(logs, 1):
                lines.append(_format_tool_detail(log, i))

    return "\n".join(lines)


def _format_tool_log_context(
    tool_logs: list,
    budget: int = _TOOL_LOG_BUDGET,
) -> str:
    """Format tool logs for the user persona under a bounded prompt budget."""
    groups = _group_tool_logs_by_turn(tool_logs)
    if not groups:
        return ""

    candidates = list(
        range(
            min(_TOOL_LOG_DETAILED_TURNS, len(groups)),
            _TOOL_LOG_MIN_DETAILED_TURNS - 1,
            -1,
        )
    )
    if _TOOL_LOG_MIN_DETAILED_TURNS > len(groups):
        candidates = [len(groups)]
    candidates.extend(i for i in (2, 1, 0) if i not in candidates)

    best = ""
    for detailed_turns in candidates:
        rendered = _render_tool_log_context(groups, detailed_turns)
        best = rendered
        if len(rendered) <= budget:
            return rendered

    if len(best) <= budget:
        return best

    marker = f"[tool log context clipped to {budget:,} chars]\n"
    if budget <= len(marker):
        return marker[:budget]
    return marker + best[-(budget - len(marker)) :]


def _format_user_context(
    conversation: list[dict],
    file_ledger: dict[str, dict] | None = None,
    tool_logs: list | None = None,
) -> str:
    """Format visible chat, shared files, and tool activity for user-sim."""
    file_map = _build_file_display_map(conversation, file_ledger or {})
    tool_context = _format_tool_log_context(tool_logs or [])

    lines: list[str] = []
    if tool_context:
        lines.append("Agent tool activity visible to you:")
        lines.append(tool_context)
        lines.append("")

    lines.append("Conversation so far:")
    agent_turn = 0
    for idx, entry in enumerate(conversation):
        role = entry.get("role", "")
        if role == "assistant":
            agent_turn += 1
            label = f"[turn {agent_turn} - agent]"
            message_key = "reply"
        else:
            label = f"[turn {agent_turn} - user]"
            message_key = "message"
        lines.append(label)
        if idx in file_map:
            lines.append("  files:")
            for file_text in file_map[idx]:
                for file_line in file_text.splitlines() or [""]:
                    lines.append(f"    {file_line}")
        content = json.dumps(entry.get("content", ""), ensure_ascii=False)
        lines.append(f"  {message_key}: {content}")

    return "\n".join(lines)


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
    r"Respond with a JSON|Reply format:|Session context visible to you:|"
    r"Agent tool activity visible to you:|Conversation so far:|"
    r"Older tool turns:|Recent tool turns:|"
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
        tool_logs: list | None = None,
        workspace_path: str | None = None,
    ) -> tuple[str, bool]:
        """Generate the next user message given conversation history.

        Args:
            conversation: [{"role": "user"|"assistant", "content": "..."}]
                "user" = user, "assistant" = tutor.
            file_ledger: Mapping of filename → {base_content, current_content, ...}.
                Used to inline first-share file content into the transcript so
                the user can see shared workspace files in temporal context.
            tool_logs: Tool-call logs visible to the user persona. Tool names,
                arguments, success flags, and results are included under a
                bounded budget; private reasoning stays out of the prompt.

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
        transcript = _format_user_context(
            conversation,
            file_ledger=file_ledger,
            tool_logs=tool_logs,
        )
        images = (
            _collect_images_from_ledger(file_ledger, workspace_path)
            if file_ledger
            else []
        )
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
