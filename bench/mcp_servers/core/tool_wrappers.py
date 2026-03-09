"""Container-aware wrappers for tool execution.

In Docker mode, ALL core tools are routed through the tool_executor.py
daemon running inside the container via ``call_tool_in_container()``.

In local mode (development), shell_exec uses subprocess with
cwd=workspace_path; all other tools use their original implementations
which read QTB_* environment variables for path resolution.
"""

import subprocess

# Tools that may run for a long time (heavy computation, exec(), subprocess).
_LONG_TIMEOUT_TOOLS = {"plot_chart", "run_backtest", "shell_exec", "compute_statistics"}


def make_container_tool(
    tool_name: str,
    local_func,
    container_manager,
    container_id: str,
    use_docker: bool,
):
    """Create a container-routed wrapper for any core tool.

    In Docker mode the call is forwarded to the tool_executor.py daemon
    running inside the container.  In local mode the original function
    is returned unchanged.

    Args:
        tool_name: Tool name in CORE_TOOLS (e.g. ``"file_read"``).
        local_func: Original tool function from tools.py.
        container_manager: ContainerManager instance.
        container_id: Docker container ID.
        use_docker: Whether Docker mode is active.

    Returns:
        A wrapper function (Docker) or *local_func* itself (local).
    """
    if not use_docker:
        return local_func

    timeout = 120.0 if tool_name in _LONG_TIMEOUT_TOOLS else 60.0

    def _wrapper(**kwargs):
        try:
            return container_manager.call_tool_in_container(
                container_id=container_id,
                tool_name=tool_name,
                tool_args=kwargs,
                timeout=timeout,
            )
        except RuntimeError as exc:
            return f"Error: {exc}"

    _wrapper.__name__ = getattr(local_func, "__name__", tool_name)
    _wrapper.__doc__ = getattr(local_func, "__doc__", "")
    return _wrapper


def make_shell_exec(
    container_manager, container_id: str, workspace_path: str, use_docker: bool
):
    """Create a local-mode shell_exec (subprocess in workspace directory).

    Only used when Docker is **not** active.  In Docker mode, shell_exec
    goes through ``make_container_tool`` like every other tool.
    """

    def shell_exec(command: str, timeout: int = 30) -> str:
        try:
            raw = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=workspace_path,
            )
            from orchestrator.container_manager import ExecResult

            result = ExecResult(
                stdout=raw.stdout,
                stderr=raw.stderr,
                exit_code=raw.returncode,
            )
        except subprocess.TimeoutExpired:
            from orchestrator.container_manager import ExecResult

            result = ExecResult(
                stderr=f"Command timed out after {timeout}s",
                exit_code=-1,
                timed_out=True,
            )

        if result.timed_out:
            return f"Error: Command timed out after {timeout}s"

        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"
        if result.exit_code != 0:
            output += f"\n[exit code]: {result.exit_code}"
        return output.strip() or "(no output)"

    return shell_exec
