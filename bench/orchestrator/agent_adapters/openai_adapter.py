"""OpenAI adapter for QuantTutorBench.

Supports two modes controlled by OPENAI_USE_DIRECT_API:

  - SDK mode (False): OpenAI Agents SDK — Runner.run_sync() manages the
    agent loop as a black box. Requires openai-agents package.

  - Direct API mode (True): OpenAI Chat Completions API with a while loop.
    GPT autonomously decides all tool calls; we implement only the mechanical
    transport loop. Visible intermediate reasoning, per-turn token tracking.

Both modes route tool calls through the benchmark's MCPProxy for logging.

Supports both native OpenAI API and OpenRouter routing (via base_url).

Install (SDK mode): pip install openai-agents
Install (direct mode): pip install openai
"""

import asyncio
import json
import logging
import os
import threading
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from .base_adapter import (
    BaseAgentAdapter,
    ensure_str,
    extract_latest_user_message,
    normalize_tool_params,
    record_token_usage,
)
from .prompts import TUTOR_SYSTEM_PROMPT

_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

from config.llm_config import (
    OPENAI_AGENT_MODEL,
    OPENAI_ENABLE_REASONING,
    OPENAI_REASONING_EFFORT,
    OPENAI_USE_DIRECT_API,
)

log = logging.getLogger(__name__)

# --- OpenAI Agents SDK imports (SDK mode only) ---
try:
    from agents import Agent, MaxTurnsExceeded, RunConfig, Runner
    from agents.model_settings import ModelSettings
    from agents.tool import FunctionTool

    OPENAI_AGENTS_AVAILABLE = True
except ImportError:
    OPENAI_AGENTS_AVAILABLE = False

# Max LLM invocations per generate_response() call.
DEFAULT_AGENT_MAX_TURNS = 16

# --- Direct API: history compaction ---
# Mirrors Anthropic BetaToolRunner's compaction_control.
# When total prompt_tokens in the last API call exceeds this threshold,
# the history is summarized and replaced to prevent O(n²) token growth.
COMPACTION_TOKEN_THRESHOLD = 100_000

COMPACTION_SUMMARY_PROMPT = (
    "You have been tutoring a student. Summarize the conversation so far "
    "into a concise continuation context that allows you to resume "
    "naturally.  Include:\n"
    "1. Task Overview — the student's learning goal and current topic\n"
    "2. Current State — what has been completed, files created, key "
    "tool outputs\n"
    "3. Important Discoveries — constraints, decisions, errors resolved\n"
    "4. Next Steps — what remains, open questions\n"
    "Be concise but complete — preserve all info needed to continue "
    "without repeating work."
)


