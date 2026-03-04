"""Generic LLM adapter using OpenAI-compatible API.

Works with any LLM that supports the OpenAI chat completions API,
including OpenRouter, OpenAI, Anthropic (via proxy), local models, etc.

Uses a multi-round agent loop (inspired by maxwell/agent-core):
  LLM call → extract tool calls → execute → feed results back → repeat
  until the model stops requesting tools or MAX_TOOL_ROUNDS is reached.
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

# Maximum LLM→tool→LLM round-trips per generate_response() call.
MAX_TOOL_ROUNDS = 10


class GenericLLMAdapter(BaseAgentAdapter):
    """Adapter for any OpenAI-compatible LLM API.

    Implements a multi-round agent loop (maxwell-style): each LLM
    response is inspected for tool calls; if present, all tool calls are
    executed, results fed back, and the LLM is called again.  The loop
    exits when the model produces a text-only response (no tool calls)
    or ``MAX_TOOL_ROUNDS`` is reached.
    """

    def __init__(
        self,
        model: str = AGENT_DEFAULT_MODEL,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: str = "",
        agent_name: str = "generic_llm",
        max_tool_rounds: int = MAX_TOOL_ROUNDS,
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
        self.max_tool_rounds = max_tool_rounds

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate response using a multi-round agent loop.

        Loop (inspired by maxwell/agent-core ``runLoop``):
          1. Call LLM with current messages + tool definitions
          2. If response contains tool_calls → execute each, append
             assistant message + tool results to context, goto 1
          3. If response is text-only (no tool_calls) → return text
          4. Safety cap: stop after ``max_tool_rounds`` iterations
        """
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
            # --- Agent loop: LLM → tool calls → execute → feed back → repeat ---
            for _round in range(self.max_tool_rounds):
                response = client.chat.completions.create(
                    model=self.model,
                    messages=api_messages,
                    tools=tools,
                    max_tokens=4096,
                )

                message = response.choices[0].message

                # No tool calls → final text response, exit loop
                if not message.tool_calls or not tool_callback:
                    return message.content or ""

                # Execute every tool call in this response
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

                # Append assistant message (with tool calls) + tool results
                api_messages.append(message.model_dump())
                api_messages.extend(tool_results)

            # Safety cap reached — ask the model for a final text response
            # with tools disabled so it summarises instead of looping further.
            response = client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                max_tokens=4096,
            )
            return response.choices[0].message.content or ""

        except Exception as e:
            return f"[Agent error: {str(e)}]"

    def _format_tools(self, tools: list[dict]) -> list[dict]:
        """Convert tool schemas to OpenAI function calling format."""
        formatted = []
        for tool in tools:
            params = tool.get("parameters", {})
            properties = {}
            required_list = []
            for param_name, param_info in params.items():
                if isinstance(param_info, dict):
                    prop = {
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

            schema = {
                "type": "object",
                "properties": properties,
            }
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

    def _fallback_response(self, messages: list[dict]) -> str:
        """Fallback when OpenAI SDK is not available."""
        last_msg = messages[-1]["content"] if messages else ""
        return (
            f"I'd be happy to help you with that! "
            f"Let me look into your question about: {last_msg[:100]}... "
            f"[Note: This is a fallback response - OpenAI SDK not available]"
        )
