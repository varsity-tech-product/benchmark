"""OpenAI Agent SDK adapter for QuantTutorBench.

Wraps agents built with the OpenAI Agents SDK (openai-agents) to work
with the benchmark's conversation loop and MCP proxy.

Uses native OpenAI API with OPENAI_API_KEY from .env.

Install: pip install openai-agents

Reference: https://github.com/openai/openai-agents-python
"""

import json
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .base_adapter import BaseAgentAdapter
from .prompts import TUTOR_SYSTEM_PROMPT

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from config.llm_config import OPENAI_AGENT_MODEL

try:
    from agents import Agent, Runner
    from agents.tool import FunctionTool

    OPENAI_AGENTS_AVAILABLE = True
except ImportError:
    OPENAI_AGENTS_AVAILABLE = False


class OpenAIAgentAdapter(BaseAgentAdapter):
    """Adapter for agents built with the OpenAI Agents SDK.

    Uses native OpenAI API (not OpenRouter). Requires OPENAI_API_KEY in .env.
    """

    def __init__(
        self,
        model: str = OPENAI_AGENT_MODEL,
        api_key: Optional[str] = None,
        system_prompt: str = "",
        agent_name: str = "openai_agent_sdk",
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT

        if not self.api_key:
            raise ValueError("OPENAI_API_KEY not set in .env")

        # Set env vars for the Agent SDK to pick up (native OpenAI, no base_url override)
        os.environ["OPENAI_API_KEY"] = self.api_key
        # Remove any OpenRouter base_url that might have been set globally
        os.environ.pop("OPENAI_BASE_URL", None)

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate a response using the OpenAI Agents SDK."""
        if not OPENAI_AGENTS_AVAILABLE:
            return self._fallback_completions(messages, available_tools, tool_callback)

        try:
            agent_tools = self._build_agent_tools(available_tools, tool_callback)

            agent = Agent(
                name="quant_tutor",
                instructions=self.system_prompt,
                model=self.model,
                tools=agent_tools,
            )

            # Get the last user message
            last_user_msg = ""
            for msg in reversed(messages):
                if msg["role"] == "user":
                    last_user_msg = msg["content"]
                    break

            result = Runner.run_sync(agent, last_user_msg)
            return result.final_output or ""

        except Exception as e:
            return f"[OpenAI Agent SDK error: {str(e)}]"

    def _build_agent_tools(self, available_tools: list[dict], tool_callback) -> list:
        """Build OpenAI Agent SDK tools from MCP tool schemas.

        FunctionTool.on_invoke_tool expects: async (ToolContext, str) -> Any
        where str is the JSON-encoded arguments from the LLM.
        """
        if not OPENAI_AGENTS_AVAILABLE or not tool_callback:
            return []

        agent_tools = []
        for tool_schema in available_tools:
            tool_name = tool_schema["name"]
            tool_desc = tool_schema.get("description", f"Tool: {tool_name}")
            params = tool_schema.get("parameters", {})

            def make_tool_fn(name, callback):
                async def tool_fn(ctx, args_json: str) -> str:
                    args = json.loads(args_json) if args_json else {}
                    result = callback(name, **args)
                    return str(result)

                return tool_fn

            fn = make_tool_fn(tool_name, tool_callback)

            properties = {}
            required = []
            for param_name in params:
                properties[param_name] = {
                    "type": "string",
                    "description": f"{param_name} parameter",
                }
                required.append(param_name)

            schema = {
                "type": "object",
                "properties": properties,
                "required": required,
            }

            agent_tools.append(
                FunctionTool(
                    name=tool_name,
                    description=tool_desc,
                    params_json_schema=schema,
                    on_invoke_tool=fn,
                )
            )

        return agent_tools

    def _fallback_completions(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Fallback to direct OpenAI completions API."""
        try:
            from openai import OpenAI
        except ImportError:
            return "[Error: neither openai-agents nor openai package available]"

        client = OpenAI(api_key=self.api_key)

        api_messages = [{"role": "system", "content": self.system_prompt}]
        api_messages.extend(messages)

        tools = None
        if available_tools:
            tools = self._format_tools_openai(available_tools)

        try:
            response = client.chat.completions.create(
                model=self.model,
                messages=api_messages,
                tools=tools,
                max_tokens=4096,
            )

            message = response.choices[0].message

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

                api_messages.append(message.model_dump())
                api_messages.extend(tool_results)

                final_response = client.chat.completions.create(
                    model=self.model,
                    messages=api_messages,
                    max_tokens=4096,
                )
                return final_response.choices[0].message.content or ""

            return message.content or ""

        except Exception as e:
            return f"[OpenAI API error: {str(e)}]"

    def _format_tools_openai(self, tools: list[dict]) -> list[dict]:
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
