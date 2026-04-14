"""MCP Proxy Layer for QuantTutorBench.

Intercepts all tool calls from the Agent Under Test, logs them,
forwards to real implementations, logs results, and returns to agent.
"""

import os
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


def _emit(event_type: str, data: dict):
    """Lazy wrapper for live_monitor.emit() to avoid import-time failures."""
    try:
        from orchestrator.live_monitor import emit

        emit(event_type, data)
    except ImportError:
        pass


# Pattern to detect non-zero exit codes appended by shell_exec wrappers
_EXIT_CODE_RE = re.compile(r"\[exit code\]:\s*(-?\d+)")

# Pattern to extract image file paths from tool results
_IMAGE_PATH_RE = re.compile(
    r"(?:saved to|wrote|created|generated|output:?)\s+(/\S+\.(?:png|jpg|jpeg|svg|gif))",
    re.IGNORECASE,
)

# Maximum characters to store in log.result and return to the agent.
# Prevents oversized tool outputs from bloating traces and consuming
# agent context.  Each tool result becomes input tokens for the NEXT
# LLM call, so keeping this tight reduces O(n²) context growth.
# 12K chars ≈ 3K tokens — sufficient for any single tool's useful output.
_MAX_LOG_RESULT = 12_000


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
    output_files: list = field(default_factory=list)  # image filenames produced


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

    # Tools exempt from per-turn step counting.
    _STEP_EXEMPT_TOOLS = frozenset(
        {
            "send_message",
            "get_session_info",
            "get_environment_info",
            "list_tasks",
            "get_task_info",
            "get_system_prompt",
            "register_session",
        }
    )

    def __init__(self, workspace_path: Optional[str] = None):
        self._tools: dict[str, Callable] = {}
        self._distractors: dict[str, str] = {}
        self._tool_schemas: dict[str, dict] = {}
        self._logs: list[ToolCallLog] = []
        self._current_turn: int = 0
        self._deadline: Optional[float] = None
        self._cancel_event = None
        # Host-side workspace path (for mapping container /workspace/ paths)
        self._workspace_path: Optional[str] = workspace_path
        # Per-turn step limit callback: () -> str|None.
        # Returns error JSON if limit exceeded, None if OK.
        self._step_check_fn: Optional[Callable] = None

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

    def set_cancel_event(self, cancel_event):
        """Set a threading.Event checked before each tool call.
        When set, tool calls are rejected immediately so the agent turn
        ends quickly and the simulation can exit."""
        self._cancel_event = cancel_event

    def set_deadline(self, deadline: Optional[float]):
        """Set wall-clock deadline.  Tool calls arriving after the deadline
        are rejected immediately with an error message instead of being
        forwarded to the real implementation."""
        self._deadline = deadline

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

        call_id = f"{name}_{len(self._logs)}"
        log = ToolCallLog(
            name=name, args=kwargs, timestamp=timestamp, turn_index=self._current_turn
        )
        _emit("tool_start", {"call_id": call_id, "name": name, "args": kwargs})

        # Cancel check: reject tool calls immediately when run is cancelled.
        if self._cancel_event is not None and self._cancel_event.is_set():
            log.result = (
                "Error: Run cancelled by user. "
                "No further tool calls can be executed."
            )
            log.success = False
            log.duration_ms = 0.0
            self._logs.append(log)
            _emit(
                "tool_result",
                {
                    "call_id": call_id,
                    "name": name,
                    "result": log.result,
                    "success": False,
                    "duration_ms": 0.0,
                },
            )
            return log.result

        # Deadline check: reject new tool calls after the session time limit.
        # This prevents long-running tools (e.g. run_backtest) from being
        # started when the conversation should be winding down.
        if self._deadline is not None and time.time() > self._deadline:
            log.result = (
                "Error: Session time limit reached. "
                "No further tool calls can be executed. "
                "Please summarize your findings for the student."
            )
            log.success = False
            log.duration_ms = 0.0
            self._logs.append(log)
            _emit(
                "tool_result",
                {
                    "call_id": call_id,
                    "name": name,
                    "result": log.result,
                    "success": False,
                    "duration_ms": 0.0,
                },
            )
            return log.result

        # Per-turn step limit: reject non-session tool calls when budget
        # exhausted.  The callback is set by exam_server after session
        # creation; legacy path leaves it as None (no enforcement).
        if self._step_check_fn is not None and name not in self._STEP_EXEMPT_TOOLS:
            step_error = self._step_check_fn()
            if step_error is not None:
                log.result = step_error
                log.success = False
                log.duration_ms = 0.0
                self._logs.append(log)
                _emit(
                    "tool_result",
                    {
                        "call_id": call_id,
                        "name": name,
                        "result": log.result,
                        "success": False,
                        "duration_ms": 0.0,
                    },
                )
                return log.result

        try:
            if name in self._distractors:
                if name in self._tools:
                    # Functional distractor — runs real code, returns
                    # plausible result.  Agent sees normal output.
                    result = self._tools[name](**kwargs)
                    log.success = True
                else:
                    # Error-based distractor — returns static error string
                    result = self._distractors[name]
                    log.success = False
            elif name in self._tools:
                result = self._tools[name](**kwargs)
                log.success = True
            else:
                result = f"Error: Unknown tool '{name}'"
                log.success = False

            log.result = str(result)

            # Truncate oversized results using head+tail strategy.
            # Keeps both the beginning (structure, column names) and the
            # end (final results, error messages, conclusions) — avoids
            # losing important information that concentrates at the tail.
            if len(log.result) > _MAX_LOG_RESULT:
                original_len = len(log.result)
                head_size = _MAX_LOG_RESULT * 2 // 3
                tail_size = _MAX_LOG_RESULT // 3
                log.result = (
                    log.result[:head_size] + f"\n\n... [{original_len:,} chars total, "
                    f"showing first {head_size:,} + last {tail_size:,}. "
                    f"Use file_write() to save large data, then file_read() to access.] ...\n\n"
                    + log.result[-tail_size:]
                )

            # Generic failure detection — tool-name agnostic.
            # Tools follow the convention of returning "Error: ..." on
            # failure, appending "[exit code]: N" for non-zero exits, or
            # emitting Python tracebacks.  We detect these patterns
            # without referencing any specific tool name.
            if log.success:
                _r = log.result
                if _r.startswith("Error:") or _r.startswith("Error "):
                    log.success = False
                elif "Traceback (most recent call last)" in _r:
                    log.success = False
                elif "Status: compile_error" in _r or "Status: runtime_error" in _r:
                    log.success = False
                else:
                    _m = _EXIT_CODE_RE.search(_r)
                    if _m and int(_m.group(1)) != 0:
                        log.success = False
        except Exception as e:
            log.result = f"Error: {type(e).__name__}: {str(e)}"
            log.success = False

        log.duration_ms = (time.time() - start_time) * 1000

        # Detect image files produced by this tool call.
        # Extract paths from result text and verify they exist on disk.
        # In Docker mode, result contains container paths (/workspace/...)
        # which must be mapped to the host workspace path.
        if log.success:
            for m in _IMAGE_PATH_RE.finditer(log.result):
                img_path = m.group(1)
                basename = os.path.basename(img_path)
                # Try direct path first (local mode)
                if os.path.isfile(img_path):
                    log.output_files.append(basename)
                # Try mapping /workspace/... to host workspace (Docker mode)
                elif self._workspace_path and img_path.startswith("/workspace/"):
                    host_path = os.path.join(
                        self._workspace_path, img_path[len("/workspace/") :]
                    )
                    if os.path.isfile(host_path):
                        log.output_files.append(basename)

        self._logs.append(log)
        emit_payload = {
            "call_id": call_id,
            "name": name,
            "result": log.result,
            "success": log.success,
            "duration_ms": log.duration_ms,
        }
        if log.output_files:
            emit_payload["output_files"] = log.output_files
        _emit("tool_result", emit_payload)

        return log.result

    def get_logs(self) -> list[ToolCallLog]:
        """Get all recorded tool call logs."""
        return list(self._logs)

    def get_distractor_names(self) -> list[str]:
        """Get names of all registered distractor tools."""
        return list(self._distractors.keys())
