"""ConversationSimulator configuration for QuantTutorBench.

Wraps DeepEval's ConversationSimulator to drive multi-turn tutoring dialogues.
The simulator plays the student role using persona definitions from task + persona JSON.

Reference: https://github.com/confident-ai/deepeval
DeepEval API:
    from deepeval.simulator import ConversationSimulator
    from deepeval.test_case import ConversationalGolden, Turn
"""

from typing import Callable, Optional

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

    def model_callback(input: str, **kwargs):
        """Route student message to agent, return response as Turn.

        DeepEval v3.8+ passes keyword args: input, turns, thread_id.
        We match the 'input' parameter name so DeepEval can introspect it.
        Returns a Turn object (role='assistant') as expected by ConversationSimulator.
        """
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

        response = agent_adapter.generate_response(
            messages=list(conversation_history),
            available_tools=available_tools,
            tool_callback=tool_callback,
        )

        response = _normalize_content(response)
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

    # Build golden
    golden = build_conversational_golden(task, persona)

    # Create model callback
    callback = create_model_callback(
        agent_adapter, proxy, task, tools_enabled=tools_enabled
    )

    # Create simulator (async_mode=False because our callback is synchronous)
    simulator = ConversationSimulator(
        model_callback=callback,
        simulator_model=resolved_model,
        async_mode=False,
    )

    # Run simulation
    test_cases = simulator.simulate(
        conversational_goldens=[golden],
        max_user_simulations=max_turns,
    )

    # Extract simulator cost (accumulated by DeepEval when using_native_model=True)
    simulator_cost = getattr(simulator, "simulation_cost", None)

    if test_cases:
        return test_cases[0], simulator_cost

    # Fallback: return empty test case
    return ConversationalTestCase(turns=[]), simulator_cost
