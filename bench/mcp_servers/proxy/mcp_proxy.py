"""MCP Proxy Layer for QuantTutorBench.

Intercepts all tool calls from the Agent Under Test, logs them,
forwards to real implementations, logs results, and returns to agent.
"""

import re
import time
from dataclasses import dataclass
from typing import Callable, Optional

# Pattern to detect non-zero exit codes appended by shell_exec wrappers
_EXIT_CODE_RE = re.compile(r"\[exit code\]:\s*(-?\d+)")


@dataclass
class ToolCallLog:
    """Record of a single tool call."""

    name: str
    args: dict
    result: str = ""
    timestamp: float = 0.0
    duration_ms: float = 0.0
    success: bool = True
    turn_index: int = 0


class MCPProxy:
    """Transparent proxy that logs all tool calls.

    Usage:
        proxy = MCPProxy()
        proxy.register_tool("shell_exec", shell_exec_func)
        proxy.register_distractor("search_web", "Error: No network")

        # Agent calls tool through proxy:
        result = proxy.call_tool("shell_exec", command="ls", timeout=10)

        # After conversation, get all logs:
        logs = proxy.get_logs()
    """

    def __init__(self):
        self._tools: dict[str, Callable] = {}
        self._distractors: dict[str, str] = {}
        self._tool_schemas: dict[str, dict] = {}
        self._logs: list[ToolCallLog] = []
        self._current_turn: int = 0

    def register_tool(
        self,
        name: str,
        func: Callable,
        description: str = "",
        params: Optional[dict] = None,
    ):
        """Register a real tool implementation."""
        self._tools[name] = func
        self._tool_schemas[name] = {
            "name": name,
            "description": description,
            "parameters": params or {},
        }

    def register_distractor(
        self,
        name: str,
        error_message: str = "",
        description: str = "",
        params: Optional[dict] = None,
        func: Optional[Callable] = None,
    ):
        """Register a distractor tool.

        Distractors come in two flavours:

        - **Error-based** (legacy): always returns ``error_message``.
        - **Functional**: has a real ``func`` that returns plausible
          results so the agent cannot distinguish it from core tools
          by simply calling it once.

        In both cases the tool is tracked in ``_distractors`` so that
        ``get_distractor_names()`` can identify it for evaluation.
        """
        self._distractors[name] = error_message
        if func is not None:
            self._tools[name] = func
        self._tool_schemas[name] = {
            "name": name,
            "description": description,
            "parameters": params or {},
        }

    def set_turn(self, turn_index: int):
        """Set the current conversation turn index."""
        self._current_turn = turn_index

    def get_available_tools(self) -> list[dict]:
        """Get the list of all available tools (core + distractors) for the agent."""
        return list(self._tool_schemas.values())

    def call_tool(self, name: str, **kwargs) -> str:
        """Call a tool through the proxy, logging the interaction."""
        start_time = time.time()
        timestamp = start_time

        log = ToolCallLog(
            name=name, args=kwargs, timestamp=timestamp, turn_index=self._current_turn
        )

        try:
            if name in self._distractors:
                if name in self._tools:
                    # Functional distractor — runs real code, returns
                    # plausible result.  Agent sees normal output.
                    result = self._tools[name](**kwargs)
                    log.success = True
                else:
                    # Error-based distractor (legacy) — returns error
                    result = self._distractors[name]
                    log.success = False
            elif name in self._tools:
                result = self._tools[name](**kwargs)
                log.success = True
            else:
                result = f"Error: Unknown tool '{name}'"
                log.success = False

            log.result = str(result)

            # Detect non-zero exit codes from shell_exec (the wrapper appends
            # "[exit code]: N" when returncode != 0).  Mark as failed so trace
            # reports and tool_usage effectiveness scoring see the truth.
            if log.success and log.name == "shell_exec":
                m = _EXIT_CODE_RE.search(log.result)
                if m and int(m.group(1)) != 0:
                    log.success = False
        except Exception as e:
            log.result = f"Error: {type(e).__name__}: {str(e)}"
            log.success = False

        log.duration_ms = (time.time() - start_time) * 1000
        self._logs.append(log)

        return log.result

    def get_logs(self) -> list[ToolCallLog]:
        """Get all recorded tool call logs."""
        return list(self._logs)

    def get_distractor_names(self) -> list[str]:
        """Get names of all registered distractor tools."""
        return list(self._distractors.keys())
