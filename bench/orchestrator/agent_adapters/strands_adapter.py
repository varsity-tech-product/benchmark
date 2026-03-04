"""AWS Strands Agents adapter for QuantTutorBench.

Uses the Strands Agents SDK which provides a high-level Agent abstraction
with tool calling managed by the SDK's autonomous loop.

Supports multiple model backends:
  - Amazon Bedrock (default): requires AWS credentials
  - Anthropic API: requires ANTHROPIC_API_KEY
  - OpenAI API: requires OPENAI_API_KEY

Install: pip install strands-agents strands-agents-tools

Reference: https://github.com/strands-agents/sdk-python
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .base_adapter import BaseAgentAdapter
from .prompts import TUTOR_SYSTEM_PROMPT

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from config.llm_config import (
    STRANDS_AGENT_MODEL,
    STRANDS_ANTHROPIC_MODEL,
    STRANDS_BEDROCK_MODEL,
    STRANDS_OPENAI_MODEL,
)

try:
    from strands import Agent
    from strands import tool as strands_tool

    STRANDS_AVAILABLE = True
except ImportError:
    STRANDS_AVAILABLE = False


def _build_json_schema(params: dict) -> dict:
    """Convert benchmark parameter spec to a JSON Schema object.

    The benchmark schema stores parameters as ``{name: {type, description,
    required, ...}}``.  We normalise this into a standard JSON Schema so
    that SDKs that inspect tool metadata can present correct parameter
    information to the model.
    """
    properties: dict = {}
    required_list: list[str] = []
    for param_name, param_info in params.items():
        if isinstance(param_info, dict):
            prop: dict = {
                "type": param_info.get("type", "string"),
                "description": param_info.get("description", param_name),
            }
            if "items" in param_info:
                prop["items"] = param_info["items"]
            properties[param_name] = prop
            if param_info.get("required", False):
                required_list.append(param_name)
        else:
            properties[param_name] = {
                "type": "string",
                "description": param_name,
            }
    schema: dict = {"type": "object", "properties": properties}
    if required_list:
        schema["required"] = required_list
    return schema


def _make_strands_tools(available_tools: list[dict], tool_callback) -> list:
    """Build Strands tool callables from benchmark tool schemas.

    Each tool becomes a decorated Python function with an attached
    ``tool_schema`` dict so the Strands SDK can expose typed parameters
    to the model.
    """
    strands_tools = []

    for tool_spec in available_tools:
        t_name = tool_spec["name"]
        t_desc = tool_spec.get("description", f"Tool: {t_name}")
        t_params = tool_spec.get("parameters", {})
        json_schema = _build_json_schema(t_params)

        def _make_fn(name, description, schema, callback):
            @strands_tool
            def tool_fn(**kwargs) -> str:
                result = callback(name, **kwargs)
                return str(result)

            # Patch metadata so the SDK can introspect the tool
            tool_fn.__name__ = name
            tool_fn.__qualname__ = name
            tool_fn.__doc__ = description
            # Attach full JSON Schema for SDK introspection
            tool_fn.tool_schema = {
                "name": name,
                "description": description,
                "input_schema": schema,
            }
            return tool_fn

        strands_tools.append(_make_fn(t_name, t_desc, json_schema, tool_callback))

    return strands_tools


def _resolve_model(model: str):
    """Resolve the Strands model backend.

    Tries Bedrock first (requires AWS credentials), then falls back to
    the Anthropic or OpenAI model providers.  When falling back, the
    Bedrock-format model ID is replaced with the provider-appropriate one
    so the downstream API receives a valid identifier.
    """
    # Try Bedrock
    if os.environ.get("AWS_ACCESS_KEY_ID") or os.environ.get("AWS_PROFILE"):
        try:
            from strands.models import BedrockModel

            return BedrockModel(
                model_id=model,
                region_name=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"),
            )
        except Exception:
            pass

    # Try Anthropic — remap model ID if it's still the Bedrock default
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            from strands.models import AnthropicModel

            effective_model = (
                STRANDS_ANTHROPIC_MODEL if model == STRANDS_BEDROCK_MODEL else model
            )
            return AnthropicModel(model_id=effective_model)
        except Exception:
            pass

    # Try OpenAI — remap model ID if it's still the Bedrock default
    if os.environ.get("OPENAI_API_KEY"):
        try:
            from strands.models import OpenAIModel

            effective_model = (
                STRANDS_OPENAI_MODEL if model == STRANDS_BEDROCK_MODEL else model
            )
            return OpenAIModel(model_id=effective_model)
        except Exception:
            pass

    raise ValueError(
        "No valid credentials found for Strands. Set AWS_ACCESS_KEY_ID + "
        "AWS_SECRET_ACCESS_KEY, ANTHROPIC_API_KEY, or OPENAI_API_KEY."
    )


class StrandsAdapter(BaseAgentAdapter):
    """Adapter using the AWS Strands Agents SDK.

    The SDK manages the agent loop autonomously — the agent decides
    which tools to call and iterates until it produces a final answer.

    Requires one of:
      - AWS credentials (for Bedrock)
      - ANTHROPIC_API_KEY
      - OPENAI_API_KEY
    """

    def __init__(
        self,
        model: str = STRANDS_AGENT_MODEL,
        system_prompt: str = "",
        agent_name: str = "strands",
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate a response using the Strands Agents SDK."""
        if not STRANDS_AVAILABLE:
            return "[Error: strands-agents not installed. pip install strands-agents strands-agents-tools]"

        try:
            model = _resolve_model(self.model)
        except ValueError as e:
            return f"[Strands error: {e}]"

        # Build tools
        tools = []
        if available_tools and tool_callback:
            tools = _make_strands_tools(available_tools, tool_callback)

        try:
            agent = Agent(
                model=model,
                tools=tools,
                system_prompt=self.system_prompt,
            )

            # Build prompt from conversation history
            prompt = self._build_prompt(messages)
            result = agent(prompt)

            # Extract text from the agent result
            if hasattr(result, "message") and result.message:
                content = result.message.get("content", [])
                text_parts = []
                for block in content:
                    if isinstance(block, dict) and block.get("text"):
                        text_parts.append(block["text"])
                    elif hasattr(block, "text") and block.text:
                        text_parts.append(block.text)
                if text_parts:
                    return "\n".join(text_parts)

            return str(result) if result else "[No response from Strands agent]"

        except Exception as e:
            return f"[Strands error: {e}]"

    def _build_prompt(self, messages: list[dict]) -> str:
        """Build a single prompt string from conversation history."""
        if len(messages) == 1:
            return messages[0]["content"]

        parts = []
        for msg in messages[:-1]:
            role = "Student" if msg["role"] == "user" else "Tutor"
            parts.append(f"[{role}]: {msg['content']}")

        parts.append(
            f"\n[Student's latest message]: {messages[-1]['content']}\n\n"
            f"Respond to the student's latest message as the tutor."
        )
        return "\n\n".join(parts)