class OpenAIAgentAdapter(BaseAgentAdapter):
    """Adapter for OpenAI agent under test.

    Two modes:
      - SDK (OPENAI_USE_DIRECT_API=False): Agents SDK Runner (black-box loop)
      - Direct API (OPENAI_USE_DIRECT_API=True): Chat Completions with while loop

    In both modes, GPT autonomously decides which tools to call and when.
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

        # Persistent state across conversation turns (both modes)
        self._agent = None
        self._input_history: list = []
        self._tool_callback = None
        self._direct_client = None  # lazy-init OpenAI client for Direct API mode
        self._model_obj = None
        self._async_client = None

        if base_url:
            self.api_key = (
                api_key
                or os.environ.get("OPENROUTER_API_KEY", "")
                or os.environ.get("OPENAI_API_KEY", "")
            )
        else:
            self.api_key = api_key or os.environ.get("OPENAI_API_KEY", "")

        if not self.api_key:
            key_name = "OPENROUTER_API_KEY" if base_url else "OPENAI_API_KEY"
            raise ValueError(f"{key_name} not set in .env")

        if not base_url:
            os.environ["OPENAI_API_KEY"] = self.api_key
            os.environ.pop("OPENAI_BASE_URL", None)

        # Validate SDK availability for SDK mode
        if not OPENAI_USE_DIRECT_API and not OPENAI_AGENTS_AVAILABLE:
            raise ImportError(
                "openai-agents required for SDK mode. "
                "pip install openai-agents  OR  set OPENAI_USE_DIRECT_API=True"
            )

    def set_task_context(self, context: str):
        """Override: also clear agent and conversation state."""
        super().set_task_context(context)
        self._agent = None
        self._input_history = []
        self._tool_callback = None

    def reset(self):
        """Reset internal state between tasks."""
        super().reset()
        self._input_history = []
        self._agent = None
        self._tool_callback = None

    def set_agent_max_steps(self, n: int):
        """Limit how many LLM→tool→LLM cycles per generate_response() call."""
        self.max_turns = n

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Route to direct API or SDK path based on OPENAI_USE_DIRECT_API."""
        if OPENAI_USE_DIRECT_API:
            return self._generate_direct(messages, available_tools, tool_callback)
        else:
            return self._generate_sdk(messages, available_tools, tool_callback)

    # ==================================================================
    # SDK model management
    # ==================================================================

    def _get_or_create_model(self):
        """Lazy-init SDK model with an adapter-owned AsyncOpenAI client.

        Use an explicit AsyncOpenAI client even for native OpenAI so the
        adapter owns the resource lifecycle and can close it deterministically
        after each benchmark task.
        """
        if self._model_obj is None:
            from agents.models.openai_chatcompletions import (
                OpenAIChatCompletionsModel,
            )
            from openai import AsyncOpenAI

            client_kwargs = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._async_client = AsyncOpenAI(**client_kwargs)
            self._model_obj = OpenAIChatCompletionsModel(
                model=self.model,
                openai_client=self._async_client,
            )
        return self._model_obj

    def close(self):
        """Release async client resources used by the Agents SDK."""
        self._agent = None
        self._input_history = []
        self._tool_callback = None
        self._task_context = ""
        self._model_obj = None

        client = self._async_client
        self._async_client = None
        if client is None:
            return

        try:
            asyncio.run(client.close())
        except RuntimeError:
            close_error: list[Exception] = []

            def _close_in_thread():
                try:
                    asyncio.run(client.close())
                except Exception as exc:  # pragma: no cover - defensive cleanup
                    close_error.append(exc)

            thread = threading.Thread(target=_close_in_thread, daemon=True)
            thread.start()
            thread.join()
            if close_error:
                raise close_error[0]

    # ==================================================================
    # Direct API mode: Chat Completions with tool_calls loop
    # ==================================================================

    def _get_direct_client(self):
        """Lazy-init and reuse OpenAI client for Direct API mode."""
        if self._direct_client is None:
            from openai import OpenAI

            client_kwargs = {"api_key": self.api_key}
            if self.base_url:
                client_kwargs["base_url"] = self.base_url
            self._direct_client = OpenAI(**client_kwargs)
        return self._direct_client

    def _generate_direct(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate response via OpenAI Chat Completions API.

        GPT autonomously decides tool calls. We implement only the
        mechanical transport loop: send → execute tool_calls → return results.
        Cross-turn context is preserved via _input_history (includes
        tool_calls and tool results from previous turns).
        """
        from config.pricing import estimate_cost

        new_user_msg = extract_latest_user_message(messages)
        if new_user_msg is None:
            return ""

        self._input_history.append({"role": "user", "content": new_user_msg})

        client = self._get_direct_client()
        tools = self.format_tools_openai(available_tools) if available_tools else None

        # Build from persistent history (preserves tool_calls/tool results)
        api_messages = [{"role": "system", "content": self._get_full_system_prompt()}]
        api_messages.extend(self._input_history)

        text_parts: list[str] = []
        turns = 0
        last_prompt_tokens = 0

        try:
            while turns < self.max_turns:
                turns += 1

                create_kwargs = dict(
                    model=self.model,
                    messages=api_messages,
                    tools=tools,
                    max_tokens=8192,
                )
                if OPENAI_ENABLE_REASONING:
                    create_kwargs["reasoning_effort"] = OPENAI_REASONING_EFFORT

                response = client.chat.completions.create(**create_kwargs)

                # --- Token tracking ---
                usage = getattr(response, "usage", None)
                if usage:
                    last_prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                record_token_usage(
                    self._token_records,
                    self.model,
                    usage,
                    cost_fn=estimate_cost,
                )

                message = response.choices[0].message

                # Accumulate text fragments across tool-call iterations.
                # Models like GPT-5.2 often return content=None during tool
                # rounds and only produce text in the final response.
                if message.content:
                    text_parts.append(ensure_str(message.content))

                # Append ALL assistant messages (intermediate + final)
                api_messages.append(message.model_dump(exclude_none=True))

                # No tool calls → GPT decided to stop
                if not message.tool_calls or not tool_callback:
                    break

                # --- Execute tools (through MCPProxy) ---
                for tc in message.tool_calls:
                    args = (
                        json.loads(tc.function.arguments)
                        if tc.function.arguments
                        else {}
                    )
                    try:
                        result = tool_callback(tc.function.name, **args)
                        api_messages.append(
                            {
                                "tool_call_id": tc.id,
                                "role": "tool",
                                "content": str(result),
                            }
                        )
                    except Exception as e:
                        api_messages.append(
                            {
                                "tool_call_id": tc.id,
                                "role": "tool",
                                "content": f"Error: {e}",
                            }
                        )
            else:
                log.warning("Direct API: max_turns (%d) reached", self.max_turns)
                # When max_turns exhausted with no text captured, make one
                # more call WITH tools so GPT can properly structure any
                # remaining tool-call intent (omitting tools= causes GPT
                # to leak raw JSON fragments into the text content).
                if not text_parts:
                    log.info("Direct API: forcing summary call after tool loop")
                    try:
                        # Append a nudge so the model produces a text summary
                        # instead of requesting more tool calls.
                        summary_messages = list(api_messages) + [
                            {
                                "role": "user",
                                "content": (
                                    "[SYSTEM: You have used all available tool-call rounds. "
                                    "Please respond to the student NOW with a helpful text "
                                    "message summarizing what you accomplished and any "
                                    "guidance. Do NOT call any more tools.]"
                                ),
                            }
                        ]
                        force_kwargs: dict = dict(
                            model=self.model,
                            messages=summary_messages,
                            max_tokens=4096,
                        )
                        # Omit tools to guarantee a text response.
                        force_resp = client.chat.completions.create(**force_kwargs)
                        force_msg = force_resp.choices[0].message
                        if force_msg.content:
                            text_parts.append(ensure_str(force_msg.content))
                        # Process any final tool calls from the forced response
                        if force_msg.tool_calls and tool_callback:
                            api_messages.append(force_msg.model_dump(exclude_none=True))
                            for tc in force_msg.tool_calls:
                                args = (
                                    json.loads(tc.function.arguments)
                                    if tc.function.arguments
                                    else {}
                                )
                                try:
                                    result = tool_callback(tc.function.name, **args)
                                    api_messages.append(
                                        {
                                            "tool_call_id": tc.id,
                                            "role": "tool",
                                            "content": str(result),
                                        }
                                    )
                                except Exception:
                                    pass
                        else:
                            api_messages.append(force_msg.model_dump(exclude_none=True))
                        record_token_usage(
                            self._token_records,
                            self.model,
                            getattr(force_resp, "usage", None),
                            cost_fn=estimate_cost,
                        )
                    except Exception as exc:
                        log.warning("Direct API: forced text call failed: %s", exc)

            # --- History compaction (mirrors Anthropic BetaToolRunner) ---
            # When context grows too large, summarize and replace history
            # to prevent O(n²) token growth across conversation turns.
            if last_prompt_tokens > COMPACTION_TOKEN_THRESHOLD:
                api_messages = self._compact_history(client, api_messages)

            # Persist full history (skip system message)
            self._input_history = api_messages[1:]

            result_text = "\n\n".join(text_parts)
            return result_text if result_text else "[No response from OpenAI API]"

        except Exception as e:
            log.error(f"OpenAI direct API error: {e}")
            return f"[OpenAI API error: {e}]"

    def _compact_history(self, client, api_messages: list[dict]) -> list[dict]:
        """Summarize and replace message history to cap context growth.

        Mirrors Anthropic BetaToolRunner's compaction_control: sends the
        full history to the LLM with a summarization prompt, then replaces
        the conversation body with the summary.  The system message
        (api_messages[0]) is always preserved.

        Returns a new api_messages list: [system, assistant_summary].
        """
        log.info(
            "Compacting history (%d messages) — token threshold exceeded.",
            len(api_messages),
        )
        try:
            summary_messages = list(api_messages)  # copy
            summary_messages.append(
                {"role": "user", "content": COMPACTION_SUMMARY_PROMPT}
            )
            summary_resp = client.chat.completions.create(
                model=self.model,
                messages=summary_messages,
                max_tokens=2048,
            )
            summary_text = summary_resp.choices[0].message.content or ""

            from config.pricing import estimate_cost

            record_token_usage(
                self._token_records,
                self.model,
                getattr(summary_resp, "usage", None),
                cost_fn=estimate_cost,
            )

            log.info("Compaction complete (%d chars summary).", len(summary_text))

            # Replace history: keep system message + summary as assistant
            return [
                api_messages[0],  # system prompt
                {"role": "assistant", "content": summary_text},
            ]
        except Exception as e:
            log.warning("Compaction failed (%s), keeping full history.", e)
            return api_messages

    # ==================================================================
    # SDK mode: OpenAI Agents SDK (Runner.run_sync) — black-box
    # ==================================================================

    def _generate_sdk(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate response using OpenAI Agents SDK.

        The SDK's Runner manages the agent loop as a black box.
        Agent persists across conversation turns via _input_history.
        """
        if not OPENAI_AGENTS_AVAILABLE:
            return "[Error: openai-agents package not available]"

        try:
            self._tool_callback = tool_callback
            agent = self._get_or_create_agent(available_tools)

            new_user_msg = extract_latest_user_message(messages)
            if new_user_msg is None:
                return ""

            self._input_history.append({"role": "user", "content": new_user_msg})

            result = Runner.run_sync(
                agent,
                self._input_history,
                max_turns=self.max_turns,
                run_config=RunConfig(
                    tracing_disabled=bool(self.base_url),
                    trace_include_sensitive_data=False,
                ),
            )

            self._input_history = result.to_input_list()
            self._record_usage_from_run_result(result)
            return result.final_output or ""

        except MaxTurnsExceeded:
            if hasattr(self, "_input_history") and self._input_history:
                for item in reversed(self._input_history):
                    if isinstance(item, dict) and item.get("role") == "assistant":
                        content = item.get("content", "")
                        if content:
                            return content
            return (
                "I've been working through several steps to help you with this. "
                "Let me summarize what we've covered so far and continue from here."
            )
        except Exception as e:
            log.error(f"Agent SDK error: {e}")
            return (
                "I encountered a technical issue. Let me try a different approach "
                "to help you with your question."
            )

    def _dynamic_instructions(self, ctx, agent):
        """Dynamic instructions callable for the Agent (SDK mode)."""
        return self._get_full_system_prompt()

    def _get_or_create_agent(self, available_tools: list[dict]) -> "Agent":
        """Create the Agent once per task, reuse across turns (SDK mode)."""
        if self._agent is None:
            agent_tools = self._build_agent_tools(available_tools)
            model_settings_kwargs = {"tool_choice": "auto"}
            if OPENAI_ENABLE_REASONING:
                from openai.types.shared import Reasoning

                model_settings_kwargs["reasoning"] = Reasoning(
                    effort=OPENAI_REASONING_EFFORT
                )

            self._agent = Agent(
                name="quant_tutor",
                instructions=self._dynamic_instructions,
                model=self._get_or_create_model(),
                tools=agent_tools,
                model_settings=ModelSettings(**model_settings_kwargs),
            )
        return self._agent

    def _build_agent_tools(self, available_tools: list[dict]) -> list:
        """Build OpenAI Agent SDK FunctionTool objects (SDK mode)."""
        if not OPENAI_AGENTS_AVAILABLE:
            return []

        agent_tools = []
        for tool_schema in available_tools:
            tool_name = tool_schema["name"]
            tool_desc = tool_schema.get("description", f"Tool: {tool_name}")

            def make_tool_fn(name, adapter_ref):
                async def tool_fn(ctx, args_json: str) -> str:
                    args = json.loads(args_json) if args_json else {}
                    result = adapter_ref._tool_callback(name, **args)
                    return str(result)

                return tool_fn

            fn = make_tool_fn(tool_name, self)

            properties, required_list = normalize_tool_params(
                tool_schema.get("parameters", {})
            )
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

    def _record_usage_from_run_result(self, result):
        """Extract token usage from OpenAI Agents SDK RunResult."""
        from config.pricing import estimate_cost

        for raw_resp in getattr(result, "raw_responses", []):
            record_token_usage(
                self._token_records,
                self.model,
                getattr(raw_resp, "usage", None),
                input_attr="input_tokens",
                output_attr="output_tokens",
                cost_fn=estimate_cost,
            )

    # ==================================================================
    # Shared utilities
    # ==================================================================
