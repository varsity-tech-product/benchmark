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

import base64
import json
import logging
import os
import time
from typing import Optional

from server.core.artifact_digest import build_visible_artifact_digest
from server.core.tc_evidence import build_turn_evidence
from server.core.workspace_delta import scan_workspace_snapshot

logger = logging.getLogger(__name__)

# Fallback student message when generate_message() fails — aligned with
# model_callback exception handler (simulation.py:586).
_STUDENT_FALLBACK = "Could you explain that in a bit more detail?"

# Repeat detection threshold — aligned with model_callback _MAX_REPEATS
# (simulation.py:493).
_MAX_REPEATS = 2
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
        "The student cannot see your tool calls, file operations, "
        "raw command output, or files in your workspace. If you generate "
        "charts, code files, or other artifacts the student should see, "
        "include their file paths in the 'attachments' parameter of "
        "send_message.",
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


# ---------------------------------------------------------------------------
# Attachment support
# ---------------------------------------------------------------------------

_ATTACHMENT_MAX_FILES = 3
_ATTACHMENT_MAX_CHARS = 4_000

_IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"})
_IMAGE_MAX_BYTES = 5 * 1024 * 1024  # 5 MB raw before base64
_IMAGE_LEDGER_MAX = 5  # max images in ledger across session

_BINARY_EXTENSIONS = frozenset(
    {
        ".ico",
        ".svg",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".7z",
        ".pkl",
        ".pickle",
        ".npy",
        ".npz",
        ".pyc",
        ".so",
        ".dylib",
        ".dll",
        ".exe",
        ".whl",
        ".class",
        ".o",
    }
)

_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
}


