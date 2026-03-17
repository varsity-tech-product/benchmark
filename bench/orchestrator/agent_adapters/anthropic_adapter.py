"""Anthropic adapter for QuantTutorBench.

Two independent switches control behavior:

  ANTHROPIC_USE_SDK (transport):
    True  → Claude Agent SDK (ClaudeSDKClient) — black-box agent loop.
            Requires ANTHROPIC_API_KEY. Does NOT work with OAuth.
    False → Anthropic Python SDK BetaToolRunner — automatic tool loop with
            cross-turn context persistence, parallel tool execution,
            prompt caching, and extended thinking support.

  AGENT_USE_OAUTH (auth, only when ANTHROPIC_USE_SDK=False):
    True  → OAuth token (CLAUDE_CODE_OAUTH_TOKEN) + anthropic-beta header
    False → API key (ANTHROPIC_API_KEY) + x-api-key header

Both modes route tool calls through the benchmark's MCPProxy for logging.
Claude autonomously decides all tool calls in every mode.
"""

import asyncio
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .base_adapter import BaseAgentAdapter, TokenRecord
from .prompts import TUTOR_SYSTEM_PROMPT

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from config.llm_config import (
    AGENT_USE_OAUTH,
    ANTHROPIC_AGENT_MODEL,
    ANTHROPIC_ENABLE_THINKING,
    ANTHROPIC_THINKING_BUDGET,
    ANTHROPIC_USE_SDK,
    OAUTH_BETA_HEADER,
)
from config.pricing import estimate_cost

log = logging.getLogger(__name__)

# --- Anthropic Python SDK (BetaToolRunner, direct API mode) ---
try:
    import anthropic
    from anthropic.lib.tools._beta_functions import BetaBuiltinFunctionTool

    ANTHROPIC_SDK_AVAILABLE = True
except ImportError:
    ANTHROPIC_SDK_AVAILABLE = False

# --- Claude Agent SDK imports (SDK mode only) ---
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
    from claude_agent_sdk._errors import MessageParseError

    CLAUDE_AGENT_SDK_AVAILABLE = True
except ImportError:
    CLAUDE_AGENT_SDK_AVAILABLE = False
    MessageParseError = Exception  # fallback so except clause compiles


# ---------------------------------------------------------------------------
# DynamicTool — wraps MCP tool schema + callback for BetaToolRunner
# ---------------------------------------------------------------------------


