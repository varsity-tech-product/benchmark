"""Anthropic adapter for QuantTutorBench.

Two independent switches control behavior:

  ANTHROPIC_USE_SDK (transport):
    True  → Claude Agent SDK (ClaudeSDKClient) — black-box agent loop.
            Requires ANTHROPIC_API_KEY.
    False → Anthropic Python SDK BetaToolRunner — automatic tool loop with
            cross-turn context persistence, parallel tool execution,
            prompt caching, and extended thinking support.


Both modes route tool calls through the benchmark's MCPProxy for logging.
Claude autonomously decides all tool calls in every mode.
"""

import asyncio
import logging
import os
import time
from typing import Optional

from .base_adapter import (
    BaseAgentAdapter,
    TokenRecord,
    normalize_tool_params,
    record_token_usage,
)
from .config import (
    AGENT_USE_OPENROUTER,
    ANTHROPIC_AGENT_MODEL,
    ANTHROPIC_AGENT_MODEL_OR,
    ANTHROPIC_ENABLE_THINKING,
    ANTHROPIC_THINKING_BUDGET,
    ANTHROPIC_USE_SDK,
    OPENROUTER_ANTHROPIC_BASE_URL,
    estimate_cost,
)
from .prompts import CLEAN_SYSTEM_PROMPT

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
        input_schema = self._schema.get("input_schema")
        if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
            properties, required = normalize_tool_params(
                self._schema.get("parameters", {})
            )
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
        if isinstance(input, dict):
            kwargs = input
        elif hasattr(input, "model_dump"):
            kwargs = input.model_dump()
        elif hasattr(input, "__dict__"):
            kwargs = {k: v for k, v in input.__dict__.items() if not k.startswith("_")}
        else:
            kwargs = dict(input) if input else {}
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
        input_schema = tool_schema.get("input_schema")
        if not isinstance(input_schema, dict) or input_schema.get("type") != "object":
            properties, required = normalize_tool_params(
                tool_schema.get("parameters", {})
            )
            input_schema = {"type": "object", "properties": properties}
            if required:
                input_schema["required"] = required

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
      False → Anthropic Python SDK BetaToolRunner (automatic tool loop)

      False → API key (x-api-key header)

    In all modes, Claude autonomously decides which tools to call and when.
    """

    def __init__(
        self,
        model: str = ANTHROPIC_AGENT_MODEL,
        system_prompt: str = "",
        agent_name: str = "claude_agent_sdk",
        max_agent_turns: int = 200,
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.system_prompt = system_prompt or CLEAN_SYSTEM_PROMPT
        self.max_agent_turns = max_agent_turns

        self._thinking_trace: list[dict] = []  # COT blocks from extended thinking
        self._turn_index: int = 0  # conversation turn counter for thinking trace
        self._turn_content_blocks: dict[int, list[dict]] = {}  # per-turn content blocks
        self._current_turn_blocks: list[dict] = (
            []
        )  # incremental capture during iteration
        self._captured_tool_results: dict[str, dict] = (
            {}
        )  # tool_use_id → {content, is_error}, captured incrementally
        self._client: object | None = None  # anthropic.Anthropic instance

        if ANTHROPIC_USE_SDK:
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
            if AGENT_USE_OPENROUTER:
                # OpenRouter Anthropic Skin: transparent proxy to Anthropic API.
                # Base URL /api (SDK appends /v1/messages → /api/v1/messages).
                # Supports tool_runner, thinking blocks, native tool use.
                api_key = os.environ.get("OPENROUTER_API_KEY", "")
                if not api_key:
                    raise ValueError("OPENROUTER_API_KEY not set in .env")
                self.model = (
                    ANTHROPIC_AGENT_MODEL_OR  # OR format: "anthropic/claude-..."
                )
                self._client = anthropic.Anthropic(
                    auth_token=api_key,  # Bearer header (OpenRouter Anthropic Skin)
                    base_url=OPENROUTER_ANTHROPIC_BASE_URL,
                )
                self._client.api_key = None  # suppress x-api-key header
                self._betas = []
            else:
                api_key = os.environ.get("ANTHROPIC_API_KEY", "")
                if not api_key:
                    raise ValueError("ANTHROPIC_API_KEY not set in .env")
                self._client = anthropic.Anthropic(api_key=api_key)
                self._betas = []

    def set_task_context(self, context: str):
        """Override: clear state for a new task."""
        super().set_task_context(context)

    def reset(self):
        """Reset internal state between tasks."""
        super().reset()
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

        The SDK manages the full agent loop autonomously:
        - Auto-executes tools via DynamicTool.call() (including send_message)
        - Parallel tool execution when Claude emits multiple tool_use
        - Built-in compaction and context management
        - Prompt caching and rate-limit retry built-in

        Called once per session — the entire multi-turn tutoring conversation
        runs inside a single BetaToolRunner invocation.
        """
        try:
            # Build auto-executable tool objects
            tools = (
                _build_runner_tools(available_tools, tool_callback)
                if available_tools and tool_callback
                else []
            )

            # Create BetaToolRunner — SDK manages the full session
            runner_kwargs = dict(
                model=self.model,
                max_tokens=16384,
                messages=list(messages),
                system=self._get_full_system_prompt(),
                max_iterations=self.max_agent_turns,
            )
            if tools:
                runner_kwargs["tools"] = tools
            if self._betas:
                runner_kwargs["betas"] = self._betas

            # Enable automatic compaction when context exceeds threshold.
            # Default SDK threshold is 100K tokens — far too late for
            # strategy tasks where 10 iterations of shell_exec + plot_chart
            # generate 30-40K tokens of tool_use blocks alone.  Lowering to
            # 40K triggers summarization after ~6 iterations, preventing
            # O(n²) token growth within a single tool_runner burst.
            runner_kwargs["compaction_control"] = {
                "enabled": True,
                "context_token_threshold": 40_000,
            }

            # Automatically clear old tool_use/tool_result blocks and
            # thinking blocks during the tool loop.  This keeps the
            # context lean: only the most recent 6 tool calls remain in
            # full, older ones are discarded (their results are already
            # reflected in the agent's own text and workspace files).
            # Thinking blocks are captured by _extract_thinking() before
            # being cleared, so no information is lost.
            runner_kwargs["context_management"] = {
                "edits": [
                    {
                        "type": "clear_thinking_20251015",
                        "keep": {"type": "thinking_turns", "value": 1},
                    },
                    {
                        "type": "clear_tool_uses_20250919",
                        "keep": {"type": "tool_uses", "value": 6},
                    },
                ]
            }

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
            self._captured_tool_results = {}
            text_parts: list[str] = []  # Accumulate text from ALL iterations
            _runner_crashed = False
            _iter_t0 = time.time()
            for message in runner:
                # Cancel check: exit runner loop immediately
                if self._cancel_event is not None and self._cancel_event.is_set():
                    log.info(
                        "Cancel detected in runner loop at iteration %d, breaking",
                        iteration,
                    )
                    break
                final_message = message
                iteration += 1
                # [DIAG] Log iteration timing and content types
                block_types = [getattr(b, "type", "?") for b in message.content]
                _iter_elapsed = time.time() - _iter_t0
                _thinking_len = sum(
                    len(getattr(b, "thinking", "") or "")
                    for b in message.content
                    if getattr(b, "type", None) == "thinking"
                )
                _text_len = sum(
                    len(getattr(b, "text", "") or "")
                    for b in message.content
                    if getattr(b, "type", None) == "text"
                )
                _usage = getattr(message, "usage", None)
                _in_tok = getattr(_usage, "input_tokens", "?") if _usage else "?"
                _out_tok = getattr(_usage, "output_tokens", "?") if _usage else "?"
                print(
                    f"    [iter {iteration}] {_iter_elapsed:.1f}s "
                    f"blocks={block_types} "
                    f"thinking={_thinking_len}ch text={_text_len}ch "
                    f"tokens={_in_tok}in/{_out_tok}out",
                    flush=True,
                )
                _iter_t0 = time.time()

                self._record_message_usage(message)
                self._extract_thinking(message, iteration)
                self._capture_iteration_blocks(message)
                # Incrementally capture tool_results from runner messages
                # BEFORE context_management clears them in the next iteration.
                self._scan_runner_tool_results(runner)

                # Progress events — client does not use live_monitor.
                # (Legacy: orchestrator.live_monitor.emit)

                # Collect text blocks from every iteration, not just the last.
                # Without this, intermediate explanations (concept intro, code
                # walkthrough, progress notes) are lost — only the final
                # results summary would survive.
                for block in message.content:
                    if hasattr(block, "text") and block.text.strip():
                        text_parts.append(block.text)

            # Final scan: capture tool_results from the last iteration
            # (tools execute AFTER yield, so the last iteration's results
            # only appear in messages after the loop exits).
            try:
                self._scan_runner_tool_results(runner)
            except Exception:
                pass

            # Finalize content blocks: inject tool_results from history into
            # the incrementally captured blocks.
            self._turn_content_blocks[self._turn_index] = self._finalize_turn_blocks()
            self._turn_index += 1

            # Join all text parts across iterations into one response.
            result_text = "\n\n".join(text_parts)

            # Fallback: generate a text summary when the runner's final
            # message lacks a substantive text conclusion.  This covers:
            #   (a) Final message is pure tool_use with no text at all
            #   (b) Final message has only transition text (< 100 chars like
            #       "Now the charts:") followed by tool_use — the student
            #       would see the transition but no summary of tool results.
            last_needs_summary = False
            if final_message is not None:
                fm_text_chars = sum(
                    len(b.text)
                    for b in final_message.content
                    if hasattr(b, "text") and b.text.strip()
                )
                fm_has_tool = any(
                    getattr(b, "type", None) == "tool_use"
                    for b in final_message.content
                )
                if fm_text_chars == 0:
                    last_needs_summary = True
                elif fm_has_tool and fm_text_chars < 100:
                    # Trivial transition text followed by tool calls
                    last_needs_summary = True
            if not result_text or last_needs_summary:
                try:
                    log.warning(
                        "BetaToolRunner ended with tool_use (no final text). "
                        "Making fallback summary call (existing text: %d chars).",
                        len(result_text),
                    )
                    # When thinking is enabled, max_tokens must exceed
                    # budget_tokens to leave room for actual text output.
                    fallback_max = (
                        ANTHROPIC_THINKING_BUDGET + 4096
                        if ANTHROPIC_ENABLE_THINKING
                        else 4096
                    )
                    # Use runner's current message history for fallback context
                    try:
                        fallback_messages = list(runner._params["messages"])
                    except Exception:
                        fallback_messages = list(messages)
                    summary_kwargs = dict(
                        model=self.model,
                        max_tokens=fallback_max,
                        system=self._get_full_system_prompt(),
                        messages=fallback_messages,
                    )
                    if self._betas:
                        summary_kwargs["betas"] = self._betas
                    if ANTHROPIC_ENABLE_THINKING:
                        summary_kwargs["thinking"] = {
                            "type": "enabled",
                            "budget_tokens": ANTHROPIC_THINKING_BUDGET,
                        }
                    summary_msg = self._client.beta.messages.create(**summary_kwargs)
                    fallback_text = ""
                    for block in summary_msg.content:
                        if hasattr(block, "text"):
                            fallback_text += block.text
                    self._record_message_usage(summary_msg)
                    # Append fallback to existing text with separator
                    if fallback_text:
                        if result_text:
                            result_text = result_text + "\n\n" + fallback_text
                        else:
                            result_text = fallback_text
                except Exception as fallback_exc:
                    log.error("Fallback summary call failed: %s", fallback_exc)

            return result_text if result_text else "[No response from Anthropic API]"

        except Exception as e:
            log.error("BetaToolRunner error: %s", e, exc_info=True)
            # Recover accumulated text from iterations completed before crash.
            # Without this, a mid-loop API error discards all prior work.
            if text_parts:
                log.warning(
                    "Recovering %d chars of text from %d iterations before crash.",
                    sum(len(t) for t in text_parts),
                    iteration,
                )
                self._turn_index += 1
                return "\n\n".join(text_parts)
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
        cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
        cache_create = getattr(usage, "cache_creation_input_tokens", 0) or 0
        record_token_usage(
            self._token_records,
            self.model,
            usage,
            input_attr="input_tokens",
            output_attr="output_tokens",
            extra_input=cache_read + cache_create,
            cost_fn=estimate_cost,
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

    def get_thinking_trace(self) -> list[dict]:
        """Return accumulated thinking/COT blocks from all iterations."""
        return list(self._thinking_trace)

    # ------------------------------------------------------------------
    # Content blocks capture (for web UI inline display)
    # ------------------------------------------------------------------

    def _scan_runner_tool_results(self, runner) -> None:
        """Incrementally capture tool_results from runner._params['messages'].

        Called during each iteration AND once after the loop exits.
        Captures tool_results before context_management clears them in
        subsequent API calls.  Idempotent — already-captured results are
        skipped via the _captured_tool_results dict.

        Wrapped in try/except so capture failures never affect the main
        agent loop or result recording.
        """
        try:
            messages = runner._params.get("messages", [])
            for msg in messages:
                if not isinstance(msg, dict) or msg.get("role") != "user":
                    continue
                content = msg.get("content", [])
                if not isinstance(content, list):
                    continue
                for block in content:
                    # Handle both SDK objects and plain dicts
                    if isinstance(block, dict):
                        bt = block.get("type")
                        tui = block.get("tool_use_id")
                        raw = block.get("content", "")
                        is_err = block.get("is_error", False)
                    else:
                        bt = getattr(block, "type", None)
                        tui = getattr(block, "tool_use_id", None)
                        raw = getattr(block, "content", "")
                        is_err = getattr(block, "is_error", False)

                    if bt != "tool_result" or not tui:
                        continue
                    if tui in self._captured_tool_results:
                        continue  # already captured

                    if not isinstance(raw, str):
                        raw = str(raw)
                    if len(raw) > 800:
                        raw = raw[:800] + "\u2026"
                    self._captured_tool_results[tui] = {
                        "content": raw,
                        "is_error": bool(is_err),
                    }
        except Exception as exc:
            log.debug("_scan_runner_tool_results: %s (non-fatal)", exc)

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
        """Inject tool_results into incrementally captured blocks.

        Uses ``_captured_tool_results`` (populated by ``_scan_runner_tool_results``
        during each iteration).  This ensures tool_results cleared by
        context_management are still available.
        """
        tool_results = dict(self._captured_tool_results)

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
            system_prompt=self._get_full_system_prompt(),
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
        text_parts: list[str] = []

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
                            if block.text.strip():
                                text_parts.append(block.text)
                    record_token_usage(
                        self._token_records,
                        self.model,
                        getattr(message, "usage", None),
                        input_attr="input_tokens",
                        output_attr="output_tokens",
                        cost_fn=estimate_cost,
                    )
                elif isinstance(message, ResultMessage):
                    if hasattr(message, "result") and message.result:
                        text_parts.append(message.result)
                    break  # ResultMessage = end of response

        result_text = "\n\n".join(text_parts)
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
