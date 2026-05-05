"""Sandbox runtime and tool routing API for plugin execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Sequence

from platform_api.telemetry import TelemetryHook, emit_telemetry


class SandboxRuntimeError(RuntimeError):
    """Raised when sandbox lifecycle operations fail."""


@dataclass(frozen=True)
class SandboxMount:
    """Host path mounted into a sandbox."""

    host_path: str
    container_path: str
    read_only: bool = True

    def to_docker_volume(self) -> str:
        mode = "ro" if self.read_only else "rw"
        return f"{self.host_path}:{self.container_path}:{mode}"


@dataclass(frozen=True)
class SandboxCreateRequest:
    """Request to create an isolated sandbox process."""

    image_uri: str
    sandbox_id: str | None = None
    mounts: tuple[SandboxMount, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    network_enabled: bool = False
    cpus: str = "1"
    memory: str = "768m"
    workdir: str = "/workspace"
    command: tuple[str, ...] = ("sleep", "infinity")
    pull_policy: str = "missing"
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxHandle:
    """Runtime handle returned by a sandbox implementation."""

    sandbox_id: str
    image_uri: str
    container_id: str
    workspace_path: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = 0
    timed_out: bool = False
    duration_ms: float = 0.0


@dataclass(frozen=True)
class ToolRequest:
    name: str
    args: Mapping[str, Any] = field(default_factory=dict)
    timeout: float = 60.0
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    tool_name: str
    success: bool
    output: str = ""
    data: Mapping[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0
    error: str | None = None


ToolRoute = Callable[
    [SandboxHandle, Mapping[str, Any]], ToolResult | str | Mapping[str, Any]
]
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]


class ToolRouter:
    """Name-based router for sandbox-aware tools."""

    def __init__(self) -> None:
        self._routes: dict[str, ToolRoute] = {}

    def register(self, name: str, route: ToolRoute) -> None:
        if not name:
            raise ValueError("tool route name is required")
        self._routes[name] = route

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._routes))

    def has_route(self, name: str) -> bool:
        return name in self._routes

    def route(self, handle: SandboxHandle, request: ToolRequest) -> ToolResult:
        if request.name not in self._routes:
            raise SandboxRuntimeError(f"Unknown tool route: {request.name}")

        start = time.perf_counter()
        try:
            result = self._routes[request.name](handle, request.args)
        except Exception as exc:
            return ToolResult(
                tool_name=request.name,
                success=False,
                duration_ms=(time.perf_counter() - start) * 1000,
                error=f"{type(exc).__name__}: {exc}",
            )

        duration_ms = (time.perf_counter() - start) * 1000
        if isinstance(result, ToolResult):
            return result
        if isinstance(result, Mapping):
            success = bool(result.get("success", True))
            output = str(result.get("output", result.get("result", "")))
            error = result.get("error")
            data = {
                key: value
                for key, value in result.items()
                if key not in {"success", "output", "result", "error"}
            }
            return ToolResult(
                tool_name=request.name,
                success=success,
                output=output,
                data=data,
                duration_ms=duration_ms,
                error=str(error) if error else None,
            )
        return ToolResult(
            tool_name=request.name,
            success=True,
            output=str(result),
            duration_ms=duration_ms,
        )


class SandboxRuntime(ABC):
    """Abstract sandbox lifecycle and execution boundary."""

    @abstractmethod
    def pull_image(self, image_uri: str) -> None:
        """Ensure a sandbox image is available locally."""

    @abstractmethod
    def create(self, request: SandboxCreateRequest) -> SandboxHandle:
        """Create a sandbox and return its handle."""

    @abstractmethod
    def exec(
        self,
        handle: SandboxHandle,
        command: str | Sequence[str],
        *,
        timeout: float = 60.0,
    ) -> ExecResult:
        """Execute a process inside the sandbox."""

    @abstractmethod
    def call_tool(self, handle: SandboxHandle, request: ToolRequest) -> ToolResult:
        """Route a tool call through the sandbox runtime."""

    @abstractmethod
    def destroy(self, handle: SandboxHandle) -> None:
        """Destroy a sandbox and release owned resources."""


def _default_runner(
    args: Sequence[str], *, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(args),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _timeout_output_text(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


class DockerSandboxRuntime(SandboxRuntime):
    """Docker-backed sandbox runtime."""

    def __init__(
        self,
        *,
        telemetry: TelemetryHook | None = None,
        router: ToolRouter | None = None,
        runner: CommandRunner | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.router = router or ToolRouter()
        self._runner = runner
        self._handles: dict[str, SandboxHandle] = {}

    def pull_image(self, image_uri: str) -> None:
        start = time.perf_counter()
        error: str | None = None
        result = self._run(["docker", "pull", image_uri], timeout=None)
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            self._emit("image_pull", start, False, error, {"image_uri": image_uri})
            raise SandboxRuntimeError(f"docker pull failed: {error}")
        self._emit("image_pull", start, True, None, {"image_uri": image_uri})

    def create(self, request: SandboxCreateRequest) -> SandboxHandle:
        if request.pull_policy == "always":
            self.pull_image(request.image_uri)

        sandbox_id = request.sandbox_id or f"qab_{uuid.uuid4().hex[:12]}"
        args = self._build_run_args(sandbox_id, request)
        start = time.perf_counter()
        result = self._run(args, timeout=None)
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            self._emit(
                "create",
                start,
                False,
                error,
                {"sandbox_id": sandbox_id, "image_uri": request.image_uri},
            )
            raise SandboxRuntimeError(f"docker run failed: {error}")

        container_id = result.stdout.strip()
        if not container_id:
            error = "docker run returned empty container id"
            self._emit(
                "create",
                start,
                False,
                error,
                {"sandbox_id": sandbox_id, "image_uri": request.image_uri},
            )
            raise SandboxRuntimeError(error)

        handle = SandboxHandle(
            sandbox_id=sandbox_id,
            image_uri=request.image_uri,
            container_id=container_id,
            metadata=dict(request.metadata),
        )
        self._handles[sandbox_id] = handle
        self._emit(
            "create",
            start,
            True,
            None,
            {"sandbox_id": sandbox_id, "image_uri": request.image_uri},
        )
        return handle

    def exec(
        self,
        handle: SandboxHandle,
        command: str | Sequence[str],
        *,
        timeout: float = 60.0,
    ) -> ExecResult:
        if isinstance(command, str):
            exec_command = ["bash", "-lc", command]
        else:
            exec_command = list(command)

        args = ["docker", "exec", handle.container_id, *exec_command]
        start = time.perf_counter()
        try:
            result = self._run(args, timeout=timeout)
            duration_ms = (time.perf_counter() - start) * 1000
            success = result.returncode == 0
            error = None if success else (result.stderr.strip() or result.stdout.strip())
            self._emit(
                "exec",
                start,
                success,
                error,
                {"sandbox_id": handle.sandbox_id, "exit_code": result.returncode},
            )
            return ExecResult(
                stdout=result.stdout,
                stderr=result.stderr,
                exit_code=result.returncode,
                duration_ms=duration_ms,
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = (time.perf_counter() - start) * 1000
            error = f"timeout after {timeout}s"
            self._emit(
                "exec",
                start,
                False,
                error,
                {"sandbox_id": handle.sandbox_id},
            )
            return ExecResult(
                stdout=_timeout_output_text(exc.stdout),
                stderr=_timeout_output_text(exc.stderr),
                exit_code=-1,
                timed_out=True,
                duration_ms=duration_ms,
            )

    def call_tool(self, handle: SandboxHandle, request: ToolRequest) -> ToolResult:
        start = time.perf_counter()
        result = self.router.route(handle, request)
        latency_ms = (time.perf_counter() - start) * 1000
        error = result.error if not result.success else None
        self._emit(
            "tool",
            start,
            result.success,
            error,
            {"sandbox_id": handle.sandbox_id, "tool": request.name},
        )
        if result.duration_ms:
            return result
        return ToolResult(
            tool_name=result.tool_name,
            success=result.success,
            output=result.output,
            data=result.data,
            duration_ms=latency_ms,
            error=result.error,
        )

    def destroy(self, handle: SandboxHandle) -> None:
        start = time.perf_counter()
        result = self._run(["docker", "rm", "-f", handle.container_id], timeout=None)
        success = result.returncode == 0
        error = None if success else (result.stderr.strip() or result.stdout.strip())
        self._handles.pop(handle.sandbox_id, None)
        self._emit(
            "destroy",
            start,
            success,
            error,
            {"sandbox_id": handle.sandbox_id},
        )
        if not success:
            raise SandboxRuntimeError(f"docker rm failed: {error}")

    def _build_run_args(
        self, sandbox_id: str, request: SandboxCreateRequest
    ) -> list[str]:
        args = [
            "docker",
            "run",
            "-d",
            "--name",
            sandbox_id,
            "--network",
            "bridge" if request.network_enabled else "none",
            "--cpus",
            str(request.cpus),
            "--memory",
            str(request.memory),
        ]
        for mount in request.mounts:
            args.extend(["-v", mount.to_docker_volume()])
        for key, value in sorted(request.env.items()):
            args.extend(["-e", f"{key}={value}"])
        if request.workdir:
            args.extend(["-w", request.workdir])
        args.append(request.image_uri)
        args.extend(request.command)
        return args

    def _run(
        self, args: Sequence[str], *, timeout: float | None
    ) -> subprocess.CompletedProcess[str]:
        if self._runner is None:
            return _default_runner(args, timeout=timeout)
        return self._runner(list(args), timeout=timeout)  # type: ignore[misc]

    def _emit(
        self,
        event: str,
        start: float,
        success: bool,
        error: str | None,
        attributes: dict[str, Any],
    ) -> None:
        emit_telemetry(
            self.telemetry,
            namespace="sandbox",
            event=event,
            latency_ms=(time.perf_counter() - start) * 1000,
            success=success,
            error=error,
            attributes=attributes,
        )


class LocalSandboxRuntime(SandboxRuntime):
    """Local runtime used for tests and development."""

    def __init__(
        self,
        *,
        telemetry: TelemetryHook | None = None,
        router: ToolRouter | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.router = router or ToolRouter()
        self._owned_workspaces: set[str] = set()
        self._env_by_sandbox_id: dict[str, dict[str, str]] = {}

    def pull_image(self, image_uri: str) -> None:
        emit_telemetry(
            self.telemetry,
            namespace="sandbox",
            event="image_pull",
            attributes={"image_uri": image_uri, "runtime": "local"},
        )

    def create(self, request: SandboxCreateRequest) -> SandboxHandle:
        start = time.perf_counter()
        workspace = self._resolve_workspace(request)
        sandbox_id = request.sandbox_id or f"local_{uuid.uuid4().hex[:12]}"
        handle = SandboxHandle(
            sandbox_id=sandbox_id,
            image_uri=request.image_uri,
            container_id=sandbox_id,
            workspace_path=workspace,
            metadata=dict(request.metadata),
        )
        self._env_by_sandbox_id[sandbox_id] = dict(request.env)
        self._emit(
            "create",
            start,
            True,
            None,
            {"sandbox_id": sandbox_id, "runtime": "local"},
        )
        return handle

    def exec(
        self,
        handle: SandboxHandle,
        command: str | Sequence[str],
        *,
        timeout: float = 60.0,
    ) -> ExecResult:
        start = time.perf_counter()
        env = os.environ.copy()
        env.update(self._env_by_sandbox_id.get(handle.sandbox_id, {}))
        try:
            result = subprocess.run(
                command,
                shell=isinstance(command, str),
                cwd=handle.workspace_path or None,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            self._emit(
                "exec",
                start,
                False,
                f"timeout after {timeout}s",
                {"sandbox_id": handle.sandbox_id, "runtime": "local"},
            )
            return ExecResult(
                stdout=_timeout_output_text(exc.stdout),
                stderr=_timeout_output_text(exc.stderr),
                exit_code=-1,
                timed_out=True,
                duration_ms=(time.perf_counter() - start) * 1000,
            )

        success = result.returncode == 0
        error = None if success else (result.stderr.strip() or result.stdout.strip())
        self._emit(
            "exec",
            start,
            success,
            error,
            {"sandbox_id": handle.sandbox_id, "runtime": "local"},
        )
        return ExecResult(
            stdout=result.stdout,
            stderr=result.stderr,
            exit_code=result.returncode,
            duration_ms=(time.perf_counter() - start) * 1000,
        )

    def call_tool(self, handle: SandboxHandle, request: ToolRequest) -> ToolResult:
        start = time.perf_counter()
        result = self.router.route(handle, request)
        self._emit(
            "tool",
            start,
            result.success,
            result.error if not result.success else None,
            {
                "sandbox_id": handle.sandbox_id,
                "runtime": "local",
                "tool": request.name,
            },
        )
        return result

    def destroy(self, handle: SandboxHandle) -> None:
        start = time.perf_counter()
        if handle.workspace_path in self._owned_workspaces:
            shutil.rmtree(handle.workspace_path, ignore_errors=True)
            self._owned_workspaces.remove(handle.workspace_path)
        self._env_by_sandbox_id.pop(handle.sandbox_id, None)
        self._emit(
            "destroy",
            start,
            True,
            None,
            {"sandbox_id": handle.sandbox_id, "runtime": "local"},
        )

    def _resolve_workspace(self, request: SandboxCreateRequest) -> str:
        for mount in request.mounts:
            if mount.container_path == request.workdir:
                return mount.host_path
        workspace = tempfile.mkdtemp(prefix="qab_local_")
        self._owned_workspaces.add(workspace)
        return workspace

    def _emit(
        self,
        event: str,
        start: float,
        success: bool,
        error: str | None,
        attributes: dict[str, Any],
    ) -> None:
        emit_telemetry(
            self.telemetry,
            namespace="sandbox",
            event=event,
            latency_ms=(time.perf_counter() - start) * 1000,
            success=success,
            error=error,
            attributes=attributes,
        )