class DynamicTool(BetaBuiltinFunctionTool if ANTHROPIC_SDK_AVAILABLE else object):
    """Auto-executable tool for Anthropic BetaToolRunner.

    Converts benchmark MCP tool schemas + a unified tool_callback into
    SDK-compatible objects that the runner can auto-execute.
    """

    def __init__(self, schema: dict, callback: callable):
        self._schema = schema
        self._callback = callback
        self._name = schema["name"]

    @property
    def name(self) -> str:
        return self._name

    def to_dict(self) -> dict:
        """Return Anthropic API tool definition."""
        # Build input_schema from MCP-style parameters
        properties = {}
        required = []
        for param_name, param_info in self._schema.get("parameters", {}).items():
            if isinstance(param_info, dict):
                prop = {
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

        input_schema = {"type": "object", "properties": properties}
        if required:
            input_schema["required"] = required

        return {
            "name": self._name,
            "description": self._schema.get("description", ""),
            "input_schema": input_schema,
        }

    def call(self, input: object) -> str:
        """Auto-executed by BetaToolRunner when Claude calls this tool."""
        kwargs = dict(input) if isinstance(input, dict) else {}
        result = self._callback(self._name, **kwargs)
        return str(result)


def _build_runner_tools(available_tools: list[dict], tool_callback: callable) -> list:
    """Convert benchmark tool schemas to DynamicTool objects for BetaToolRunner."""
    return [DynamicTool(schema, tool_callback) for schema in available_tools]


# ---------------------------------------------------------------------------
# Tool schema converters (SDK mode)
# ---------------------------------------------------------------------------


def _build_sdk_tools(available_tools: list[dict], tool_callback) -> list:
    """Build SdkMcpTool objects from benchmark tool schemas (SDK mode)."""
    sdk_tools = []

    for tool_schema in available_tools:
        t_name = tool_schema["name"]
        t_desc = tool_schema.get("description", f"Tool: {t_name}")
        t_params = tool_schema.get("parameters", {})

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


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class ClaudeAgentAdapter(BaseAgentAdapter):
    """Adapter for Claude agent under test.

    Transport (ANTHROPIC_USE_SDK):
      True  → Claude Agent SDK (ClaudeSDKClient, black-box loop)
      False → Anthropic Python SDK BetaToolRunner (automatic tool loop
              with cross-turn context persistence via _input_history)

    Auth (AGENT_USE_OAUTH, BetaToolRunner mode only):
      True  → OAuth token (Bearer + anthropic-beta header)
      False → API key (x-api-key header)

    In all modes, Claude autonomously decides which tools to call and when.
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

        # Persistent state for cross-turn context (parallels OpenAI _input_history)
        self._task_context: str = ""
        self._input_history: list[dict] = []
        self._thinking_trace: list[dict] = []  # COT blocks from extended thinking
        self._turn_index: int = 0  # conversation turn counter for thinking trace
        self._turn_content_blocks: dict[int, list[dict]] = {}  # per-turn content blocks
        self._current_turn_blocks: list[dict] = (
            []
        )  # incremental capture during iteration
        self._client: object | None = None  # anthropic.Anthropic instance

        if ANTHROPIC_USE_SDK:
            # SDK mode: requires API key, does NOT work with OAuth
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise ValueError("ANTHROPIC_API_KEY not set (required for SDK mode)")
            if not CLAUDE_AGENT_SDK_AVAILABLE:
                raise ImportError(
                    "claude-agent-sdk required for SDK mode. "
                    "pip install claude-agent-sdk  OR  set ANTHROPIC_USE_SDK=False"
                )
        else:
            # BetaToolRunner mode: create SDK client
            if not ANTHROPIC_SDK_AVAILABLE:
                raise ImportError("anthropic package required. pip install anthropic")
            if AGENT_USE_OAUTH:
                from config.auth import get_oauth_token

                auth_token = get_oauth_token()
                if not auth_token:
                    raise ValueError(
                        "OAuth token not found in Keychain or "
                        "CLAUDE_CODE_OAUTH_TOKEN env var"
                    )
                # SDK falls back to ANTHROPIC_API_KEY env var when api_key=None,
                # so we must explicitly suppress it after construction to
                # ensure only the Bearer token is sent.
                self._client = anthropic.Anthropic(auth_token=auth_token)
                self._client.api_key = None  # suppress X-Api-Key header
                self._betas = [OAUTH_BETA_HEADER]
            else:
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
                if not api_key:
                    raise ValueError("ANTHROPIC_API_KEY not set in .env")
                self._client = anthropic.Anthropic(api_key=api_key)
                self._betas = []

    def set_task_context(self, context: str):
        """Set per-task dynamic context injected into system prompt.

        Resets conversation history but preserves thinking_trace —
        the trace is collected by pre_teardown_hook after the
        conversation ends but before reset() is called.
        """
        self._task_context = context
        self._input_history = []
        # NOTE: _thinking_trace and _turn_index are NOT reset here.
        # They are cleared in reset() which runs between tasks.

    def _get_full_system_prompt(self) -> str:
        """Build system prompt with optional task context."""
        base = self.system_prompt
        if self._task_context:
            base += "\n\n" + self._task_context
        return base

    def set_agent_max_steps(self, n: int):
        """Limit how many LLM→tool→LLM cycles per generate_response() call."""
        self.max_agent_turns = n

    def reset(self):
        """Reset internal state between tasks."""
        super().reset()
        self._input_history = []
        self._task_context = ""
        self._thinking_trace = []
        self._turn_index = 0
        self._turn_content_blocks = {}
        self._current_turn_blocks = []

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Route to SDK or direct API path based on ANTHROPIC_USE_SDK."""
        if ANTHROPIC_USE_SDK:
            return self._generate_sdk(messages, available_tools, tool_callback)
        else:
            return self._generate_direct(messages, available_tools, tool_callback)

    # ==================================================================
    # Direct API mode: Anthropic Python SDK BetaToolRunner
    # ==================================================================

    def _generate_direct(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate response via Anthropic BetaToolRunner.

        The SDK handles the tool loop automatically:
        - Auto-executes tools via DynamicTool.call()
        - Parallel tool execution when Claude emits multiple tool_use
        - Cross-turn context persistence via _input_history
        - Prompt caching and rate-limit retry built-in
        """
        try:
            # Extract the latest user message (same pattern as OpenAI SDK adapter)
            new_user_msg = None
            for msg in reversed(messages):
                if msg["role"] == "user":
                    new_user_msg = msg["content"]
                    break
            if new_user_msg is None:
                return ""

            self._input_history.append({"role": "user", "content": new_user_msg})

            # Build auto-executable tool objects
            tools = (
                _build_runner_tools(available_tools, tool_callback)
                if available_tools and tool_callback
                else []
            )

            # Create BetaToolRunner — SDK manages the full agent loop
            runner_kwargs = dict(
                model=self.model,
                max_tokens=16384,
                messages=list(self._input_history),
                system=self._get_full_system_prompt(),
                max_iterations=self.max_agent_turns,
            )
            if tools:
                runner_kwargs["tools"] = tools
            if self._betas:
                runner_kwargs["betas"] = self._betas

            # Enable automatic compaction when context exceeds threshold.
            # The SDK calls an extra LLM summarization and replaces the
            # full history with a condensed version — prevents O(n²) token
            # growth across long multi-turn conversations.
            runner_kwargs["compaction_control"] = {"enabled": True}

            # NOTE: context_management (server-side clear_tool_uses /
            # compact) requires a beta header not yet available via OAuth.
            # compaction_control above is client-side and sufficient.

            # Extended thinking: capture COT blocks per-iteration.
            if ANTHROPIC_ENABLE_THINKING:
                runner_kwargs["thinking"] = {
                    "type": "enabled",
                    "budget_tokens": ANTHROPIC_THINKING_BUDGET,
                }

            runner = self._client.beta.messages.tool_runner(**runner_kwargs)

            # Iterate manually (instead of runner.until_done()) to capture
            # token usage from EVERY intermediate API call, not just the last.
            # Also extracts thinking blocks before they are persisted to history.
            # Content blocks are captured incrementally — the runner strips
            # thinking from intermediate messages, so we must grab them here
            # before they are lost.
            final_message = None
            iteration = 0
            self._current_turn_blocks = []
            for message in runner:
                final_message = message
                iteration += 1
                self._record_message_usage(message)
                self._extract_thinking(message, iteration)
                self._capture_iteration_blocks(message)

            # Persist full message history (including tool_use/tool_result).
            # If compaction fired, this already contains the summarized version.
            self._input_history = list(runner._params["messages"])

            # Finalize content blocks: inject tool_results from history into
            # the incrementally captured blocks.
            self._turn_content_blocks[self._turn_index] = self._finalize_turn_blocks()

            # Strip thinking blocks from persisted history to prevent
            # them from being re-sent as input tokens on subsequent turns.
            # COT is already captured in _thinking_trace above.
            self._strip_thinking_from_history()

            self._turn_index += 1

            # Extract final text response
            result_text = ""
            for block in final_message.content:
                if hasattr(block, "text"):
                    result_text += block.text

            return result_text if result_text else "[No response from Anthropic API]"

        except Exception as e:
            log.error("BetaToolRunner error: %s", e, exc_info=True)
            return f"[Anthropic API error: {e}]"

    def _record_message_usage(self, message) -> None:
        """Record token usage from a single BetaToolRunner iteration.

        Called once per API call inside the runner loop (via manual
        iteration), so every intermediate tool-loop call is captured —
        not just the final message.
        """
        usage = getattr(message, "usage", None)
        if not usage:
            return
        inp = getattr(usage, "input_tokens", 0) or 0
        out = getattr(usage, "output_tokens", 0) or 0
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        total_inp = inp + cache_read + cache_create
        self._token_records.append(
            TokenRecord(
                model=self.model,
                input_tokens=total_inp,
                output_tokens=out,
                cost_usd=estimate_cost(self.model, total_inp, out),
            )
        )

    # ------------------------------------------------------------------
    # Extended thinking helpers
    # ------------------------------------------------------------------

    def _extract_thinking(self, message, iteration: int) -> None:
        """Extract thinking blocks from a single BetaToolRunner iteration.

        Called inside the ``for message in runner:`` loop so that every
        intermediate thinking block is captured before the runner
        appends the message to history.
        """
        content = getattr(message, "content", None)
        if not content:
            return
        for block in content:
            if getattr(block, "type", None) == "thinking":
                self._thinking_trace.append(
                    {
                        "turn_index": self._turn_index,
                        "iteration": iteration,
                        "thinking": block.thinking,
                    }
                )

    def _strip_thinking_from_history(self) -> None:
        """Remove thinking blocks from ``_input_history`` to prevent
        them from being re-sent as input tokens on subsequent turns.

        Thinking blocks are the model's internal scratchpad and do not
        need to persist in the conversation history.  They have already
        been captured in ``_thinking_trace`` by ``_extract_thinking``.
        """
        for msg in self._input_history:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content")
            if not isinstance(content, list):
                continue
            filtered = [
                block
                for block in content
                if not (
                    (isinstance(block, dict) and block.get("type") == "thinking")
                    or getattr(block, "type", None) == "thinking"
                )
            ]
            if len(filtered) != len(content):
                msg["content"] = filtered

    def get_thinking_trace(self) -> list[dict]:
        """Return accumulated thinking/COT blocks from all iterations."""
        return list(self._thinking_trace)

    # ------------------------------------------------------------------
    # Content blocks capture (for web UI inline display)
    # ------------------------------------------------------------------

    def _capture_iteration_blocks(self, message) -> None:
        """Capture content blocks from a single BetaToolRunner iteration.

        Called inside the ``for message in runner:`` loop BEFORE the runner
        strips thinking blocks from its internal history.  This preserves
        intermediate thinking blocks that would otherwise be lost.
        """
        content = getattr(message, "content", None)
        if not content:
            return
        if isinstance(content, str):
            if content:
                self._current_turn_blocks.append({"type": "text", "text": content})
            return
        if not isinstance(content, list):
            return

        for block in content:
            bt = getattr(block, "type", None)

            if bt == "thinking":
                thinking_text = getattr(block, "thinking", "")
                if thinking_text:
                    self._current_turn_blocks.append(
                        {"type": "thinking", "text": thinking_text}
                    )

            elif bt == "tool_use":
                self._current_turn_blocks.append(
                    {
                        "type": "tool_use",
                        "name": getattr(block, "name", ""),
                        "input": getattr(block, "input", {}),
                        "_tool_id": getattr(block, "id", ""),
                    }
                )

            elif bt == "text":
                text_val = getattr(block, "text", "")
                if text_val:
                    self._current_turn_blocks.append({"type": "text", "text": text_val})

    def _finalize_turn_blocks(self) -> list[dict]:
        """Inject tool_results from history into incrementally captured blocks.

        Tool results live in user messages (role="user", content=[tool_result])
        and are NOT stripped by the runner, so we read them from
        ``_input_history`` after the iteration loop completes.
        """
        # Collect tool_results keyed by tool_use_id
        tool_results: dict[str, dict] = {}
        for msg in self._input_history:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                bt = getattr(block, "type", None)
                if isinstance(block, dict):
                    bt = block.get("type")
                if bt != "tool_result":
                    continue
                tui = getattr(block, "tool_use_id", None)
                if isinstance(block, dict):
                    tui = block.get("tool_use_id")
                if not tui:
                    continue
                raw_content = getattr(block, "content", "")
                if isinstance(block, dict):
                    raw_content = block.get("content", "")
                if not isinstance(raw_content, str):
                    raw_content = str(raw_content)
                is_err = getattr(block, "is_error", False)
                if isinstance(block, dict):
                    is_err = block.get("is_error", False)
                # Truncate for storage (full result is in tool_logs)
                if len(raw_content) > 800:
                    raw_content = raw_content[:800] + "\u2026"
                tool_results[tui] = {
                    "content": raw_content,
                    "is_error": bool(is_err),
                }

        # Build final list: inject tool_result after each tool_use,
        # and strip the temporary _tool_id field.
        final_blocks: list[dict] = []
        for block in self._current_turn_blocks:
            if block["type"] == "tool_use":
                tool_id = block.pop("_tool_id", "")
                final_blocks.append(block)
                if tool_id in tool_results:
                    final_blocks.append(
                        {
                            "type": "tool_result",
                            **tool_results[tool_id],
                        }
                    )
            else:
                final_blocks.append(block)

        return final_blocks

    def _capture_content_blocks(self) -> list[dict]:
        """Legacy fallback — extract content blocks from ``_input_history``.

        NOTE: This method misses intermediate thinking blocks because the
        BetaToolRunner strips them before they reach ``_input_history``.
        Prefer the incremental capture path (_capture_iteration_blocks +
        _finalize_turn_blocks) which is used by _generate_direct().
        """
        """Extract ordered content blocks for the current turn from runner history.

        Scans _input_history backwards from the end to find all messages
        belonging to the current turn (everything after our injected user
        message). Returns a flat list preserving the natural sequence:
        thinking → tool_use → tool_result → thinking → ... → text.
        """
        blocks: list[dict] = []

        # Find the start of current turn: last "real" user message
        # (tool_result messages also have role="user" but content is a list)
        start_idx = None
        for i in range(len(self._input_history) - 1, -1, -1):
            msg = self._input_history[i]
            if msg.get("role") == "user" and isinstance(msg.get("content"), str):
                start_idx = i + 1
                break
        if start_idx is None:
            return blocks

        # First pass: collect tool_results keyed by tool_use_id
        tool_results: dict[str, dict] = {}
        for msg in self._input_history[start_idx:]:
            if msg.get("role") != "user":
                continue
            content = msg.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                bt = getattr(block, "type", None)
                if isinstance(block, dict):
                    bt = block.get("type")
                if bt != "tool_result":
                    continue
                tui = getattr(block, "tool_use_id", None)
                if isinstance(block, dict):
                    tui = block.get("tool_use_id")
                if not tui:
                    continue
                raw_content = getattr(block, "content", "")
                if isinstance(block, dict):
                    raw_content = block.get("content", "")
                if not isinstance(raw_content, str):
                    raw_content = str(raw_content)
                is_err = getattr(block, "is_error", False)
                if isinstance(block, dict):
                    is_err = block.get("is_error", False)
                # Truncate for storage (full result is in tool_logs)
                if len(raw_content) > 800:
                    raw_content = raw_content[:800] + "\u2026"
                tool_results[tui] = {
                    "content": raw_content,
                    "is_error": bool(is_err),
                }

        # Second pass: build ordered blocks from assistant messages
        for msg in self._input_history[start_idx:]:
            if msg.get("role") != "assistant":
                continue
            content = msg.get("content", [])
            if isinstance(content, str):
                if content:
                    blocks.append({"type": "text", "text": content})
                continue
            if not isinstance(content, list):
                continue
            for block in content:
                bt = getattr(block, "type", None)
                if isinstance(block, dict):
                    bt = block.get("type")

                if bt == "thinking":
                    thinking_text = getattr(block, "thinking", "")
                    if isinstance(block, dict):
                        thinking_text = block.get("thinking", "")
                    blocks.append({"type": "thinking", "text": thinking_text})

                elif bt == "tool_use":
                    tool_id = getattr(block, "id", "")
                    tool_name = getattr(block, "name", "")
                    tool_input = getattr(block, "input", {})
                    if isinstance(block, dict):
                        tool_id = block.get("id", "")
                        tool_name = block.get("name", "")
                        tool_input = block.get("input", {})
                    blocks.append(
                        {
                            "type": "tool_use",
                            "name": tool_name,
                            "input": tool_input,
                        }
                    )
                    # Immediately follow with paired tool_result
                    if tool_id in tool_results:
                        blocks.append(
                            {
                                "type": "tool_result",
                                **tool_results[tool_id],
                            }
                        )

                elif bt == "text":
                    text_val = getattr(block, "text", "")
                    if isinstance(block, dict):
                        text_val = block.get("text", "")
                    if text_val:
                        blocks.append({"type": "text", "text": text_val})

        return blocks

    def get_content_blocks(self) -> dict[int, list[dict]]:
        """Return per-turn content blocks {turn_index: [blocks]}."""
        return dict(self._turn_content_blocks)

    def get_last_content_blocks(self) -> list[dict] | None:
        """Return content blocks for the most recently completed turn."""
        if self._turn_index == 0:
            return None
        return self._turn_content_blocks.get(self._turn_index - 1)

    # ==================================================================
    # SDK mode: Claude Agent SDK (ClaudeSDKClient) — API Key only
    # ==================================================================

    def _generate_sdk(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate response using Claude Agent SDK (API key mode).

        The SDK manages the agent loop autonomously via ClaudeSDKClient.
        We provide benchmark tools through an in-process MCP server.
        """
        # Prevent "cannot launch inside another Claude Code session" error
        os.environ.pop("CLAUDECODE", None)

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

        options_kwargs = dict(
            system_prompt=self.system_prompt,
            model=self.model,
            mcp_servers=mcp_servers,
            max_turns=self.max_agent_turns,
            permission_mode="bypassPermissions",
            disallowed_tools=disallowed_tools,
        )

        # Extended thinking in SDK mode (same switch as Direct API)
        if ANTHROPIC_ENABLE_THINKING:
            options_kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": ANTHROPIC_THINKING_BUDGET,
            }

        options = ClaudeAgentOptions(**options_kwargs)

        try:
            result = self._run_sync_sdk(prompt, options)
            return result if result else "[No response from Claude Agent SDK]"
        except Exception as e:
            return f"[Claude Agent SDK error: {e}]"

    def _build_prompt(self, messages: list[dict]) -> str:
        """Build a prompt string from conversation history (SDK mode).

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

    def _run_sync_sdk(self, prompt: str, options) -> str:
        """Run the async SDK query synchronously."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self._query_sdk(prompt, options))
                return future.result(timeout=120)
        else:
            return asyncio.run(self._query_sdk(prompt, options))

    async def _query_sdk(self, prompt: str, options) -> str:
        """Execute the Claude Agent SDK query and extract the result."""
        result_text = ""

        async with ClaudeSDKClient(options=options) as client:
            await client.query(prompt)
            msg_iter = client.receive_messages().__aiter__()
            while True:
                try:
                    message = await msg_iter.__anext__()
                except MessageParseError:
                    continue  # skip unknown message types
                except StopAsyncIteration:
                    break

                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock):
                            result_text = block.text
                    usage = getattr(message, "usage", None)
                    if usage:
                        inp = getattr(usage, "input_tokens", 0) or 0
                        out = getattr(usage, "output_tokens", 0) or 0
                        self._token_records.append(
                            TokenRecord(
                                model=self.model,
                                input_tokens=inp,
                                output_tokens=out,
                                cost_usd=estimate_cost(self.model, inp, out),
                            )
                        )
                elif isinstance(message, ResultMessage):
                    if hasattr(message, "result") and message.result:
                        result_text = message.result
                    break  # ResultMessage = end of response

        # Fallback: estimate from character count if SDK didn't expose usage
        if not any(r.model == self.model for r in self._token_records):
            est_inp = int(len(prompt) / 3.5)
            est_out = int(len(result_text) / 3.5)
            self._token_records.append(
                TokenRecord(
                    model=self.model,
                    input_tokens=est_inp,
                    output_tokens=est_out,
                    cost_usd=estimate_cost(self.model, est_inp, est_out),
                )
            )

        return result_text
