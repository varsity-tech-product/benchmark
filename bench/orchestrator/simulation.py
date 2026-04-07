"""ConversationSimulator configuration for QuantTutorBench.

Wraps DeepEval's ConversationSimulator to drive multi-turn tutoring dialogues.
The simulator plays the student role using persona definitions from task + persona JSON.

Reference: https://github.com/confident-ai/deepeval
DeepEval API:
    from deepeval.simulator import ConversationSimulator
    from deepeval.test_case import ConversationalGolden, Turn
"""

import json
import logging
import re
import time
from typing import Callable, Optional

logger = logging.getLogger(__name__)


class _SessionTimeoutError(Exception):
    """Raised by model_callback to force-stop the conversation loop.

    DeepEval's ConversationSimulator does not catch model_callback exceptions,
    so this propagates up through simulate() and terminates the loop.
    The accumulated turns are carried so the caller can reconstruct a test case.
    """

    def __init__(self, turns: list):
        self.turns = turns
        super().__init__("Session time limit reached")


class _CancelledError(Exception):
    """Raised by model_callback when the user cancels a run via the web UI.

    Like _SessionTimeoutError, this propagates through simulate() and
    terminates the loop. Accumulated turns are preserved.
    """

    def __init__(self, turns: list):
        self.turns = turns
        super().__init__("Run cancelled by user")


# Maximum retries when simulator.simulate() returns 0 turns despite the
# agent having consumed tokens.  Each retry re-creates the simulator
# instance to reset DeepEval's internal state.
_MAX_SIMULATION_RETRIES = 2

# HTTP status codes that will never resolve on retry (payment, auth, forbidden).
_NON_RETRYABLE_STATUSES = {401, 402, 403}

# Categories that use the incremental TC checker (must have numbered TC items).
_INCREMENTAL_CHECKER_CATEGORIES = {"strategy", "backtest", "implementation", "debug"}


def _resolve_checker_model(model_name: str):
    """Resolve TC checker model — skip OAuth for reliable JSON calls."""
    from config.model_resolver import resolve_deepeval_model

    return resolve_deepeval_model(model_name, skip_oauth=True)


def _is_non_retryable(exc: Exception) -> bool:
    """Return True for errors that cannot possibly succeed on retry.

    Covers payment failures (402 Insufficient credits), auth errors (401),
    forbidden (403), and Rich LiveError (concurrent display conflict).
    Checks both the status_code attribute (Anthropic / OpenAI SDK exceptions)
    and common keywords in the message string.
    """
    status = getattr(exc, "status_code", 0) or 0
    if status in _NON_RETRYABLE_STATUSES:
        return True
    # Rich LiveError: "Only one live display may be active at once"
    # Retrying is futile while another Live instance is running.
    if type(exc).__name__ == "LiveError":
        return True
    msg = str(exc).lower()
    return any(
        kw in msg
        for kw in (
            "insufficient credits",
            "credit balance",
            "payment required",
            "only one live display",
        )
    )


try:
    from deepeval.dataset import ConversationalGolden
    from deepeval.simulator import ConversationSimulator
    from deepeval.test_case import ConversationalTestCase, Turn

    DEEPEVAL_AVAILABLE = True
except ImportError:
    DEEPEVAL_AVAILABLE = False

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.llm_config import SIMULATOR_DEFAULT_MODEL, TC_CHECKER_MODEL
from config.model_resolver import resolve_deepeval_model
from config.prompt_config import build_scenario, build_user_description

from orchestrator.live_monitor import emit
from orchestrator.schemas import QuantTutorTask, StudentPersona

# ---------------------------------------------------------------------------
# TC parsing
# ---------------------------------------------------------------------------


