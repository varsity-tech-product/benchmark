"""Microsoft Agent Framework adapter for QuantTutorBench.

Uses Microsoft's agent-framework SDK with OpenAI Responses API backend.
The SDK is still in preview (GA target Q1 2026).

Install: pip install agent-framework  # may need --pre

Reference: https://github.com/microsoft/agent-framework
"""

import asyncio
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .base_adapter import BaseAgentAdapter
from .prompts import TUTOR_SYSTEM_PROMPT

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from config.llm_config import MICROSOFT_AGENT_MODEL

try:
    from agent_framework import tool as af_tool

    MICROSOFT_AF_AVAILABLE = True
except ImportError:
    MICROSOFT_AF_AVAILABLE = False


def _build_json_schema(params: dict) -> dict:
    """Convert benchmark parameter spec to a JSON Schema object."""
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


def _make_af_tools(available_tools: list[dict], tool_callback) -> list:
    """Build agent-framework tool callables from benchmark tool schemas.

    Each tool becomes a decorated Python function with an attached
    ``tool_schema`` dict so the SDK can expose typed parameters to the model.
    """
    af_tools = []

    for tool_spec in available_tools:
        t_name = tool_spec["name"]
        t_desc = tool_spec.get("description", f"Tool: {t_name}")
        t_params = tool_spec.get("parameters", {})
        json_schema = _build_json_schema(t_params)

        def _make_fn(name, description, schema, callback):
            @af_tool
            def tool_fn(**kwargs) -> str:
                result = callback(name, **kwargs)
                return str(result)

            tool_fn.__name__ = name
            tool_fn.__qualname__ = name
            tool_fn.__doc__ = description
            # Attach full JSON Schema for SDK introspection
            tool_fn.tool_schema = {
                "name": name,
                "description": description,
                "parameters": schema,
            }
            return tool_fn

        af_tools.append(_make_fn(t_name, t_desc, json_schema, tool_callback))

    return af_tools


class MicrosoftAgentAdapter(BaseAgentAdapter):
    """Adapter using the Microsoft Agent Framework.

    Uses OpenAI Responses API as the backend. Requires OPENAI_API_KEY.
    The SDK manages the agent loop — the agent autonomously decides
    which tools to call and iterates until producing a final answer.
    """

    def __init__(
        self,
        model: str = MICROSOFT_AGENT_MODEL,
        api_key: Optional[str] = None,
        system_prompt: str = "",
        agent_name: str = "microsoft",
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        # Require a real OPENAI_API_KEY — the OpenRouter env bridge
        # (run_benchmark.py line 52) sets OPENAI_API_KEY from
        # OPENROUTER_API_KEY, but OpenRouter is not a valid backend
        # for the Microsoft Agent Framework's Responses API.
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT

        if not self.api_key:
            raise ValueError(
                "OPENAI_API_KEY not set in .env (required for Microsoft Agent Framework)"
            )

        # Guard: reject the OpenRouter bridge key — it won't work with
        # the Responses API that the Microsoft framework expects.
        _base_url = os.environ.get("OPENAI_BASE_URL", "")
        if not api_key and "openrouter" in _base_url:
            raise ValueError(
                "Microsoft Agent Framework requires a native OPENAI_API_KEY. "
                "The current OPENAI_API_KEY was set by the OpenRouter env bridge "
                "and will not work. Set a real OPENAI_API_KEY in .env."
            )

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate a response using the Microsoft Agent Framework."""
        if not MICROSOFT_AF_AVAILABLE:
            return "[Error: agent-framework not installed. pip install agent-framework]"

        try:
            return self._run_sync(messages, available_tools, tool_callback)
        except Exception as e:
            return f"[Microsoft Agent Framework error: {e}]"

    def _run_sync(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Run the async agent synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self._agenerate(messages, available_tools, tool_callback),
                )
                return future.result(timeout=120)
        else:
            return asyncio.run(
                self._agenerate(messages, available_tools, tool_callback)
            )

    async def _agenerate(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Async generation using the Agent Framework."""
        from agent_framework import OpenAIResponsesClient

        client = OpenAIResponsesClient(api_key=self.api_key)

        # Build tools
        tools = []
        if available_tools and tool_callback:
            tools = _make_af_tools(available_tools, tool_callback)

        agent = client.as_agent(
            name="quant_tutor",
            instructions=self.system_prompt,
            model=self.model,
            tools=tools,
        )

        # Build prompt from conversation history
        prompt = self._build_prompt(messages)
        result = await agent.run(prompt)

        if hasattr(result, "final_output"):
            return result.final_output or ""
        return str(result) if result else "[No response from Microsoft agent]"

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
