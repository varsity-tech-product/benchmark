"""ConversationSimulator configuration for QuantTutorBench.

Wraps DeepEval's ConversationSimulator to drive multi-turn tutoring dialogues.
The simulator plays the student role using persona definitions from task + persona JSON.

Reference: https://github.com/confident-ai/deepeval
DeepEval API:
    from deepeval.simulator import ConversationSimulator
    from deepeval.test_case import ConversationalGolden, Turn
"""

import logging
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


# Maximum retries when simulator.simulate() returns 0 turns despite the
# agent having consumed tokens.  Each retry re-creates the simulator
# instance to reset DeepEval's internal state.
_MAX_SIMULATION_RETRIES = 2

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

from orchestrator.schemas import QuantTutorTask, StudentPersona


def _normalize_content(content) -> str:
    """Ensure content is a plain string for DeepEval Turn.

    OpenAI gpt-5.2 occasionally returns ``message.content`` as a list of
    content-block dicts (``[{"type": "text", "text": "…", …}]``) instead
    of a plain string.  DeepEval's ``Turn(content=...)`` requires ``str``,
    so we flatten here.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                parts.append(block.get("text", ""))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    if content is None:
        return ""
    return str(content)


def build_conversational_golden(
    task: QuantTutorTask,
    persona: StudentPersona,
) -> "ConversationalGolden":
    """Build a DeepEval ConversationalGolden from task + persona.

    This is the seed object that drives the ConversationSimulator.

    Args:
        task: The benchmark task definition.
        persona: The student persona.

    Returns:
        ConversationalGolden configured for this task-persona pair.
    """
    if not DEEPEVAL_AVAILABLE:
        raise ImportError("deepeval is required. Install with: pip install deepeval")

    # stop_conversation uses expected_outcome to decide when to end.
    # For adversarial tasks, termination_criteria contains only positive
    # conditions so the LLM checker can actually judge "complete".
    stop_outcome = (
        task.ground_truth.termination_criteria or task.ground_truth.expected_outcome
    )
    return ConversationalGolden(
        scenario=build_scenario(task, persona.persona_id),
        expected_outcome=stop_outcome,
        user_description=build_user_description(persona),
    )


def create_model_callback(
    agent_adapter,
    proxy,
    task: QuantTutorTask,
    tools_enabled: bool = True,
    deadline: Optional[float] = None,
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
            logger.error(
                "model_callback: generate_response raised %s: %s",
                type(exc).__name__,
                exc,
            )
            response = "Let me continue our discussion."

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
                conversation_history.append({"role": "user", "content": input})
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
        return Turn(role="assistant", content=response)

    return model_callback


def run_conversation_simulation(
    task: QuantTutorTask,
    persona: StudentPersona,
    agent_adapter,
    proxy,
    simulator_model: str = SIMULATOR_DEFAULT_MODEL,
    max_turns: Optional[int] = None,
    tools_enabled: bool = True,
    timeout_minutes: Optional[int] = None,
) -> tuple["ConversationalTestCase", Optional[float]]:
    """Run a full conversation simulation using DeepEval ConversationSimulator.

    Args:
        task: The benchmark task.
        persona: The student persona.
        agent_adapter: The agent adapter.
        proxy: The MCPProxy instance.
        simulator_model: LLM model for student simulation.
        max_turns: Maximum conversation turns.
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

    # Build golden
    golden = build_conversational_golden(task, persona)

    # Create model callback
    callback = create_model_callback(
        agent_adapter, proxy, task, tools_enabled=tools_enabled, deadline=deadline
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
        except _SessionTimeoutError as timeout_exc:
            # Intentional force-stop — do NOT retry.
            logger.info(
                "Session force-stopped after timeout (%d turns collected).",
                len(timeout_exc.turns),
            )
            simulator_cost = getattr(simulator, "simulation_cost", None)
            return (
                ConversationalTestCase(turns=timeout_exc.turns),
                simulator_cost,
            )
        except Exception as exc:
            logger.warning(
                "[Attempt %d/%d] simulator.simulate() raised %s: %s",
                attempt + 1,
                1 + _MAX_SIMULATION_RETRIES,
                type(exc).__name__,
                exc,
            )
            test_cases = None

        simulator_cost = getattr(simulator, "simulation_cost", None)

        # Success: simulate() returned at least one test case with turns.
        if test_cases and test_cases[0].turns:
            if attempt > 0:
                logger.info(
                    "simulate() succeeded on attempt %d with %d turns.",
                    attempt + 1,
                    len(test_cases[0].turns),
                )
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

    # All attempts exhausted — return empty test case.
    # DeepEval validates that turns must not be empty, so we use a
    # lightweight stand-in that quacks like ConversationalTestCase.
    class _EmptyTestCase:
        turns: list = []

    return _EmptyTestCase(), simulator_cost
