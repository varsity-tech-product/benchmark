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


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def normalize_tool_params(parameters: dict) -> tuple[dict, list[str]]:
    """Convert MCP-style parameter dict to JSON Schema properties + required.

    Args:
        parameters: MCP tool parameter dict
            ``{param_name: {type, description, required?, items?} | str}``

    Returns:
        (properties dict, required list) suitable for JSON Schema.
    """
    properties: dict = {}
    required: list[str] = []
    for param_name, param_info in parameters.items():
        if isinstance(param_info, dict):
            prop: dict = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", param_name),
            }
            if "items" in param_info:
                prop["items"] = param_info["items"]
            properties[param_name] = prop
            if param_info.get("required", False):
                required.append(param_name)
        else:
            properties[param_name] = {
                "type": "string",
                "description": param_name,
            }
    return properties, required


def extract_latest_user_message(messages: list[dict]) -> str | None:
    """Return the content of the most recent user message, or None."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content")
    return None


def record_token_usage(
    records: list["TokenRecord"],
    model: str,
    usage,
    *,
    input_attr: str = "prompt_tokens",
    output_attr: str = "completion_tokens",
    extra_input: int = 0,
    cost_fn=None,
) -> None:
    """Append a TokenRecord extracted from an API usage object.

    Args:
        records: list to append to (typically ``self._token_records``).
        model: model name string.
        usage: response usage object (may be None — no-op in that case).
        input_attr: attribute name for input token count.
        output_attr: attribute name for output token count.
        extra_input: additional input tokens (e.g. cache tokens).
        cost_fn: ``(model, inp, out) -> float``; when *None* cost is 0.
    """
    if not usage:
        return
    inp = (getattr(usage, input_attr, 0) or 0) + extra_input
    out = getattr(usage, output_attr, 0) or 0
    cost = cost_fn(model, inp, out) if cost_fn else 0.0
    records.append(
        TokenRecord(model=model, input_tokens=inp, output_tokens=out, cost_usd=cost)
    )


def ensure_str(content) -> str:
    """Flatten LLM content (str | list[dict] | None) to a plain string."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        ).strip()
    return str(content)


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
        self._task_context: str = ""
        self._cancel_event = None

    def set_cancel_event(self, cancel_event):
        """Set cancel event checked during agent tool loops."""
        self._cancel_event = cancel_event

    def set_task_context(self, context: str):
        """Set per-task dynamic context appended to the system prompt.

        Subclasses that maintain conversation history should override this
        to also clear their history (call super first).
        """
        self._task_context = context

    def _get_full_system_prompt(self) -> str:
        """Return system_prompt with task context appended."""
        base = self.system_prompt
        if self._task_context:
            base += "\n\n" + self._task_context
        return base

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

    def get_thinking_trace(self) -> list[dict]:
        """Return accumulated thinking/COT blocks from the agent loop.

        Override in subclasses that support extended thinking.
        Each entry: {"turn_index": int, "iteration": int, "thinking": str}
        """
        return []

    def get_content_blocks(self) -> dict[int, list[dict]]:
        """Return per-turn content blocks {turn_index: [blocks]}.

        Override in subclasses that capture structured content blocks
        (thinking, tool_use, tool_result, text) per conversation turn.
        """
        return {}

    def get_last_content_blocks(self) -> list[dict] | None:
        """Return content blocks for the most recently completed turn.

        Override in subclasses that capture content blocks.
        Returns None when no blocks are available (non-Anthropic adapters).
        """
        return None

    def reset(self):
        """Reset any internal state between tasks."""
        self._token_records.clear()
        self._task_context = ""

    def close(self):
        """Release any external resources held by the adapter."""
        pass

    @staticmethod
    def format_tools_openai(tools: list[dict]) -> list[dict]:
        """Convert benchmark tool schemas to OpenAI function calling format."""
        formatted = []
        for tool in tools:
            schema = tool.get("input_schema")
            if not isinstance(schema, dict) or schema.get("type") != "object":
                properties, required_list = normalize_tool_params(
                    tool.get("parameters", {})
                )
                schema = {"type": "object", "properties": properties}
                if required_list:
                    schema["required"] = required_list
            formatted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": schema,
                    },
                }
            )
        return formatted
