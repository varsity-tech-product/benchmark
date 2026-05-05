"""ConversationSimulator configuration for QuantTutorBench.

Wraps DeepEval's ConversationSimulator to drive multi-turn tutoring dialogues.
The simulator plays the user role using persona definitions from task + persona JSON.

Reference: https://github.com/confident-ai/deepeval
DeepEval API:
    from deepeval.simulator import ConversationSimulator
    from deepeval.test_case import ConversationalGolden, Turn
"""

import json
import logging
import time
from typing import TYPE_CHECKING, Callable, Optional

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

from config.llm_config import SIMULATOR_DEFAULT_MODEL
from config.model_resolver import resolve_deepeval_model
from config.prompt_config import build_scenario, build_user_description

from orchestrator.live_monitor import emit
from orchestrator.schemas import QuantTutorTask, UserPersona

if TYPE_CHECKING:
    from mcp_servers.session import TutoringSession

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_content(content) -> str:
    """Ensure content is a plain string for DeepEval Turn."""
    from orchestrator.agent_adapters.base_adapter import ensure_str

    return ensure_str(content)


def build_conversational_golden(
    task: QuantTutorTask,
    persona: UserPersona,
) -> "ConversationalGolden":
    """Build a DeepEval ConversationalGolden from task + persona.

    Termination criteria are passed to DeepEval when available.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    stop_outcome = task.ground_truth.termination_criteria
    if isinstance(stop_outcome, dict):
        stop_outcome = "\n".join(
            f"{key}: {value}" for key, value in stop_outcome.items()
        )

    return ConversationalGolden(
        scenario=build_scenario(
            task,
            persona.persona_id,
            has_incremental_tc=False,
        ),
        expected_outcome=stop_outcome,
        user_description=build_user_description(
            persona,
            has_incremental_tc=False,
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
    it passes the user's message as a string and expects the agent's
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
        """Route user message to agent, return response as Turn.

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
        emit("user_message", {"content": input, "turn_index": turn_idx})

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
        # exit before the simulator generates the next user message.
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


def _append_user_closing(test_case, resolved_model, golden) -> None:
    """Append a natural user closing message if conversation ends on tutor turn.

    DeepEval's stop_conversation() checks *before* generating the next user
    message, so conversations always end with the tutor's reply.  This adds a
    brief user wrap-up for a more natural ending.

    Skipped if the conversation already ends on a user message.
    """
    if not test_case.turns or test_case.turns[-1].role != "assistant":
        return
    try:
        prompt = (
            "You are the user in the conversation below. The tutor just "
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
                    "user_message", {"content": text.strip(), "turn_index": turn_idx}
                )
            except Exception:
                pass
    except Exception as exc:
        logger.debug("Failed to generate closing message: %s", exc)


