"""Docker container lifecycle manager for QuantTutorBench sandboxed execution."""

import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
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


class ContainerManager:
    """Manages Docker containers for task execution.

    Falls back to local subprocess execution if Docker is not available.
    """

    def __init__(
        self, docker_image: str = "quant-tutor-env:v1.0", use_docker: bool = True
    ):
        self.docker_image = docker_image
        self.use_docker = use_docker and self._docker_available()
        self._containers: dict[str, ContainerInfo] = {}
        self._workspaces: dict[str, str] = {}

    def _docker_available(self) -> bool:
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def ensure_image_exists(self, image: Optional[str] = None) -> bool:
        """Check if the Docker image exists; auto-build from Dockerfile if not."""
        image = image or self.docker_image
        if not self.use_docker:
            return True
        try:
            result = subprocess.run(
                f"docker image inspect {image}",
                shell=True,
                capture_output=True,
                timeout=10,
            )
            if result.returncode == 0:
                return True
            # Image not found — try to build it
            dockerfile_dir = Path(__file__).parent.parent / "docker"
            if (dockerfile_dir / "Dockerfile").exists():
                print(f"Docker image '{image}' not found. Building...")
                build = subprocess.run(
                    f"docker build -t {image} {dockerfile_dir}",
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=300,
                )
                if build.returncode == 0:
                    print(f"Docker image '{image}' built successfully.")
                    return True
                print(f"Docker build failed: {build.stderr[:500]}")
            return False
        except Exception:
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

    def exec_in_container(
        self, container_id: str, command: str, timeout: int = 30
    ) -> ExecResult:
        if self.use_docker and not container_id.startswith("local_"):
            try:
                result = subprocess.run(
                    ["docker", "exec", container_id, "bash", "-lc", command],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return ExecResult(
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.returncode,
                )
            except subprocess.TimeoutExpired:
                return ExecResult(
                    stderr="Command timed out", exit_code=-1, timed_out=True
                )
        else:
            # Local fallback
            workspace = self._workspaces.get(container_id, "/tmp")
            try:
                result = subprocess.run(
                    command,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    cwd=workspace,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                )
                return ExecResult(
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.returncode,
                )
            except subprocess.TimeoutExpired:
                return ExecResult(
                    stderr="Command timed out", exit_code=-1, timed_out=True
                )

    def get_workspace_path(self, container_id: str) -> str:
        return self._workspaces.get(container_id, "")

    def destroy_container(self, container_id: str) -> None:
        if self.use_docker and not container_id.startswith("local_"):
            subprocess.run(
                f"docker rm -f {container_id}", shell=True, capture_output=True
            )
        workspace = self._workspaces.pop(container_id, None)
        if workspace and os.path.exists(workspace):
            shutil.rmtree(workspace, ignore_errors=True)
        self._containers.pop(container_id, None)

    def cleanup_all(self) -> None:
        for cid in list(self._containers.keys()):
            self.destroy_container(cid)
