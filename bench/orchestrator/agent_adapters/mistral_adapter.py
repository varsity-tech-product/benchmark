"""Mistral AI adapter for QuantTutorBench.

Uses the Mistral Python SDK with function calling support.
The Mistral chat API is OpenAI-compatible, so tool schemas reuse the
same format as the generic adapter.

Install: pip install mistralai

Reference: https://docs.mistral.ai/capabilities/function_calling/
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

from config.llm_config import MISTRAL_AGENT_MODEL

try:
    from mistralai import Mistral

    MISTRAL_AVAILABLE = True
except ImportError:
    MISTRAL_AVAILABLE = False


class MistralAdapter(BaseAgentAdapter):
    """Adapter using the Mistral AI SDK with function calling.

    Requires MISTRAL_API_KEY in .env.
    """

    def __init__(
        self,
        model: str = MISTRAL_AGENT_MODEL,
        api_key: Optional[str] = None,
        system_prompt: str = "",
        agent_name: str = "mistral",
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.api_key = api_key or os.environ.get("MISTRAL_API_KEY", "")
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT

        if not self.api_key:
            raise ValueError("MISTRAL_API_KEY not set in .env")

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate a response using the Mistral SDK with function calling."""
        if not MISTRAL_AVAILABLE:
            return "[Error: mistralai not installed. pip install mistralai]"

        client = Mistral(api_key=self.api_key)

        api_messages = [{"role": "system", "content": self.system_prompt}]
        api_messages.extend(messages)

        tools = self._format_tools(available_tools) if available_tools else None

        try:
            response = client.chat.complete(
                model=self.model,
                messages=api_messages,
                tools=tools,
                tool_choice="auto" if tools else None,
            )

            message = response.choices[0].message

            # Multi-round tool calling loop
            max_iterations = 5
            for _ in range(max_iterations):
                if not message.tool_calls or not tool_callback:
                    break

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

                response = client.chat.complete(
                    model=self.model,
                    messages=api_messages,
                    tools=tools,
                    tool_choice="auto",
                )
                message = response.choices[0].message

            return message.content or ""

        except Exception as e:
            return f"[Mistral error: {e}]"

    def _format_tools(self, tools: list[dict]) -> list[dict]:
        """Convert tool schemas to OpenAI-compatible function calling format."""
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