def _safe_open(real: str, mode: str = "rb", encoding: str | None = None):
    """Open a file rejecting symlinks (O_NOFOLLOW) to close TOCTOU gaps."""
    try:
        fd = os.open(real, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:
        raise ValueError(f"Cannot open (symlink or inaccessible): {real}")
    return os.fdopen(fd, mode, encoding=encoding) if encoding else os.fdopen(fd, mode)


def _read_attachment(workspace_path: str, filename: str) -> dict:
    """Read a single workspace file for attachment.

    Returns a dict with at least ``filename``, ``content``, ``truncated``,
    and ``is_image``.  Image attachments also include ``media_type``.
    Raises ``ValueError`` on validation failure.
    """
    if not workspace_path:
        raise ValueError("No workspace available")

    # ── Filename validation (C3) ──
    if not filename or not filename.strip():
        raise ValueError("Empty filename")
    if "\x00" in filename:
        raise ValueError("Filename contains null byte")

    # Agents often use container-absolute paths ("/workspace/foo.py") or
    # prefixed relative paths ("workspace/foo.py").  Strip the prefix so
    # the join below resolves correctly against the real workspace root.
    cleaned = filename.strip()
    for prefix in ("/workspace/", "workspace/"):
        if cleaned.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break

    resolved = os.path.join(workspace_path, cleaned)
    real = os.path.realpath(resolved)
    workspace_real = os.path.realpath(workspace_path)

    if not real.startswith(workspace_real + os.sep):
        raise ValueError(f"Path outside workspace: {filename}")

    if not os.path.isfile(real):
        raise ValueError(f"File not found: {filename}")

    _, ext = os.path.splitext(real)
    ext_lower = ext.lower()

    # ── Image files ──
    if ext_lower in _IMAGE_EXTENSIONS:
        size = os.path.getsize(real)
        if size > _IMAGE_MAX_BYTES:
            raise ValueError(
                f"Image too large: {filename} "
                f"({size:,} bytes, max {_IMAGE_MAX_BYTES:,})"
            )
        with _safe_open(real, "rb") as f:
            data = base64.b64encode(f.read()).decode("ascii")
        return {
            "filename": filename,
            "content": data,
            "truncated": False,
            "is_image": True,
            "media_type": _MEDIA_TYPES[ext_lower],
        }

    # ── Remaining binary files — rejected ──
    if ext_lower in _BINARY_EXTENSIONS:
        raise ValueError(f"Binary file not supported: {filename}")

    # ── Text files ──
    try:
        with _safe_open(real, "r", encoding="utf-8") as f:
            content = f.read()
    except UnicodeDecodeError:
        raise ValueError(f"Cannot read as text: {filename}")

    truncated = len(content) > _ATTACHMENT_MAX_CHARS
    if truncated:
        head = _ATTACHMENT_MAX_CHARS * 2 // 3
        tail = _ATTACHMENT_MAX_CHARS // 3
        content = (
            content[:head] + f"\n\n... [{len(content):,} chars total, showing first "
            f"{head:,} + last {tail:,}] ...\n\n" + content[-tail:]
        )

    return {
        "filename": filename,
        "content": content,
        "truncated": truncated,
        "is_image": False,
    }


def _resolve_attachments(
    workspace_path: str | None, raw_paths: list[str]
) -> tuple[list[dict], list[str]]:
    """Resolve and read attachment files.

    Returns ``(attachments, errors)``.
    """
    if not raw_paths:
        return [], []

    if len(raw_paths) > _ATTACHMENT_MAX_FILES:
        return [], [f"Maximum {_ATTACHMENT_MAX_FILES} attachments allowed"]

    attachments: list[dict] = []
    errors: list[str] = []
    for path in raw_paths:
        try:
            attachments.append(_read_attachment(workspace_path or "", path))
        except ValueError as exc:
            errors.append(str(exc))

    return attachments, errors


# ---------------------------------------------------------------------------
# TutoringSession
# ---------------------------------------------------------------------------


class TutoringSession:
    """Manages a single tutoring session.

    The agent interacts with the student exclusively through
    ``send_message`` tool calls.  This class:

    - Maintains the conversation history
    - Generates student replies via StudentSimulator
    - Checks termination criteria via TCChecker
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
        workspace_path: Optional[str] = None,
    ):
        self._task = task
        self._persona = persona
        self._student_sim = student_sim
        self._tc_checker = tc_checker
        self._max_turns = max_turns
        self._deadline = deadline
        self._proxy = proxy  # For set_turn() calls
        self._workspace_path = workspace_path

        self._conversation: list[dict[str, str]] = []
        self._turn: int = 0
        self._done: bool = False
        self._session_info_called: bool = False
        self._completion_reason: str | None = None

        # File ledger — tracks all files shared via attachments.
        # Key: filename, Value: {base_content, base_turn, current_content, current_turn}
        # Same-name files are deduped (latest version replaces current_content).
        self._file_ledger: dict[str, dict] = {}
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
        self._conversation.append(
            {"role": "user", "content": opening, "ts": time.time()}
        )
        self._session_info_called = True

        background = build_background(self._task)
        return json.dumps(
            {
                "background": background,
                "student_message": opening,
            }
        )

    def handle_send_message(
        self,
        text: str,
        attachments: list[str] | None = None,
        reasoning: str | None = None,
    ) -> str:
        """Process agent message, generate student reply.

        ``reasoning`` is the agent's private rationale for this turn.
        It is stored as metadata on the conversation entry for trace
        analysis but is NEVER passed to the student simulator (which
        only reads ``entry["content"]``).

        Execution order aligned with Legacy model_callback
        (simulation.py:495-637) and DeepEval _simulate_single_conversation
        (conversation_simulator.py:226-272):

        1. Pre-checks (done, empty)
        2. Resolve attachments
        3. Repeat detection (model_callback:606-624)
        4. Record + advance turn
        5. TC check (_EfficientSimulator.stop_conversation)
        6. Deadline check
        8. Max turns (_append_student_closing:642-678)
        9. Generate student reply (generate_next_user_input)

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

        # ── Resolve attachments ──
        resolved_attachments, attach_errors = _resolve_attachments(
            self._workspace_path, attachments or []
        )
        if attach_errors:
            return json.dumps(
                {
                    "error": "Attachment errors: " + "; ".join(attach_errors),
                    "status": "active",
                }
            )

        # ── Image ledger limit ──
        new_images = sum(1 for a in resolved_attachments if a.get("is_image"))
        if new_images:
            existing_images = sum(
                1 for e in self._file_ledger.values() if e.get("is_image")
            )
            # Don't count images that will be overwritten (same filename)
            overwrites = sum(
                1
                for a in resolved_attachments
                if a.get("is_image") and a["filename"] in self._file_ledger
            )
            if existing_images + new_images - overwrites > _IMAGE_LEDGER_MAX:
                return json.dumps(
                    {
                        "error": f"Maximum {_IMAGE_LEDGER_MAX} images per session",
                        "status": "active",
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
        msg_entry: dict = {"role": "assistant", "content": text, "ts": time.time()}
        # Stash private rationale on the entry for trace analysis. Student
        # simulator reads only ``content``, so this never leaks.
        if reasoning and reasoning.strip():
            msg_entry["reasoning"] = reasoning.strip()
        if resolved_attachments:
            # Store metadata only — strip base64 content from images to avoid
            # unbounded memory growth in conversation history.
            msg_entry["attachments"] = [
                {
                    k: v
                    for k, v in att.items()
                    if not (att.get("is_image") and k == "content")
                }
                for att in resolved_attachments
            ]
        self._conversation.append(msg_entry)
        self._turn += 1

        # ── Update file ledger ──
        for att in resolved_attachments:
            fname = att["filename"]
            is_image = att.get("is_image", False)
            if fname not in self._file_ledger:
                self._file_ledger[fname] = {
                    # Text: store content for diff. Images: store None (read from disk on demand).
                    "prev_content": att["content"] if not is_image else None,
                    "prev_turn": self._turn,
                    "current_content": att["content"] if not is_image else None,
                    "current_turn": self._turn,
                    "is_image": is_image,
                    "media_type": att.get("media_type", ""),
                    "path": fname,  # workspace-relative, used for on-demand image reads
                }
            else:
                if not is_image:
                    # Shift current → prev so diff shows incremental changes
                    self._file_ledger[fname]["prev_content"] = self._file_ledger[fname][
                        "current_content"
                    ]
                    self._file_ledger[fname]["current_content"] = att["content"]
                self._file_ledger[fname]["current_turn"] = self._turn

        # Update proxy turn index for tool log attribution
        if self._proxy is not None:
            self._proxy.set_turn(self._turn)

        tc_turn_index = self._current_tool_turn_index()
        turn_evidence = self._build_turn_evidence(tc_turn_index)
        attached_filenames = frozenset(a["filename"] for a in resolved_attachments)
        artifact_digest = self._build_artifact_digest(text, attached_filenames)

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
                self._conversation.append(
                    {"role": "user", "content": closing, "ts": time.time()}
                )
            self._done = True
            self._completion_reason = "objectives_met"
            logger.info("TC fully covered at turn %d.", self._turn)
            return self._result(closing, "completed", reason="objectives_met")

        # ── Deadline check ──
        # Let the just-sent tutor message contribute to TC completion
        # before converting the session into a timeout.
        if self._deadline is not None and time.time() > self._deadline:
            self._done = True
            self._completion_reason = "timeout"
            logger.info("Session timed out at turn %d.", self._turn)
            closing = self._safe_closing()
            if closing:
                self._conversation.append(
                    {"role": "user", "content": closing, "ts": time.time()}
                )
            return self._result(closing, "completed", reason="timeout")

        # ── Max turns check ──  (aligned: _append_student_closing:642-678)
        if self._turn >= self._max_turns:
            self._done = True
            self._completion_reason = "max_turns"
            logger.info("Max turns (%d) reached.", self._max_turns)
            closing = self._safe_closing()
            if closing:
                self._conversation.append(
                    {"role": "user", "content": closing, "ts": time.time()}
                )
            return self._result(closing, "completed", reason="max_turns")

        # ── Generate student reply ──  (aligned: generate_next_user_input)
        try:
            reply = self._student_sim.generate_message(
                self._conversation,
                runtime_guidance=self._build_student_runtime_guidance(
                    text,
                    artifact_digest,
                ),
                file_ledger=self._file_ledger,
                workspace_path=self._workspace_path,
            )
        except Exception as exc:
            from server.core.student_sim import StudentSimError

            error_type = (
                exc.error_type
                if isinstance(exc, StudentSimError)
                else type(exc).__name__
            )
            reason = f"student_sim_error:{error_type}"
            logger.error(
                "Student simulator failed at turn %d, terminating session: %s",
                self._turn,
                exc,
            )
            self._done = True
            self._completion_reason = reason
            return self._result("", "completed", reason=reason)
        self._conversation.append({"role": "user", "content": reply, "ts": time.time()})

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
    def file_ledger(self) -> dict[str, dict]:
        """All files shared via attachments — latest versions, keyed by filename."""
        return dict(self._file_ledger)

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
                self._conversation.append(
                    {"role": "user", "content": closing, "ts": time.time()}
                )
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

    def _build_artifact_digest(
        self,
        latest_agent_text: str,
        shared_filenames: frozenset[str] = frozenset(),
    ) -> dict:
        latest_student_text = ""
        if len(self._conversation) >= 2 and self._conversation[-2]["role"] == "user":
            latest_student_text = self._conversation[-2]["content"]

        try:
            new_snapshot, digest = build_visible_artifact_digest(
                workspace_path=self._workspace_path,
                previous_snapshot=self._workspace_snapshot,
                latest_student_text=latest_student_text,
                latest_agent_text=latest_agent_text,
                shared_filenames=shared_filenames,
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

    def _build_student_runtime_guidance(
        self,
        latest_agent_text: str,
        artifact_digest: Optional[dict] = None,
    ) -> str:
        """Build steering notes for conversation pacing only.

        Artifact visibility signals (B1-B4) are intentionally excluded.
        The student should not be coached to request code/output — if the
        agent fails to share artifacts, that is the agent's fault and
        should be penalised in evaluation, not compensated at runtime.
        Artifact digest data is still recorded in ``_artifact_debug_history``
        for the evaluation pipeline to consume.
        """
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

        if not signals:
            return ""

        return "\n".join(
            [
                "Guide the student's next reply with these hidden steering notes:",
                *signals,
            ]
        )

    def _safe_closing(self) -> str:
        """Select a pre-written student closing message."""
        return self._student_sim.generate_closing(self._conversation)

    def inject_student_opening(self, opening: str) -> None:
        """Inject the student opening into conversation without start_session.

        Used by the reference harness when the opening is already in the
        agent's bootstrap prompt.
        """
        if not self._session_info_called and not self._conversation:
            self._conversation.append(
                {"role": "user", "content": opening, "ts": time.time()}
            )
            self._session_info_called = True

    def _get_student_opening(self) -> str:
        """Get persona-specific opening message for this task.

        Openings are stored on the *task* as ``student_openings: dict[persona_id, str]``.
        """
        openings = getattr(self._task, "student_openings", {}) or {}
        persona_id = getattr(self._persona, "persona_id", "")
        opening = openings.get(persona_id, "")
        return opening or "Hi, I need help with this topic."
