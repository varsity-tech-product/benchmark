"""Claude Code CLI adapter for QuantTutorBench.

Uses the Claude Code CLI (`claude --print`) as the agent under test.
No API key needed — uses the user's existing Claude Code authentication.

Tool integration: benchmark tools are exposed via a lightweight MCP stdio
server (mcp_bridge_server.py) that bridges tool calls back to the parent
process over a Unix domain socket.

Usage:
    python bench/run_benchmark.py run-single --agent claude-code --task I01_implement_sma ...
"""

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from typing import Optional

from .base_adapter import BaseAgentAdapter, TokenRecord
from .prompts import TUTOR_SYSTEM_PROMPT

_BRIDGE_SERVER = str(
    Path(__file__).parent.parent.parent / "mcp_servers" / "core" / "mcp_bridge_server.py"
)

# Built-in Claude Code tools to block (agent should only use benchmark MCP tools)
_DISALLOWED_BUILTIN_TOOLS = [
    "Bash",
    "Read",
    "Write",
    "Edit",
    "Glob",
    "Grep",
    "WebSearch",
    "WebFetch",
    "NotebookEdit",
    "TodoWrite",
    "AskUserQuestion",
    "Agent",
]


class ClaudeCodeAdapter(BaseAgentAdapter):
    """Adapter that runs Claude Code CLI as the agent under test.

    Each conversation turn launches ``claude --print`` with the full
    conversation formatted as a prompt. MCP tools are bridged through
    a Unix socket so Claude Code's internal agent loop can call them.
    """

    def __init__(
        self,
        model: str = "sonnet",
        system_prompt: str = "",
        agent_name: str = "claude_code",
        max_agent_turns: int = 10,
    ):
        super().__init__(agent_name=agent_name)
        self.model = model
        self.system_prompt = system_prompt or TUTOR_SYSTEM_PROMPT
        self.max_agent_turns = max_agent_turns
        self._task_context: str = ""
        self._session_id: Optional[str] = None
        self._local_auth_available: Optional[bool] = None

        # Bridge state (lazy-initialized per generate_response call)
        self._socket_path: Optional[str] = None
        self._socket_server: Optional[socket.socket] = None
        self._listener_thread: Optional[threading.Thread] = None
        self._tool_callback: Optional[callable] = None
        self._running = False
        self._tmp_dir: Optional[str] = None

    def set_task_context(self, context: str):
        """Set per-task dynamic context injected into system prompt."""
        self._task_context = context

    def set_agent_max_steps(self, n: int):
        """Limit Claude Code internal agent loop depth per turn."""
        self.max_agent_turns = n

    def reset(self):
        """Reset between tasks."""
        super().reset()
        self._task_context = ""
        self._session_id = None
        self._stop_bridge()

    def close(self):
        """Release bridge resources."""
        self._stop_bridge()

    # ── Core: generate_response ────────────────────────────────

    def generate_response(
        self,
        messages: list[dict],
        available_tools: list[dict],
        tool_callback: Optional[callable] = None,
    ) -> str:
        """Generate a response using Claude Code CLI.

        Launches ``claude --print`` with:
        - Full conversation formatted as prompt
        - System prompt including task context
        - MCP bridge server for benchmark tool access
        - All built-in tools disabled
        """
        self._tool_callback = tool_callback
        use_persistent_session = self._should_use_persistent_session()

        # Build the effective system prompt
        effective_prompt = self.system_prompt
        if self._task_context:
            effective_prompt = f"{effective_prompt}\n\n{self._task_context}"

        # Build prompt from conversation history
        prompt = self._build_prompt(
            messages,
            incremental=use_persistent_session and self._session_id is not None,
        )

        # Set up MCP bridge if tools are available
        mcp_config_path = None
        if available_tools and tool_callback:
            mcp_config_path = self._setup_bridge(available_tools)

        # Build CLI command
        cmd = self._build_command(
            effective_prompt=effective_prompt,
            mcp_config_path=mcp_config_path,
            use_persistent_session=use_persistent_session,
        )

        # Prevent nesting error and set up auth
        env = self._build_env(use_persistent_session)

        try:
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )

            if result.returncode != 0:
                stderr = result.stderr.strip()
                if stderr:
                    return f"[Claude Code error: {stderr}]"
                return f"[Claude Code exited with code {result.returncode}]"

            return self._parse_output(result.stdout)

        except subprocess.TimeoutExpired:
            return "[Claude Code timed out after 300s]"
        except FileNotFoundError:
            return (
                "[Claude Code CLI not found. "
                "Install from: https://docs.anthropic.com/en/docs/claude-code]"
            )
        except Exception as e:
            return f"[Claude Code error: {e}]"

    # ── Prompt building ────────────────────────────────────────

    def _build_prompt(self, messages: list[dict], incremental: bool = False) -> str:
        """Format conversation history as a single prompt string.

        Claude Code's --print mode takes a single prompt. We format
        the full conversation so Claude has context from prior turns.
        """
        if incremental:
            return messages[-1]["content"]

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

    def _build_command(
        self,
        *,
        effective_prompt: str,
        mcp_config_path: Optional[str],
        use_persistent_session: bool,
    ) -> list[str]:
        """Build the Claude CLI command for the current turn."""
        cmd = [
            "claude",
            "--print",
            "--output-format",
            "json",
            "--dangerously-skip-permissions",
            "--model",
            self.model,
            "--max-budget-usd",
            "2.00",
        ]

        if use_persistent_session and self._session_id:
            cmd.extend(["--resume", self._session_id])
        else:
            if not use_persistent_session:
                cmd.append("--no-session-persistence")
            cmd.extend(["--system-prompt", effective_prompt])

        if mcp_config_path:
            cmd.extend(["--mcp-config", mcp_config_path])
            cmd.append("--strict-mcp-config")
            cmd.extend(
                [
                    "--disallowed-tools",
                    ",".join(_DISALLOWED_BUILTIN_TOOLS),
                ]
            )

        return cmd

    def _build_env(self, use_persistent_session: bool) -> dict[str, str]:
        """Build the subprocess environment for Claude CLI."""
        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        # Persistent task sessions require Claude's native on-disk session
        # machinery. When local Claude auth is available, prefer that path.
        # Otherwise, keep the previous stateless OAuth-credential override.
        if not use_persistent_session:
            oauth_token = env.pop("CLAUDE_CODE_OAUTH_TOKEN", None)
            refresh_token = env.pop("CLAUDE_CODE_OAUTH_REFRESH_TOKEN", None)
            if oauth_token:
                creds_dir = self._ensure_oauth_creds(
                    oauth_token,
                    refresh_token=refresh_token or "",
                )
                env["CLAUDE_CONFIG_DIR"] = creds_dir

        return env

    def _should_use_persistent_session(self) -> bool:
        """Return True when Claude's native task sessions should be used."""
        if self._local_auth_available is None:
            self._local_auth_available = self._detect_local_auth()
        return self._local_auth_available

    def _detect_local_auth(self) -> bool:
        """Check whether the locally installed Claude CLI is already logged in."""
        try:
            result = subprocess.run(
                ["claude", "auth", "status"],
                capture_output=True,
                text=True,
                timeout=10,
                env={k: v for k, v in os.environ.items() if k != "CLAUDECODE"},
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

        if result.returncode != 0 or not result.stdout.strip():
            return False

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return False
        return bool(data.get("loggedIn"))

    # ── Output parsing ─────────────────────────────────────────

    def _parse_output(self, stdout: str) -> str:
        """Parse Claude Code's JSON output for response text and token usage."""
        stdout = stdout.strip()
        if not stdout:
            return "[No output from Claude Code]"

        try:
            data = json.loads(stdout)
        except json.JSONDecodeError:
            # Might be plain text if JSON parsing failed
            return stdout

        # Record token usage
        model_usage = data.get("modelUsage", {})
        for model_name, usage in model_usage.items():
            inp = usage.get("inputTokens", 0)
            cache_read = usage.get("cacheReadInputTokens", 0)
            cache_create = usage.get("cacheCreationInputTokens", 0)
            out = usage.get("outputTokens", 0)
            cost = usage.get("costUSD", 0.0)
            self._token_records.append(
                TokenRecord(
                    model=model_name,
                    input_tokens=inp + cache_read + cache_create,
                    output_tokens=out,
                    cost_usd=cost,
                )
            )

        if data.get("session_id"):
            self._session_id = data["session_id"]

        if data.get("is_error"):
            return f"[Claude Code error: {data.get('result', 'unknown')}]"

        return data.get("result", "")

    # ── OAuth credentials ──────────────────────────────────────

    def _ensure_oauth_creds(self, token: str, refresh_token: str = "") -> str:
        """Write a temporary CLAUDE_CONFIG_DIR with OAuth credentials.

        Returns the path to the temp config directory.
        """
        if self._tmp_dir is None:
            self._tmp_dir = tempfile.mkdtemp(prefix="qtb_claude_code_")

        config_dir = os.path.join(self._tmp_dir, "claude_config")
        os.makedirs(config_dir, exist_ok=True)

        creds = {
            "claudeAiOauth": {
                "accessToken": token,
                "refreshToken": refresh_token,
                "expiresAt": 9999999999999,
                "scopes": [
                    "user:inference",
                    "user:profile",
                    "user:sessions:claude_code",
                ],
                "subscriptionType": "max",
            }
        }
        creds_path = os.path.join(config_dir, ".credentials.json")
        with open(creds_path, "w") as f:
            json.dump(creds, f)
        os.chmod(creds_path, 0o600)

        return config_dir

    # ── MCP Bridge ─────────────────────────────────────────────

    def _setup_bridge(self, available_tools: list[dict]) -> str:
        """Set up the MCP bridge: socket server + config file.

        Returns the path to the MCP config JSON file.
        """
        if self._tmp_dir is None:
            self._tmp_dir = tempfile.mkdtemp(prefix="qtb_claude_code_")

        # Write tool schemas
        schemas_path = os.path.join(self._tmp_dir, "tool_schemas.json")
        with open(schemas_path, "w") as f:
            json.dump(available_tools, f)

        # Start socket server if not running
        if self._socket_server is None:
            self._start_socket_server()

        # Write MCP config
        mcp_config = {
            "mcpServers": {
                "benchmark_tools": {
                    "command": sys.executable,
                    "args": [_BRIDGE_SERVER],
                    "env": {
                        "QTB_BRIDGE_SOCKET": self._socket_path,
                        "QTB_TOOL_SCHEMAS": schemas_path,
                    },
                },
            },
        }
        config_path = os.path.join(self._tmp_dir, "mcp_config.json")
        with open(config_path, "w") as f:
            json.dump(mcp_config, f)

        return config_path

    def _start_socket_server(self):
        """Start a Unix domain socket server for tool call bridging."""
        self._socket_path = os.path.join(
            self._tmp_dir, "bridge.sock"
        )
        # Clean up stale socket
        if os.path.exists(self._socket_path):
            os.unlink(self._socket_path)

        self._socket_server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket_server.bind(self._socket_path)
        self._socket_server.listen(16)
        self._socket_server.settimeout(1.0)  # Allow periodic shutdown checks
        self._running = True

        self._listener_thread = threading.Thread(
            target=self._socket_listener, daemon=True
        )
        self._listener_thread.start()

    def _socket_listener(self):
        """Accept incoming tool call connections from the bridge server."""
        while self._running:
            try:
                conn, _ = self._socket_server.accept()
                # Handle each tool call in its own thread (Claude may make concurrent calls)
                threading.Thread(
                    target=self._handle_tool_call,
                    args=(conn,),
                    daemon=True,
                ).start()
            except socket.timeout:
                continue
            except OSError:
                break  # Socket closed

    def _handle_tool_call(self, conn: socket.socket):
        """Handle a single tool call from the bridge server."""
        try:
            conn.settimeout(300)
            # Read the full request (newline-delimited JSON)
            data = b""
            while True:
                chunk = conn.recv(65536)
                if not chunk:
                    break
                data += chunk

            if not data:
                return

            request = json.loads(data.decode("utf-8"))
            tool_name = request.get("name", "")
            tool_args = request.get("args", {})

            # Execute through the proxy's tool_callback
            if self._tool_callback:
                try:
                    result = self._tool_callback(tool_name, **tool_args)
                    response = {"result": str(result), "error": False}
                except Exception as e:
                    response = {"result": f"Tool error: {e}", "error": True}
            else:
                response = {"result": "Error: no tool callback available", "error": True}

            conn.sendall(json.dumps(response).encode("utf-8"))
        except Exception as e:
            try:
                err = json.dumps({"result": f"Bridge handler error: {e}", "error": True})
                conn.sendall(err.encode("utf-8"))
            except Exception:
                pass
        finally:
            conn.close()

    def _stop_bridge(self):
        """Stop the socket server and clean up temp files."""
        self._running = False

        if self._socket_server is not None:
            try:
                self._socket_server.close()
            except Exception:
                pass
            self._socket_server = None

        if self._listener_thread is not None:
            self._listener_thread.join(timeout=5)
            self._listener_thread = None

        if self._socket_path and os.path.exists(self._socket_path):
            try:
                os.unlink(self._socket_path)
            except Exception:
                pass
            self._socket_path = None

        if self._tmp_dir and os.path.exists(self._tmp_dir):
            import shutil
            try:
                shutil.rmtree(self._tmp_dir)
            except Exception:
                pass
            self._tmp_dir = None
