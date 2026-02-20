"""OpenAI Agent SDK adapter for QuantTutorBench.

Uses the SDK's Runner agent loop for multi-step reasoning: the agent can
make multiple tool calls autonomously within a single generate_response()
call, controlled by max_turns. Dynamic instructions inject per-task context.

Supports both native OpenAI API and OpenRouter routing.
When base_url is provided (e.g. OpenRouter), uses OpenAIChatCompletionsModel
with a custom AsyncOpenAI client for clean isolation from env vars.

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
    from agents.model_settings import ModelSettings
    from agents.tool import FunctionTool

    OPENAI_AGENTS_AVAILABLE = True
except ImportError:
    OPENAI_AGENTS_AVAILABLE = False

# Max LLM invocations per generate_response() call.
# The SDK agent loop: LLM call -> tool execution -> LLM call -> ...
# until final text output. Each LLM call counts as one turn.
DEFAULT_AGENT_MAX_TURNS = 8


class OpenAIAgentAdapter(BaseAgentAdapter):
    """Adapter for agents built with the OpenAI Agents SDK.

    Uses the SDK's Runner for true agentic behavior: the agent can chain
    multiple tool calls autonomously within a single conversation turn.

    Supports two modes:
      - Native OpenAI API (default): uses OPENAI_API_KEY
      - OpenRouter routing: pass base_url to route through OpenRouter
    """

    def __init__(
        self,
        model: str = OPENAI_AGENT_MODEL,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        system_prompt: str = "",
        agent_name: str = "openai_agent_sdk",
        max_turns: int = DEFAULT_AGENT_MAX_TURNS,
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.base_url = base_url
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT
        self.max_turns = max_turns
        self._task_context = ""

        if base_url:
            # Custom provider (e.g. OpenRouter): prefer OPENROUTER_API_KEY
            self.api_key = (
                api_key
                or os.environ.get("OPENROUTER_API_KEY", "")
                or os.environ.get("OPENAI_API_KEY", "")
            )
        else:
            # Native OpenAI
            self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

        if not self.api_key:
            key_name = "OPENROUTER_API_KEY" if base_url else "OPENAI_API_KEY"
            raise ValueError(f"{key_name} not set in .env")

        if not base_url:
            # Native OpenAI mode: set env vars for the Agent SDK
            os.environ["OPENAI_API_KEY"] = self.api_key
            os.environ.pop("OPENAI_BASE_URL", None)

    def set_task_context(self, context: str):
        """Set per-task dynamic context injected into system prompt."""
        self._task_context = context

    def _dynamic_instructions(self, ctx, agent):
        """Dynamic instructions callable for the Agent.

        The SDK calls this before each LLM invocation, allowing per-task
        context to be injected alongside the base system prompt.
        """
        base = self.system_prompt
        if self._task_context:
            base += "\n\n" + self._task_context
        return base

    def _resolve_model(self):
        """Resolve the model object for the Agent SDK.

        When base_url is set, returns an OpenAIChatCompletionsModel with a
        custom AsyncOpenAI client. Otherwise returns the model name string
        for native OpenAI.
        """
        if self.base_url:
            from agents.models.openai_chatcompletions import (
                OpenAIChatCompletionsModel,
            )
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
            return OpenAIChatCompletionsModel(model=self.model, openai_client=client)
        return self.model

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate a response using the OpenAI Agents SDK.

        The SDK's Runner handles the agent loop internally:
        LLM call -> tool execution -> LLM call -> ... -> final text output.
        This allows the agent to chain multiple tool calls autonomously.
        """
        if not OPENAI_AGENTS_AVAILABLE:
            return self._fallback_completions(messages, available_tools, tool_callback)

        try:
            agent_tools = self._build_agent_tools(available_tools, tool_callback)

            agent = Agent(
                name="quant_tutor",
                instructions=self._dynamic_instructions,
                model=self._resolve_model(),
                tools=agent_tools,
                model_settings=ModelSettings(tool_choice="auto"),
            )

            # Pass full conversation history to Runner.run_sync().
            # The SDK accepts list[TResponseInputItem] — standard
            # {"role": "user"|"assistant", "content": "..."} dicts.
            # Filter to user/assistant only; system prompt is set via
            # Agent(instructions=...).
            input_items = [
                {"role": msg["role"], "content": msg["content"]}
                for msg in messages
                if msg["role"] in ("user", "assistant")
            ]

            result = Runner.run_sync(
                agent,
                input_items,
                max_turns=self.max_turns,
            )
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
                "required": required_list,
                "additionalProperties": False,
            }

            agent_tools.append(
                FunctionTool(
                    name=tool_name,
                    description=tool_desc,
                    params_json_schema=schema,
                    on_invoke_tool=fn,
                    strict_json_schema=False,
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

        client_kwargs = {"api_key": self.api_key}
        if self.base_url:
            client_kwargs["base_url"] = self.base_url
        client = OpenAI(**client_kwargs)

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
