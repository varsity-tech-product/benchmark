"""Generic LLM adapter using OpenAI-compatible API.

Works with any LLM that supports the OpenAI chat completions API,
including OpenRouter, OpenAI, Anthropic (via proxy), local models, etc.
"""

import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .base_adapter import BaseAgentAdapter
from .prompts import TUTOR_SYSTEM_PROMPT

# Load .env from project root (two levels up from this file)
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from config.llm_config import AGENT_DEFAULT_MODEL, OPENROUTER_BASE_URL


class GenericLLMAdapter(BaseAgentAdapter):
    """Adapter for any OpenAI-compatible LLM API."""

    def __init__(
        self,
        model: str = AGENT_DEFAULT_MODEL,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: str = "",
        agent_name: str = "generic_llm",
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.api_key = (
            api_key
            or os.environ.get("OPENROUTER_API_KEY")
            or os.environ.get("OPENAI_API_KEY", "")
        )
        self.base_url = base_url or os.environ.get(
            "OPENROUTER_BASE_URL", OPENROUTER_BASE_URL
        )
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate response using OpenAI-compatible API with tool use."""
        try:
            from openai import OpenAI
        except ImportError:
            return self._fallback_response(messages)

        client = OpenAI(api_key=self.api_key, base_url=self.base_url)

        # Build messages with system prompt
        api_messages = [{"role": "system", "content": self.system_prompt}]
        api_messages.extend(messages)

        # Convert tool schemas to OpenAI format
        tools = self._format_tools(available_tools) if available_tools else None

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                tools=tools,
                max_tokens=4096,
            )

            message = response.choices[0].message

            # Handle tool calls
            if message.tool_calls and tool_callback:
                tool_results = []
                for tc in message.tool_calls:
                    args = (
                        json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    )
                    result = tool_callback(tc.function.name, **args)
                    tool_results.append(
                        {
                            "tool_call_id": tc.id,
                            "role": "tool",
                            "content": str(result),
                        }
                    )

                # Add assistant message with tool calls and tool results
                api_messages.append(message.model_dump())
                api_messages.extend(tool_results)

                # Get final response after tool use
                final_response = client.chat.completions.create(
                    model=self.model,
                    messages=api_messages,
                    max_tokens=4096,
                )
                return final_response.choices[0].message.content or ""

            return message.content or ""

        except Exception as e:
            return f"[Agent error: {str(e)}]"

    def _format_tools(self, tools: list[dict]) -> list[dict]:
        """Convert tool schemas to OpenAI function calling format."""
        formatted = []
        for tool in tools:
            params = tool.get("parameters", {})
            properties = {}
            for param_name, param_type in params.items():
                properties[param_name] = {
                    "type": "string",
                    "description": f"{param_name} parameter",
                }

            formatted.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool.get("description", ""),
                        "parameters": {
                            "type": "object",
                            "properties": properties,
                        },
                    },
                }
            )
        return formatted

    def _fallback_response(self, messages: list[dict]) -> str:
        """Fallback when OpenAI SDK is not available."""
        last_msg = messages[-1]["content"] if messages else ""
        return (
            f"I'd be happy to help you with that! "
            f"Let me look into your question about: {last_msg[:100]}... "
            f"[Note: This is a fallback response - OpenAI SDK not available]"
        )
