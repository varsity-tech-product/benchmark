"""Docker container lifecycle manager for QuantTutorBench sandboxed execution."""

import json
import os
import select
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ExecResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False


@dataclass
class ContainerInfo:
    container_id: str
    workspace_path: str
    task_id: str
    network_enabled: bool = False
    network_mode: str = "none"


@dataclass
class _ExecutorHandle:
    """Handle to a running tool_executor.py process inside a container."""

    proc: subprocess.Popen
    lock: threading.Lock = field(default_factory=threading.Lock)
    alive: bool = True
    _next_id: int = 0

    def next_id(self) -> int:
        self._next_id += 1
        return self._next_id


class ContainerManager:
    """Manages Docker containers for task execution.

    Falls back to local subprocess execution if Docker is not available.
    """

    def __init__(
        self, docker_image: str = "quant-tutor-env:v2.2", use_docker: bool = True
    ):
        self.docker_image = docker_image
        self.use_docker = use_docker and self._docker_available()
        self._containers: dict[str, ContainerInfo] = {}
        self._workspaces: dict[str, str] = {}
        self._executors: dict[str, _ExecutorHandle] = {}

    def _docker_available(self) -> bool:
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def create_container(
        self,
        task_id: str,
        data_dir: str,
        docs_dir: str,
        student_code_dir: Optional[str] = None,
        sandbox_image: Optional[str] = None,
        network_enabled: bool = False,
    ) -> ContainerInfo:
        """Create a sandboxed Docker container (or local workspace fallback).

        Args:
            task_id: Unique identifier for this task run.
            data_dir: Host-side directory to mount as /data (read-only).
                      May be a staged/filtered directory.
            docs_dir: Host-side directory to mount as /docs (read-only).
                      May be a staged/filtered directory.
            student_code_dir: If provided, mounted as /student_code (read-only).
            sandbox_image: Docker image override (default: self.docker_image).
        """
        image = sandbox_image or self.docker_image
        workspace = tempfile.mkdtemp(prefix=f"qtb_{task_id}_")

        if self.use_docker:
            mounts = [
                f"-v {workspace}:/workspace",
                f"-v {data_dir}:/data:ro",
                f"-v {docs_dir}:/docs:ro",
            ]
            if student_code_dir:
                mounts.append(f"-v {student_code_dir}:/student_code:ro")

            network_flag = "--network bridge" if network_enabled else "--network none"
            network_mode = "bridge" if network_enabled else "none"
            cmd = (
                f"docker run -d --name qtb_{task_id}_{int(time.time())} "
                f"{network_flag} --cpus 2 --memory 4g "
                f"{' '.join(mounts)} "
                f"{image} sleep infinity"
            )
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            container_id = result.stdout.strip()[:12]

            # Inject tool_executor.py and tools.py into the container.
            bench_core = Path(__file__).parent.parent / "mcp_servers" / "core"
            subprocess.run(
                [
                    "docker",
                    "cp",
                    str(bench_core / "tools.py"),
                    f"{container_id}:/opt/bench/tools.py",
                ],
                capture_output=True,
            )
            subprocess.run(
                [
                    "docker",
                    "cp",
                    str(bench_core / "tool_executor.py"),
                    f"{container_id}:/opt/bench/tool_executor.py",
                ],
                capture_output=True,
            )
        else:
            container_id = f"local_{task_id}_{int(time.time())}"
            # Local fallback cannot enforce isolation; use host networking semantics.
            network_mode = "host"
            network_enabled = True

        info = ContainerInfo(
            container_id=container_id,
            workspace_path=workspace,
            task_id=task_id,
            network_enabled=network_enabled,
            network_mode=network_mode,
        )
        self._containers[container_id] = info
        self._workspaces[container_id] = workspace
        return info

    # ------------------------------------------------------------------
    # Tool executor lifecycle (Docker mode only)
    # ------------------------------------------------------------------

    def start_executor(self, container_id: str, timeout: float = 15.0) -> None:
        """Start the tool_executor.py daemon inside a Docker container.

        Launches ``docker exec -i`` with the Python executor script and
        waits for the readiness signal.

        Args:
            container_id: The Docker container ID.
            timeout: Seconds to wait for the readiness signal.

        Raises:
            RuntimeError: If the executor fails to start within *timeout*.
        """
        if not self.use_docker or container_id.startswith("local_"):
            return  # No executor needed in local mode

        proc = subprocess.Popen(
            [
                "docker",
                "exec",
                "-i",
                "-e",
                "PYTHONPATH=/opt/bench",
                container_id,
                "python3",
                "-u",
                "/opt/bench/tool_executor.py",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # line-buffered
        )

        # Wait for the {"status": "ready"} handshake.
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                stderr = proc.stderr.read()
                raise RuntimeError(
                    f"Executor died during startup (exit {proc.returncode}): "
                    f"{stderr[:500]}"
                )
            remaining = max(0.1, deadline - time.time())
            ready, _, _ = select.select([proc.stdout], [], [], min(remaining, 1.0))
            if ready:
                line = proc.stdout.readline().strip()
                if line:
                    msg = json.loads(line)
                    if msg.get("status") == "ready":
                        self._executors[container_id] = _ExecutorHandle(proc=proc)
                        return

        proc.kill()
        raise RuntimeError(f"Executor did not become ready within {timeout}s")

    def call_tool_in_container(
        self,
        container_id: str,
        tool_name: str,
        tool_args: dict,
        timeout: float = 60.0,
    ) -> str:
        """Call a tool inside the container via the executor daemon.

        Thread-safe: acquires a lock so concurrent callers are serialised.

        Returns:
            The tool's result string.

        Raises:
            RuntimeError: If the executor is dead or times out.
        """
        handle = self._executors.get(container_id)
        if handle is None or not handle.alive:
            raise RuntimeError(f"No executor running for container {container_id}")

        with handle.lock:
            req_id = handle.next_id()
            request_line = (
                json.dumps(
                    {
                        "id": req_id,
                        "tool": tool_name,
                        "args": tool_args,
                    }
                )
                + "\n"
            )

            try:
                handle.proc.stdin.write(request_line)
                handle.proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                handle.alive = False
                raise RuntimeError(f"Executor stdin broken: {exc}")

            # Read response with timeout using select().
            deadline = time.time() + timeout
            while time.time() < deadline:
                remaining = max(0.1, deadline - time.time())
                ready, _, _ = select.select(
                    [handle.proc.stdout], [], [], min(remaining, 1.0)
                )
                if ready:
                    line = handle.proc.stdout.readline().strip()
                    if line:
                        try:
                            response = json.loads(line)
                        except json.JSONDecodeError:
                            continue  # skip garbled output
                        if response.get("id") == req_id:
                            return response.get("result", "")
                        # Wrong ID — keep reading (defensive)

                # Check if process died.
                if handle.proc.poll() is not None:
                    handle.alive = False
                    stderr = handle.proc.stderr.read()
                    raise RuntimeError(
                        f"Executor died (exit {handle.proc.returncode}): "
                        f"{stderr[:500]}"
                    )

            raise RuntimeError(f"Tool '{tool_name}' timed out after {timeout}s")

    def stop_executor(self, container_id: str) -> None:
        """Gracefully stop the executor daemon for a container."""
        handle = self._executors.pop(container_id, None)
        if handle is None:
            return
        if handle.alive and handle.proc.poll() is None:
            try:
                req = json.dumps({"id": 0, "tool": "__shutdown__"}) + "\n"
                handle.proc.stdin.write(req)
                handle.proc.stdin.flush()
                handle.proc.wait(timeout=5)
            except Exception:
                handle.proc.kill()
        handle.alive = False

    def destroy_container(self, container_id: str) -> None:
        self.stop_executor(container_id)
        if self.use_docker and not container_id.startswith("local_"):
            subprocess.run(
                f"docker rm -f {container_id}", shell=True, capture_output=True
            )
        workspace = self._workspaces.pop(container_id, None)
        if workspace and os.path.exists(workspace):
            shutil.rmtree(workspace, ignore_errors=True)
        self._containers.pop(container_id, None)
