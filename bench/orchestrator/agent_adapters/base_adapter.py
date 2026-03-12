"""Base adapter for agent-under-test integration.

Defines the interface that all agent adapters must implement.
The model_callback signature matches DeepEval ConversationSimulator requirements.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class TokenRecord:
    """A single API call's token usage."""

    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0


class BaseAgentAdapter(ABC):
    """Base class for agent adapters.

    Adapters wrap an Agent Under Test to work with the benchmark's
    conversation loop. The primary method is `generate_response` which
    takes the conversation history and available tools, and returns
    the agent's response.
    """

    def __init__(self, agent_name: str = "unknown"):
        self.agent_name = agent_name
        self._token_records: list[TokenRecord] = []

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

    def get_token_records(self) -> list[TokenRecord]:
        """Return accumulated token usage records for cost tracking."""
        return list(self._token_records)

    def set_agent_max_steps(self, n: int):
        """Set the max tool-call steps per conversation turn.

        Adapters with internal agentic loops (OpenAI SDK, Claude SDK) override
        this to limit how many LLM→tool→LLM cycles happen within a single
        generate_response() call. Adapters without agentic loops ignore this.
        """
        pass

    def reset(self):
        """Reset any internal state between tasks."""
        self._token_records.clear()

    def close(self):
        """Release any external resources held by the adapter."""
        pass