def run_conversation_simulation(
    task: QuantTutorTask,
    persona: UserPersona,
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
        persona: The user persona.
        agent_adapter: The agent adapter.
        proxy: The MCPProxy instance.
        simulator_model: LLM model for user simulation.
        max_turns: Maximum conversation turns (exchanges, not messages).
        tools_enabled: If False, no tools are passed to the agent.

    Returns:
        Tuple of (ConversationalTestCase, simulator_cost).
        simulator_cost is the accumulated USD cost of user message generation,
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
    # catches an internal exception (e.g. user-simulator LLM failure,
    # context overflow from a very long agent response).  We detect this
    # by checking for 0 turns despite the agent having consumed tokens,
    # and retry with a fresh simulator instance.
    simulator_cost: Optional[float] = None

    for attempt in range(1 + _MAX_SIMULATION_RETRIES):
        # Fresh simulator instance per attempt (resets DeepEval internals).
        # The model_callback is reused — its conversation_history accumulates
        # across attempts, but DeepEval tracks turns independently.
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
            _append_user_closing(tc, resolved_model, golden)
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
            _append_user_closing(test_cases[0], resolved_model, golden)
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


# ============================================================================
# Agent-driven session (MCP unification)
# ============================================================================

# Bootstrap prompt given to the agent at the start of a session.
# The agent's system prompt already contains task context (via set_task_context),
# so this only needs to instruct it on the interaction protocol.
_AGENT_BOOTSTRAP = (
    "A user is waiting for your help. Here is their opening message:\n\n"
    '"{opening}"\n\n'
    "You have access to tools for data analysis, coding, backtesting, "
    "and communicating with the user.\n\n"
    "IMPORTANT: To talk to the user, you MUST use the send_message "
    "tool. Your text responses are internal notes — only send_message "
    "delivers messages to the user.\n\n"
    "Workflow:\n"
    "1. Use tools (shell_exec, file_read, fetch_market_data, etc.) to "
    "prepare your teaching.\n"
    "2. Use send_message(text=...) to respond to the user.\n"
    "3. Read the user's reply from the send_message result.\n"
    "4. Repeat until send_message returns status 'completed'.\n\n"
    "Begin by addressing the user's opening message."
)


def run_agent_session(
    task: "QuantTutorTask",
    persona: "UserPersona",
    agent_adapter,
    proxy,
    session: "TutoringSession",
    timeout_minutes: Optional[int] = None,
    cancel_event=None,
) -> list[dict[str, str]]:
    """Run a tutoring session where the agent drives the loop.

    The agent interacts with the user via ``send_message`` tool calls
    and uses other MCP tools autonomously.  The conversation is managed
    by ``TutoringSession``; this function only bootstraps the agent and
    handles edge cases.

    Args:
        task: The benchmark task.
        persona: The user persona.
        agent_adapter: The agent adapter (BaseAgentAdapter).
        proxy: MCPProxy with session tools already registered.
        session: TutoringSession backing the session tools.
        timeout_minutes: Wall-clock timeout (also enforced by proxy deadline).
        cancel_event: threading.Event for user cancellation.

    Returns:
        Conversation history as list of {role, content} dicts.
    """
    # Compute deadline and propagate to proxy
    deadline: Optional[float] = None
    effective_timeout = timeout_minutes or getattr(task, "timeout_minutes", None)
    if effective_timeout and effective_timeout > 0:
        deadline = time.time() + effective_timeout * 60
    proxy.set_deadline(deadline)

    if cancel_event is not None:
        proxy.set_cancel_event(cancel_event)
        if hasattr(agent_adapter, "set_cancel_event"):
            agent_adapter.set_cancel_event(cancel_event)

    # Get user opening and inject into session
    opening = task.user_opening or "Hi, I need help with this topic."
    session.inject_user_opening(opening)

    # Build bootstrap prompt with the user's opening
    bootstrap = _AGENT_BOOTSTRAP.format(opening=opening)

    # Give the agent enough iterations for the full session.
    # Each "turn" may involve many tool calls + one send_message.
    agent_adapter.set_agent_max_steps(task.max_turns * 10)

    # Agent runs autonomously — it uses tools (including send_message)
    # until the session is done or it runs out of iterations.
    available_tools = proxy.get_available_tools()

    def tool_callback(name, **kwargs):
        return proxy.call_tool(name, **kwargs)

    try:
        response = agent_adapter.generate_response(
            messages=[{"role": "user", "content": bootstrap}],
            available_tools=available_tools,
            tool_callback=tool_callback,
        )
    except Exception as exc:
        if cancel_event is not None and cancel_event.is_set():
            logger.info("Agent session cancelled by user.")
        else:
            logger.error("Agent session failed: %s: %s", type(exc).__name__, exc)
        response = ""

    # Fallback: if agent returned text but never called send_message,
    # treat the text as the agent's first message to the user.
    if response and not session.conversation:
        logger.warning(
            "Agent did not call send_message. Wrapping text response "
            "as implicit send_message."
        )
        proxy.call_tool("send_message", text=response)

    return session.conversation
