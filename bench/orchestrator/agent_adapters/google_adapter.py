"""Google GenAI SDK adapter for QuantTutorBench.

Uses the new google-genai SDK (not the deprecated google-generativeai)
with full function calling support.

Install: pip install google-genai

Reference: https://github.com/googleapis/python-genai
           https://ai.google.dev/gemini-api/docs/function-calling
"""

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .base_adapter import BaseAgentAdapter
from .prompts import TUTOR_SYSTEM_PROMPT

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from config.llm_config import GOOGLE_AGENT_MODEL


class GoogleAdapter(BaseAgentAdapter):
    """Adapter using the new Google GenAI SDK with function calling.

    Uses google-genai (from google import genai) with:
    - types.Tool(function_declarations=[...]) for tool definitions
    - types.Part.from_function_response() for tool results
    - client.models.generate_content() for generation

    Usage:
        adapter = GoogleAdapter(model="gemini-2.5-flash")
    """

    def __init__(
        self,
        model: str = GOOGLE_AGENT_MODEL,
        api_key: Optional[str] = None,
        system_prompt: str = "",
        agent_name: str = "google_genai",
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.api_key = (
            api_key
            or os.environ.get("GOOGLE_API_KEY")
            or os.environ.get("GEMINI_API_KEY", "")
        )
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate a response using Google GenAI SDK with function calling."""
        try:
            from google import genai
            from google.genai import types
        except ImportError:
            return "[Error: google-genai not installed. pip install google-genai]"

        client = genai.Client(api_key=self.api_key)

        # Build tool declarations
        tools_config = None
        if available_tools and tool_callback:
            declarations = self._build_declarations(available_tools)
            if declarations:
                tools_config = [types.Tool(function_declarations=declarations)]

        # Build generation config
        config = types.GenerateContentConfig(
            system_instruction=self._get_full_system_prompt(),
            max_output_tokens=4096,
        )
        if tools_config:
            config = types.GenerateContentConfig(
                system_instruction=self._get_full_system_prompt(),
                max_output_tokens=4096,
                tools=tools_config,
            )

        # Convert messages to Google Content format
        contents = self._build_contents(messages)

        try:
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

            # Handle function calling loop
            if tool_callback and self._has_function_call(response):
                return self._handle_function_call_loop(
                    client, contents, config, response, tool_callback, types
                )

            return response.text or ""

        except Exception as e:
            return f"[Google GenAI error: {e}]"

    def _build_declarations(self, tools: list[dict]) -> list[dict]:
        """Convert tool schemas to Google function declarations."""
        declarations = []
        for tool in tools:
            params = tool.get("parameters", {})
            properties = {}
            for param_name in params:
                properties[param_name] = {
                    "type": "string",
                    "description": f"{param_name} parameter",
                }
            declarations.append(
                {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                    },
                }
            )
        return declarations

    def _build_contents(self, messages: list[dict]) -> list:
        """Convert messages to Google Content format.

        Maps roles: 'user' -> 'user', 'assistant' -> 'model'.
        """
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(
                {
                    "role": role,
                    "parts": [{"text": msg["content"]}],
                }
            )
        return contents

    def _has_function_call(self, response) -> bool:
        """Check if response contains a function call."""
        try:
            for candidate in response.candidates:
                for part in candidate.content.parts:
                    if part.function_call:
                        return True
        except (AttributeError, IndexError):
            pass
        return False

    def _handle_function_call_loop(
        self, client, contents, config, response, tool_callback, types
    ) -> str:
        """Handle Google function calling loop.

        Supports multiple rounds of function calls until the model
        stops requesting them or max iterations reached.
        """
        max_iterations = 5
        for _ in range(max_iterations):
            # Extract function calls from response
            function_calls = []
            try:
                for part in response.candidates[0].content.parts:
                    if part.function_call:
                        function_calls.append(part.function_call)
            except (AttributeError, IndexError):
                break

            if not function_calls:
                break

            # Append the model's response (with function calls) to contents
            contents.append(response.candidates[0].content)

            # Execute each function call and build response parts
            function_response_parts = []
            for fc in function_calls:
                try:
                    args = dict(fc.args) if fc.args else {}
                    result = tool_callback(fc.name, **args)
                    function_response_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"result": str(result)},
                        )
                    )
                except Exception as e:
                    function_response_parts.append(
                        types.Part.from_function_response(
                            name=fc.name,
                            response={"error": str(e)},
                        )
                    )

            # Append function responses as user content
            contents.append(types.Content(role="user", parts=function_response_parts))

            # Get next response
            response = client.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
            )

            # If no more function calls, we're done
            if not self._has_function_call(response):
                break

        try:
            return response.text or ""
        except Exception:
            # If response.text fails (e.g., response only has function calls),
            # extract text manually
            try:
                text_parts = []
                for part in response.candidates[0].content.parts:
                    if hasattr(part, "text") and part.text:
                        text_parts.append(part.text)
                return "\n".join(text_parts) if text_parts else ""
            except (AttributeError, IndexError):
                return ""
