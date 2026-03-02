"""Container-aware wrappers for code execution tools.

These factory functions create tool implementations that route execution
through Docker containers (via ContainerManager.exec_in_container) when
Docker mode is active, with local subprocess fallback for development.

Only shell_exec needs wrapping — it runs arbitrary shell commands inside
the Docker sandbox. All other tools (including run_backtest and plot_chart)
are self-contained: they use Python libraries directly (pandas, numpy,
matplotlib exec()) and don't need subprocess/Docker routing.
"""

import subprocess


def make_shell_exec(
    container_manager, container_id: str, workspace_path: str, use_docker: bool
):
    """Create a container-aware shell_exec.

    In Docker mode, commands run inside the container via ``docker exec``.
    In local mode, commands run via subprocess in the workspace directory.
    """

    def shell_exec(command: str, timeout: int = 30) -> str:
        if use_docker:
            result = container_manager.exec_in_container(
                container_id,
                command,
                timeout=timeout,
            )
        else:
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
