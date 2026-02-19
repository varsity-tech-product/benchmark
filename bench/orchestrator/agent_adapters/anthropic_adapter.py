"""Claude Agent SDK adapter for QuantTutorBench.

Uses the Claude Agent SDK (claude-agent-sdk) — the same agent harness that
powers Claude Code. This is a real agent framework with:
  - Autonomous agent loop (Claude decides when/how to use tools)
  - In-process MCP server support (custom tools via SdkMcpTool)
  - Hooks, subagents, sessions, permissions

Install: pip install claude-agent-sdk

Reference: https://platform.claude.com/docs/en/agent-sdk/overview
           https://github.com/anthropics/claude-agent-sdk-python
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

from config.llm_config import ANTHROPIC_AGENT_MODEL

try:
    from claude_agent_sdk import (
        AssistantMessage,
        ClaudeAgentOptions,
        ClaudeSDKClient,
        ResultMessage,
        SdkMcpTool,
        TextBlock,
        create_sdk_mcp_server,
    )

    CLAUDE_AGENT_SDK_AVAILABLE = True
except ImportError:
    CLAUDE_AGENT_SDK_AVAILABLE = False


def _build_sdk_tools(available_tools: list[dict], tool_callback) -> list:
    """Build SdkMcpTool objects from benchmark tool schemas.

    Each tool wraps the benchmark's tool_callback so that when Claude
    calls the tool, it routes through the MCP proxy for logging.
    """
    sdk_tools = []

    for tool_schema in available_tools:
        t_name = tool_schema["name"]
        t_desc = tool_schema.get("description", f"Tool: {t_name}")
        t_params = tool_schema.get("parameters", {})

        # Build JSON Schema for input_schema
        properties = {}
        for param_name in t_params:
            properties[param_name] = {
                "type": "string",
                "description": f"{param_name}",
            }
        input_schema = {
            "type": "object",
            "properties": properties,
        }

        # Create async handler that routes through tool_callback
        def _make_handler(name, callback):
            async def handler(args):
                try:
                    kwargs = dict(args) if args else {}
                    result = callback(name, **kwargs)
                    return {
                        "content": [{"type": "text", "text": str(result)}],
                    }
                except Exception as e:
                    return {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    }

            return handler

        sdk_tools.append(
            SdkMcpTool(
                name=t_name,
                description=t_desc,
                input_schema=input_schema,
                handler=_make_handler(t_name, tool_callback),
            )
        )

    return sdk_tools


class ClaudeAgentAdapter(BaseAgentAdapter):
    """Adapter using the Claude Agent SDK — a real agent framework.

    The SDK manages the agent loop autonomously: Claude reads the prompt,
    decides which tools to call, processes results, and iterates until done.
    This is fundamentally different from a manual tool loop.

    Requires ANTHROPIC_API_KEY in .env.
    """

    def __init__(
        self,
        model: str = ANTHROPIC_AGENT_MODEL,
        system_prompt: str = "",
        agent_name: str = "claude_agent_sdk",
        max_agent_turns: int = 10,
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT
        self.max_agent_turns = max_agent_turns

        if not os.environ.get("ANTHROPIC_API_KEY"):
            raise ValueError("ANTHROPIC_API_KEY not set in .env")

        if not CLAUDE_AGENT_SDK_AVAILABLE:
            raise ImportError(
                "claude-agent-sdk is required. Install with: pip install claude-agent-sdk"
            )

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate a response using the Claude Agent SDK.

        The SDK's agent loop handles multi-step tool calling autonomously.
        We provide our benchmark MCP tools via an in-process SDK MCP server.

        Uses ClaudeSDKClient (not query()) because SDK MCP servers require
        bidirectional communication — query() closes stdin immediately after
        writing the prompt, which kills MCP control responses.
        """
        # Prevent "cannot launch inside another Claude Code session" error
        os.environ.pop("CLAUDECODE", None)

        # Build the prompt from conversation history
        prompt = self._build_prompt(messages)

        # Create in-process MCP server with benchmark tools
        mcp_servers = {}
        if available_tools and tool_callback:
            sdk_tools = _build_sdk_tools(available_tools, tool_callback)
            if sdk_tools:
                server = create_sdk_mcp_server(
                    name="benchmark_tools",
                    version="1.0.0",
                    tools=sdk_tools,
                )
                mcp_servers["benchmark_tools"] = server

        # Disable all built-in tools — agent should only use benchmark tools
        disallowed_tools = [
            "Read",
            "Write",
            "Edit",
            "Bash",
            "Glob",
            "Grep",
            "WebSearch",
            "WebFetch",
            "Task",
            "TodoWrite",
            "NotebookEdit",
            "AskUserQuestion",
        ]

        options = ClaudeAgentOptions(
            system_prompt=self.system_prompt,
            model=self.model,
            mcp_servers=mcp_servers,
            max_turns=self.max_agent_turns,
            permission_mode="bypassPermissions",
            disallowed_tools=disallowed_tools,
        )

        # Run the async query synchronously
        try:
            result = self._run_sync(prompt, options)
            return result if result else "[No response from Claude Agent SDK]"
        except Exception as e:
            return f"[Claude Agent SDK error: {e}]"

    def _build_prompt(self, messages: list[dict]) -> str:
        """Build a prompt string from conversation history.

        The Claude Agent SDK takes a single prompt string. We format
        the conversation history so Claude has full context.
        """
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

    def _run_sync(self, prompt: str, options: ClaudeAgentOptions) -> str:
        """Run the async SDK query synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already in an async context — run in a new thread
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self._query(prompt, options))
                return future.result(timeout=120)
        else:
            return asyncio.run(self._query(prompt, options))

    async def _query(self, prompt: str, options: ClaudeAgentOptions) -> str:
        """Execute the Claude Agent SDK query and extract the result.

        Uses ClaudeSDKClient (async context manager) which keeps the
        transport connection open for bidirectional MCP communication.
        """
        result_text = ""

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            async for message in client.receive_response():
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            result_text = block.text
                elif isinstance(message, ResultMessage):
                    if hasattr(message, "result") and message.result:
                        result_text = message.result

        return result_text