def _parse_tc_items(
    task: QuantTutorTask, persona_id: Optional[str] = None
) -> Optional[list[str]]:
    """Parse numbered TC items from termination_criteria text.

    Only applies to categories in _INCREMENTAL_CHECKER_CATEGORIES.
    Returns None if the task doesn't qualify or TC can't be parsed.

    Supports two formats:
      - String: "... (1) first item (2) second item ... Once all N steps ..."
      - Dict:   {"persona_id": "... (1) ... (2) ...", ...}
        When persona_id is provided, the matching entry is used.

    Expected format: "... (1) first item (2) second item ... Once all N steps ..."
    """
    if task.category.value not in _INCREMENTAL_CHECKER_CATEGORIES:
        return None
    tc = getattr(task.ground_truth, "termination_criteria", None)
    if not tc:
        return None
    # Support per-persona TC (dict keyed by persona_id)
    if isinstance(tc, dict):
        if persona_id and persona_id in tc:
            tc = tc[persona_id]
        else:
            # Fallback: use the first available entry
            tc = next(iter(tc.values()), None)
            if not tc:
                return None
    if not isinstance(tc, str):
        return None
    # Negative lookbehind (?<![A-Za-z]) prevents matching indicator
    # notation like SMA(20), RSI(14), EMA(10) as TC item delimiters.
    items = re.findall(
        r"(?<![A-Za-z])\(\d+\)\s*(.+?)(?=(?<![A-Za-z])\(\d+\)|Once all|$)",
        tc,
        re.DOTALL,
    )
    items = [item.strip().rstrip(".") for item in items if item.strip()]
    if len(items) < 2:
        return None  # Can't parse numbered items → fall back to native
    return items


# ---------------------------------------------------------------------------
# EfficientSimulator — incremental TC checker
# ---------------------------------------------------------------------------


