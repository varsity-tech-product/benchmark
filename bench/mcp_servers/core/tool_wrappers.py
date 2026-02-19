"""Container-aware wrappers for code execution tools.

These factory functions create tool implementations that route execution
through Docker containers (via ContainerManager.exec_in_container) when
Docker mode is active, with local subprocess fallback for development.

Only tools that execute arbitrary code need wrapping:
- shell_exec: runs shell commands
- run_backtest: executes Python scripts
- plot_chart: executes matplotlib code

Data-reading tools (fetch_market_data, search_docs, etc.) don't need
wrappers because file filtering is handled by staged directories and
lazy environment variable reads in tools.py.
"""

import os
import subprocess
import time


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


def make_run_backtest(
    container_manager, container_id: str, workspace_path: str, use_docker: bool
):
    """Create a container-aware run_backtest.

    Uses the wrapped shell_exec to execute the backtest script.
    """
    _shell = make_shell_exec(
        container_manager, container_id, workspace_path, use_docker
    )

    def run_backtest(script_path: str) -> str:
        if use_docker:
            full_path = (
                f"/workspace/{script_path}"
                if not script_path.startswith("/")
                else script_path
            )
        else:
            full_path = (
                os.path.join(workspace_path, script_path)
                if not script_path.startswith("/")
                else script_path
            )
            if not os.path.isfile(full_path):
                return f"Error: Script not found: {script_path}"
        return _shell(f"python -u {full_path}", timeout=30)

    return run_backtest


def make_plot_chart(
    container_manager, container_id: str, workspace_path: str, use_docker: bool
):
    """Create a container-aware plot_chart.

    Writes the matplotlib script to the workspace (bind-mounted into
    the container), then executes it via the wrapped shell_exec.
    The generated chart PNG is visible on the host via the same mount.
    """
    _shell = make_shell_exec(
        container_manager, container_id, workspace_path, use_docker
    )

    def plot_chart(python_code: str) -> str:
        ts = int(time.time())
        script_name = f"_plot_tmp_{ts}.py"
        chart_name = f"chart_{ts}.png"

        # Paths as seen inside the container vs on the host
        if use_docker:
            chart_path = f"/workspace/{chart_name}"
            script_exec_path = f"/workspace/{script_name}"
        else:
            chart_path = os.path.join(workspace_path, chart_name)
            script_exec_path = os.path.join(workspace_path, script_name)

        code_full = (
            python_code
            + "\nimport matplotlib.pyplot as plt\n"
            + f"plt.savefig('{chart_path}', dpi=100, bbox_inches='tight')\n"
            + "plt.close()\n"
        )

        # Write script to host workspace (visible in container via bind mount)
        host_script = os.path.join(workspace_path, script_name)
        with open(host_script, "w") as f:
            f.write(code_full)

        exec_result = _shell(f"python -u {script_exec_path}", timeout=30)

        # Cleanup temp script
        if os.path.exists(host_script):
            os.unlink(host_script)

        # Check if chart was generated (visible on host via bind mount)
        host_chart = os.path.join(workspace_path, chart_name)
        if os.path.isfile(host_chart):
            return f"Chart saved to {chart_path}"
        return f"Error generating chart: {exec_result}"

    return plot_chart
