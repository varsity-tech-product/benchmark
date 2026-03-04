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

    return ConversationalGolden(
        scenario=build_scenario(task, persona.persona_id),
        expected_outcome=task.ground_truth.expected_outcome,
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


def run_conversation_manual(
    task: QuantTutorTask,
    persona: StudentPersona,
    agent_adapter,
    proxy,
    max_turns: Optional[int] = None,
    tools_enabled: bool = True,
) -> list[dict]:
    """Run a conversation using the built-in simple student simulator.

    This is the fallback when DeepEval is not available or for quick testing.

    Args:
        task: The benchmark task.
        persona: The student persona.
        agent_adapter: The agent adapter.
        proxy: The MCPProxy instance.
        max_turns: Maximum conversation turns.
        tools_enabled: If False, no tools are passed to the agent.

    Returns:
        List of conversation turn dicts.
    """
    max_turns = max_turns or task.max_turns
    opening = task.student_openings.get(persona.persona_id, "Hello, I need help.")

    conversation = [{"role": "user", "content": opening}]

    for turn_idx in range(max_turns):
        proxy.set_turn(turn_idx)

        if tools_enabled:
            available_tools = proxy.get_available_tools()

            def tool_callback(name, **kwargs):
                return proxy.call_tool(name, **kwargs)

        else:
            available_tools = []
            tool_callback = None

        agent_response = agent_adapter.generate_response(
            messages=conversation,
            available_tools=available_tools,
            tool_callback=tool_callback,
        )
        conversation.append({"role": "assistant", "content": agent_response})

        # Check termination
        if _should_terminate(conversation, max_turns, turn_idx):
            break

        # Simple student simulation
        student_msg = _simulate_student(conversation, persona, turn_idx)
        conversation.append({"role": "user", "content": student_msg})

    return conversation


def _should_terminate(conversation: list[dict], max_turns: int, turn_idx: int) -> bool:
    """Check if the conversation should end."""
    if turn_idx >= max_turns - 1:
        return True
    if conversation and conversation[-1]["role"] == "assistant":
        last = conversation[-1]["content"].lower()
        if any(
            phrase in last
            for phrase in [
                "let me know if you have any other questions",
                "feel free to ask",
                "is there anything else",
                "good luck with your",
            ]
        ):
            return True
    return False


def _simulate_student(
    conversation: list[dict], persona: StudentPersona, turn_idx: int
) -> str:
    """Simple student simulation fallback."""
    user_turns = sum(1 for m in conversation if m["role"] == "user")

    if user_turns <= 1:
        if persona.knowledge_level == "beginner":
            return "That makes sense! Can you show me how to do this step by step?"
        elif persona.knowledge_level == "intermediate":
            return "Got it. Can you show me the implementation?"
        else:
            return "Interesting approach. What about edge cases?"
    elif user_turns <= 3:
        if persona.knowledge_level == "beginner":
            return "I'm not sure I understand the math part. Can you explain it more simply?"
        elif persona.knowledge_level == "intermediate":
            return "The code looks good. How do I interpret these results?"
        else:
            return "How does this perform in different market regimes?"
    else:
        return "Thank you, this has been very helpful! I think I understand now."