class _EfficientSimulator(ConversationSimulator):
    """ConversationSimulator with incremental TC coverage tracking.

    Instead of sending the full conversation to the checker LLM every turn,
    this maintains a persistent TC coverage bitmap and only sends the latest
    exchange (1 student + 1 tutor message) plus the coverage state.

    Token savings: ~97% vs native checker (fixed ~1400 tokens/check vs O(n)).
    DeepEvalError risk: eliminated (custom JSON parsing with graceful fallback).
    """

    def __init__(
        self,
        tc_items: list[str],
        golden: "ConversationalGolden",
        checker_model=None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self._tc_items = tc_items
        self._covered = [False] * len(tc_items)
        # Check from the very first exchange to avoid blind spots.
        self._min_check_exchange = 1
        self._last_check_exchange = 0
        self._golden = golden
        # Separate model for TC checking (defaults to simulator_model if None)
        self._checker_model = checker_model or self.simulator_model

    def stop_conversation(self, turns, golden, progress=None, pbar_turns_id=None):
        """Incremental TC checker — replaces DeepEval's full-history checker."""
        # Count exchanges (1 exchange = 1 student msg + 1 tutor response)
        n_exchanges = len([t for t in turns if t.role == "user"])

        # Gate 1: skip early exchanges
        if n_exchanges < self._min_check_exchange:
            return False

        # Gate 2: check every exchange (no skip-turn optimisation)
        if n_exchanges <= self._last_check_exchange:
            return False
        self._last_check_exchange = n_exchanges

        # Gate 3: incremental LLM check (last 1 exchange only)
        newly = self._incremental_check(turns)
        for idx in newly:
            self._covered[idx] = True

        if all(self._covered):
            logger.info(
                "TC fully covered after %d exchanges. Injecting closing.",
                n_exchanges,
            )
            closing = self._generate_closing(turns)
            turns.append(Turn(role="user", content=closing))
            try:
                from orchestrator.live_monitor import emit

                turn_idx = len([t for t in turns if t.role == "user"]) - 1
                emit("student_message", {"content": closing, "turn_index": turn_idx})
            except Exception:
                pass
            return True

        return False

    _TC_TRUNC = 3000  # per-message truncation limit for checker

    # Regex for fenced code blocks
    _CODE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

    def _incremental_check(self, turns) -> list[int]:
        """Check if the latest exchange newly covers any uncovered TC items.

        Three-pass strategy for long messages:
          1. Head (first _TC_TRUNC chars)
          2. Tail (last _TC_TRUNC chars) — skipped if head suffices
          3. Code blocks — only when uncovered TC items mention "code" and
             head+tail missed them.  Extracts fenced code blocks with
             surrounding context so the checker can evaluate code-related
             criteria regardless of where the code appears in the message.
        """
        recent = turns[-2:] if len(turns) >= 2 else turns
        needs_tail = any(len(t.content) > self._TC_TRUNC for t in recent)

        # --- Pass 1: head (first _TC_TRUNC chars) ---
        head_json = json.dumps(
            [{"role": t.role, "content": t.content[: self._TC_TRUNC]} for t in recent],
            ensure_ascii=False,
        )
        newly = self._call_checker(head_json)

        # Short-circuit: if head already covers all remaining, skip tail
        if not needs_tail or self._all_covered_after(newly):
            return newly

        # --- Pass 2: tail (last _TC_TRUNC chars) for long messages ---
        tail_json = json.dumps(
            [
                {
                    "role": t.role,
                    "content": (
                        t.content[-self._TC_TRUNC :]
                        if len(t.content) > self._TC_TRUNC
                        else t.content
                    ),
                }
                for t in recent
            ],
            ensure_ascii=False,
        )
        newly_tail = self._call_checker(tail_json)
        # Merge (deduplicate)
        seen = set(newly)
        for idx in newly_tail:
            if idx not in seen:
                newly.append(idx)
                seen.add(idx)

        if self._all_covered_after(newly):
            return newly

        # --- Pass 3: code-focused check ---
        # Only when uncovered TC items mention "code" and messages contain
        # fenced code blocks that may have fallen in the head/tail blind zone.
        has_code_tc = any(
            "code" in self._tc_items[i].lower()
            for i, c in enumerate(self._covered)
            if not c and i not in seen
        )
        if not has_code_tc:
            return newly

        code_snippets = []
        for t in recent:
            if t.role != "assistant":
                continue
            for m in self._CODE_BLOCK_RE.finditer(t.content):
                start = max(0, m.start() - 200)
                end = min(len(t.content), m.end() + 200)
                code_snippets.append(t.content[start:end])

        if not code_snippets:
            return newly

        code_content = "\n---\n".join(code_snippets)[: self._TC_TRUNC]
        code_json = json.dumps(
            [{"role": "assistant", "content": code_content}],
            ensure_ascii=False,
        )
        newly_code = self._call_checker(code_json)
        for idx in newly_code:
            if idx not in seen:
                newly.append(idx)
                seen.add(idx)

        return newly

    def _all_covered_after(self, newly: list[int]) -> bool:
        """Check if applying *newly* would cover all TC items."""
        for i, c in enumerate(self._covered):
            if not c and i not in newly:
                return False
        return True

    def _call_checker(self, exchange_json: str) -> list[int]:
        """Single checker LLM call. Returns list of newly-covered indices (0-based)."""
        uncovered_lines = []
        covered_lines = []
        for i, tc in enumerate(self._tc_items):
            if self._covered[i]:
                covered_lines.append(f"  {i + 1}. [COVERED] {tc}")
            else:
                uncovered_lines.append(f"  {i + 1}. [NOT COVERED] {tc}")

        prompt = (
            "You are tracking a tutoring session's progress against "
            "specific learning objectives.\n\n"
            "Current status:\n" + "\n".join(covered_lines + uncovered_lines) + "\n\n"
            "Latest exchange:\n" + exchange_json + "\n\n"
            "Which NOT-YET-COVERED items (if any) were demonstrated with "
            "computational evidence (actual numbers, code execution, or "
            "concrete analysis) in this exchange? "
            'Return ONLY a JSON object: {"newly_covered": [1, 3]} '
            'or {"newly_covered": []} if none were covered.'
        )

        try:
            result = self._checker_model.generate(prompt)
            text = result[0] if isinstance(result, tuple) else result
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return []
            data = json.loads(match.group())
            indices = data.get("newly_covered", [])
            return [
                i - 1
                for i in indices
                if isinstance(i, int)
                and 1 <= i <= len(self._tc_items)
                and not self._covered[i - 1]
            ]
        except Exception as exc:
            logger.debug("Incremental TC check failed: %s", exc)
            return []

    def _generate_closing(self, turns) -> str:
        """Generate a natural student closing message."""
        recent = turns[-4:] if len(turns) >= 4 else turns
        recent_text = "\n".join(
            f"[{'Student' if t.role == 'user' else 'Tutor'}]: {t.content[:300]}"
            for t in recent
        )
        prompt = (
            "You are the student in this tutoring session. The tutor has "
            "successfully covered all your learning goals with real "
            "computations and results. The session is now ending.\n\n"
            "Write a brief FINAL closing message (1-3 sentences):\n"
            "- Thank the tutor for the session\n"
            "- Mention one specific thing you learned\n"
            "- Say goodbye\n\n"
            "IMPORTANT: Do NOT ask any new questions, request more analysis, "
            "or suggest next steps that require the tutor's help. "
            "This is a goodbye message — the session is over.\n\n"
            f"Your profile: {self._golden.user_description}\n\n"
            f"Recent conversation:\n{recent_text}\n\n"
            "Reply with ONLY the closing message, no JSON."
        )
        try:
            result = self.simulator_model.generate(prompt)
            text = result[0] if isinstance(result, tuple) else result
            if text and text.strip():
                return text.strip()
        except Exception as exc:
            logger.debug("Failed to generate closing: %s", exc)
        return (
            "Thanks for walking me through all of this — "
            "I have a much clearer picture now. "
            "I'll try applying these techniques to my own data."
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_content(content) -> str:
    """Ensure content is a plain string for DeepEval Turn."""
    from orchestrator.agent_adapters.base_adapter import ensure_str

    return ensure_str(content)


def build_conversational_golden(
    task: QuantTutorTask,
    persona: StudentPersona,
) -> "ConversationalGolden":
    """Build a DeepEval ConversationalGolden from task + persona.

    For tasks using the incremental TC checker (strategy category),
    expected_outcome is set to None to disable DeepEval's native checker.
    For all other tasks, the native checker is preserved.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    tc_items = _parse_tc_items(task, persona_id=persona.persona_id)

    if tc_items is not None:
        # Incremental checker will handle termination — disable native
        stop_outcome = None
    else:
        # Native checker — use TC or EO as before
        if task.ground_truth.termination_criteria:
            if task.category.value in ("implementation", "end_to_end", "debug"):
                stop_outcome = (
                    f"{task.ground_truth.expected_outcome}\n\n"
                    f"Observable completion criteria:\n"
                    f"{task.ground_truth.termination_criteria}"
                )
            else:
                stop_outcome = task.ground_truth.termination_criteria
        else:
            stop_outcome = task.ground_truth.expected_outcome

    return ConversationalGolden(
        scenario=build_scenario(
            task,
            persona.persona_id,
            has_incremental_tc=(tc_items is not None),
        ),
        expected_outcome=stop_outcome,
        user_description=build_user_description(
            persona,
            has_incremental_tc=(tc_items is not None),
        ),
    )


def create_model_callback(
    agent_adapter,
    proxy,
    task: QuantTutorTask,
    tools_enabled: bool = True,
    deadline: Optional[float] = None,
    cancel_event=None,
) -> Callable:
    """Create the model_callback function for ConversationSimulator.

    DeepEval's ConversationSimulator expects Callable[[str], str]:
    it passes the student's message as a string and expects the agent's
    response as a string. The simulator tracks turn history internally.

    Args:
        agent_adapter: The agent adapter (implements BaseAgentAdapter).
        proxy: The MCPProxy instance for tool call logging.
        task: The task definition.
        tools_enabled: If False, no tools are passed to the agent (pure LLM conditions).

    Returns:
        Callback function compatible with ConversationSimulator.
    """
    conversation_history: list[dict] = []
    timeout_count: int = 0
    _last_response: str = ""
    _repeat_count: int = 0
    _MAX_REPEATS: int = 2  # Force-stop after 2 consecutive identical responses

    def model_callback(input: str, **kwargs):
        """Route student message to agent, return response as Turn.

        DeepEval v3.8+ passes keyword args: input, turns, thread_id.
        We match the 'input' parameter name so DeepEval can introspect it.
        Returns a Turn object (role='assistant') as expected by ConversationSimulator.
        """
        nonlocal timeout_count

        # Cancel check: abort between turns when user stops the run.
        if cancel_event is not None and cancel_event.is_set():
            raise _CancelledError(
                turns=[
                    Turn(role=m["role"], content=m["content"])
                    for m in conversation_history
                ]
            )

        # Timeout check: if wall-clock deadline exceeded, return a
        # graceful summary instead of calling the agent.
        if deadline is not None and time.time() > deadline:
            timeout_count += 1
            if timeout_count == 1:
                # First timeout: return a meaningful wrap-up so
                # stop_conversation() is likely to judge completion.
                topic_count = len(
                    [m for m in conversation_history if m["role"] == "user"]
                )
                timeout_msg = (
                    "We've reached the end of our session time. "
                    "Let me wrap up with a summary of what we accomplished:\n\n"
                    f"Over {topic_count} exchanges, I guided you through the task. "
                    "The key files and results we produced are saved in your "
                    "workspace — you can review them with file_read() or by "
                    "checking /workspace/results/.\n\n"
                    "This concludes our tutoring session. "
                    "All learning objectives for this session have been addressed."
                )
                conversation_history.append({"role": "user", "content": input})
                conversation_history.append(
                    {"role": "assistant", "content": timeout_msg}
                )
                return Turn(role="assistant", content=timeout_msg)
            else:
                # Second+ timeout: stop_conversation() didn't end the loop.
                # Force-stop by raising — DeepEval does NOT catch
                # model_callback exceptions, so this terminates simulate().
                conversation_history.append({"role": "user", "content": input})
                raise _SessionTimeoutError(
                    turns=[
                        Turn(role=m["role"], content=m["content"])
                        for m in conversation_history
                    ]
                )

        conversation_history.append({"role": "user", "content": input})
        turn_idx = len([m for m in conversation_history if m["role"] == "user"]) - 1
        emit("student_message", {"content": input, "turn_index": turn_idx})

        proxy.set_turn(turn_idx)

        if tools_enabled:
            available_tools = proxy.get_available_tools()

            def tool_callback(name, **kw):
                return proxy.call_tool(name, **kw)

        else:
            available_tools = []
            tool_callback = None

        try:
            response = agent_adapter.generate_response(
                messages=list(conversation_history),
                available_tools=available_tools,
                tool_callback=tool_callback,
            )
        except Exception as exc:
            # If cancelled during generate_response, exit immediately
            if cancel_event is not None and cancel_event.is_set():
                raise _CancelledError(
                    turns=[
                        Turn(role=m["role"], content=m["content"])
                        for m in conversation_history
                    ]
                )
            logger.error(
                "model_callback: generate_response raised %s: %s",
                type(exc).__name__,
                exc,
            )
            response = "Let me continue our discussion."

        # Post-turn cancel check: if tools were rejected during this turn,
        # exit before the simulator generates the next student message.
        if cancel_event is not None and cancel_event.is_set():
            conversation_history.append(
                {"role": "assistant", "content": response or ""}
            )
            raise _CancelledError(
                turns=[
                    Turn(role=m["role"], content=m["content"])
                    for m in conversation_history
                ]
            )

        response = _normalize_content(response)

        # Repeat detection: if the agent returns the same response
        # consecutively, it's stuck in a degenerate loop.  Force-stop
        # to avoid wasting turns and cost.
        nonlocal _last_response, _repeat_count
        if response and response == _last_response:
            _repeat_count += 1
            if _repeat_count >= _MAX_REPEATS:
                logger.warning(
                    "model_callback: agent repeated identical response %d times, "
                    "force-stopping.",
                    _repeat_count + 1,
                )
                conversation_history.append({"role": "assistant", "content": response})
                raise _SessionTimeoutError(
                    turns=[
                        Turn(role=m["role"], content=m["content"])
                        for m in conversation_history
                    ]
                )
        else:
            _repeat_count = 0
        _last_response = response

        conversation_history.append({"role": "assistant", "content": response})

        # Attach content_blocks (thinking/tool_use/tool_result/text) for
        # live web UI inline rendering.  Only Anthropic adapter provides
        # these; others return None and the frontend falls back to plain text.
        turn_blocks = agent_adapter.get_last_content_blocks()
        event_data: dict = {"content": response, "turn_index": turn_idx}
        if turn_blocks:
            event_data["content_blocks"] = turn_blocks
        emit("tutor_response", event_data)

        return Turn(role="assistant", content=response)

    return model_callback


def _append_student_closing(test_case, resolved_model, golden) -> None:
    """Append a natural student closing message if conversation ends on tutor turn.

    DeepEval's stop_conversation() checks *before* generating the next student
    message, so conversations always end with the tutor's reply.  This adds a
    brief student wrap-up for a more natural ending.

    Skipped if the conversation already ends on a student message (e.g. when
    the incremental TC checker injected a closing via stop_conversation).
    """
    if not test_case.turns or test_case.turns[-1].role != "assistant":
        return
    try:
        prompt = (
            "You are the student in the conversation below. The tutor just "
            "finished answering your last question. Write a brief closing "
            "message (1-2 sentences) that thanks the tutor and mentions one "
            "specific thing you learned or plan to try. Stay in character.\n\n"
            f"Scenario: {golden.scenario[:400]}\n\n"
            "Reply with ONLY the closing message."
        )
        result = resolved_model.generate(prompt)
        # generate() returns (text, cost) tuple or plain string
        text = result[0] if isinstance(result, tuple) else result
        if text and text.strip():
            test_case.turns.append(Turn(role="user", content=text.strip()))
            try:
                from orchestrator.live_monitor import emit

                turn_idx = len([t for t in test_case.turns if t.role == "user"]) - 1
                emit(
                    "student_message", {"content": text.strip(), "turn_index": turn_idx}
                )
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Failed to generate closing message: %s", exc)


def run_conversation_simulation(
    task: QuantTutorTask,
    persona: StudentPersona,
    agent_adapter,
    proxy,
    simulator_model: str = SIMULATOR_DEFAULT_MODEL,
    max_turns: Optional[int] = None,
    tools_enabled: bool = True,
    timeout_minutes: Optional[int] = None,
    cancel_event=None,
) -> tuple["ConversationalTestCase", Optional[float]]:
    """Run a full conversation simulation using DeepEval ConversationSimulator.

    Args:
        task: The benchmark task.
        persona: The student persona.
        agent_adapter: The agent adapter.
        proxy: The MCPProxy instance.
        simulator_model: LLM model for student simulation.
        max_turns: Maximum conversation turns (exchanges, not messages).
        tools_enabled: If False, no tools are passed to the agent.

    Returns:
        Tuple of (ConversationalTestCase, simulator_cost).
        simulator_cost is the accumulated USD cost of student message generation,
        or None if cost tracking is unavailable.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    max_turns = max_turns or task.max_turns
    resolved_model = resolve_deepeval_model(simulator_model)

    # Compute wall-clock deadline for timeout enforcement.
    # timeout_minutes flows from CLI --max-minutes or task JSON timeout_minutes.
    deadline: Optional[float] = None
    effective_timeout = timeout_minutes or task.timeout_minutes
    if effective_timeout and effective_timeout > 0:
        deadline = time.time() + effective_timeout * 60

    # Propagate deadline to the proxy so that tool calls arriving after
    # the deadline are rejected immediately (prevents long-running tools
    # like run_backtest from starting when the session should be ending).
    proxy.set_deadline(deadline)

    # Propagate cancel_event to proxy and agent adapter so both tool calls
    # and the runner loop exit immediately when the user cancels.
    if cancel_event is not None:
        proxy.set_cancel_event(cancel_event)
        if hasattr(agent_adapter, "set_cancel_event"):
            agent_adapter.set_cancel_event(cancel_event)

    # Build golden
    golden = build_conversational_golden(task, persona)

    # Parse TC items for incremental checker
    tc_items = _parse_tc_items(task, persona_id=persona.persona_id)

    # Create model callback
    callback = create_model_callback(
        agent_adapter,
        proxy,
        task,
        tools_enabled=tools_enabled,
        deadline=deadline,
        cancel_event=cancel_event,
    )

    # Retry loop: DeepEval's simulate() can silently return [] when it
    # catches an internal exception (e.g. student-simulator LLM failure,
    # context overflow from a very long agent response).  We detect this
    # by checking for 0 turns despite the agent having consumed tokens,
    # and retry with a fresh simulator instance.
    simulator_cost: Optional[float] = None

    for attempt in range(1 + _MAX_SIMULATION_RETRIES):
        # Fresh simulator instance per attempt (resets DeepEval internals).
        # The model_callback is reused — its conversation_history accumulates
        # across attempts, but DeepEval tracks turns independently.
        if tc_items is not None:
            resolved_checker = _resolve_checker_model(TC_CHECKER_MODEL)
            simulator = _EfficientSimulator(
                tc_items=tc_items,
                golden=golden,
                checker_model=resolved_checker,
                model_callback=callback,
                simulator_model=resolved_model,
                async_mode=False,
            )
        else:
            simulator = ConversationSimulator(
                model_callback=callback,
                simulator_model=resolved_model,
                async_mode=False,
            )

        try:
            test_cases = simulator.simulate(
                conversational_goldens=[golden],
                max_user_simulations=max_turns,
            )
        except _CancelledError as cancel_exc:
            # User cancellation — do NOT retry.
            logger.info(
                "Session cancelled by user (%d turns collected).",
                len(cancel_exc.turns),
            )
            simulator_cost = getattr(simulator, "simulation_cost", None)
            tc = ConversationalTestCase(turns=cancel_exc.turns)
            return tc, simulator_cost
        except _SessionTimeoutError as timeout_exc:
            # Intentional force-stop — do NOT retry.
            logger.info(
                "Session force-stopped after timeout (%d turns collected).",
                len(timeout_exc.turns),
            )
            simulator_cost = getattr(simulator, "simulation_cost", None)
            tc = ConversationalTestCase(turns=timeout_exc.turns)
            _append_student_closing(tc, resolved_model, golden)
            return tc, simulator_cost
        except Exception as exc:
            logger.warning(
                "[Attempt %d/%d] simulator.simulate() raised %s: %s",
                attempt + 1,
                1 + _MAX_SIMULATION_RETRIES,
                type(exc).__name__,
                exc,
            )
            test_cases = None

            # Payment / auth errors will never resolve on retry — abort
            # immediately to avoid burning tokens on identical failures.
            if _is_non_retryable(exc):
                logger.error(
                    "Non-retryable error (status %s). Skipping remaining retries.",
                    getattr(exc, "status_code", "?"),
                )
                break

        simulator_cost = getattr(simulator, "simulation_cost", None)

        # Success: simulate() returned at least one test case with turns.
        if test_cases and test_cases[0].turns:
            if attempt > 0:
                logger.info(
                    "simulate() succeeded on attempt %d with %d turns.",
                    attempt + 1,
                    len(test_cases[0].turns),
                )
            _append_student_closing(test_cases[0], resolved_model, golden)
            return test_cases[0], simulator_cost

        # Detect token consumption to distinguish "agent worked but
        # DeepEval failed" from "nothing happened at all".
        token_info = ""
        if hasattr(agent_adapter, "get_token_records"):
            records = agent_adapter.get_token_records()
            total_in = sum(getattr(r, "input_tokens", 0) for r in records)
            total_out = sum(getattr(r, "output_tokens", 0) for r in records)
            if total_in > 0 or total_out > 0:
                token_info = f" (agent consumed {total_in} in / {total_out} out tokens)"

        retries_left = _MAX_SIMULATION_RETRIES - attempt
        if retries_left > 0:
            logger.warning(
                "[Attempt %d/%d] simulate() returned 0 turns%s. "
                "Retrying (%d retries left)...",
                attempt + 1,
                1 + _MAX_SIMULATION_RETRIES,
                token_info,
                retries_left,
            )
        else:
            logger.error(
                "simulate() returned 0 turns after %d attempt(s)%s. "
                "Returning empty conversation.",
                1 + _MAX_SIMULATION_RETRIES,
                token_info,
            )

    # All attempts exhausted — raise so the caller reports an error
    # instead of silently treating 0 turns as success.
    raise RuntimeError(
        f"Simulation failed after {1 + _MAX_SIMULATION_RETRIES} attempts: "
        f"0 conversation turns produced"
    )
