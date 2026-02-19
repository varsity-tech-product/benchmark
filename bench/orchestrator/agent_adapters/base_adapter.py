"""Base adapter for agent-under-test integration.

Defines the interface that all agent adapters must implement.
The model_callback signature matches DeepEval ConversationSimulator requirements.
"""

from abc import ABC, abstractmethod
from typing import Optional


class BaseAgentAdapter(ABC):
    """Base class for agent adapters.

    Adapters wrap an Agent Under Test to work with the benchmark's
    conversation loop. The primary method is `generate_response` which
    takes the conversation history and available tools, and returns
    the agent's response.
    """

    def __init__(self, agent_name: str = "unknown"):
        self.agent_name = agent_name

    @abstractmethod
    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate a response given conversation history and tools.

        Args:
            messages: List of {role, content} dicts (conversation history).
            available_tools: List of tool schema dicts available to the agent.
            tool_callback: Callable to execute tool calls through the proxy.
                          Signature: tool_callback(tool_name, **kwargs) -> str

        Returns:
            The agent's text response to the student.
        """
        pass

    def reset(self):
        """Reset any internal state between tasks."""
        pass
