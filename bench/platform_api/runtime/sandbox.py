"""Sandbox runtime and tool routing API for plugin execution."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import parse_qs, quote, unquote, urlparse

from platform_api.contracts.models import DataMount, SandboxSpec
from platform_api.telemetry import TelemetryHook, emit_telemetry


class SandboxRuntimeError(RuntimeError):
    """Raised when sandbox lifecycle operations fail."""


DataMountFetcher = Callable[[DataMount, Path], str | Path]
_DEFAULT_REMOTE_FETCHER = object()


def _image_digest(image_uri: str) -> str:
    marker = "@sha256:"
    if marker in image_uri:
        return "sha256:" + image_uri.split(marker, 1)[1]
    return ""


def build_sandbox_digest(
    image_uri: str,
    *,
    resource_limits: Mapping[str, Any] | None = None,
    data_mounts: Sequence[DataMount] = (),
) -> dict[str, Any]:
    """Build the bundle-ready sandbox digest metadata."""
    return {
        "sandbox_image": image_uri,
        "image_uri": image_uri,
        "digest": _image_digest(image_uri),
        "resource_limits": dict(resource_limits or {}),
        "data_mounts": [asdict(mount) for mount in data_mounts],
        "sandbox_policy": {
            "stage": "1",
            "image_policy": "reference_base_image",
            "data_fetch": "materialize_then_bind_mount",
        },
        "source": "SandboxSpec",
    }


def _limit_str(
    limits: Mapping[str, Any], primary: str, aliases: Sequence[str], default: str
) -> str:
    for key in (primary, *aliases):
        value = limits.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def _memory_limit(limits: Mapping[str, Any], default: str) -> str:
    if limits.get("memory"):
        return str(limits["memory"])
    if limits.get("memory_mb"):
        return f"{int(limits['memory_mb'])}m"
    return default


def _bool_limit(limits: Mapping[str, Any], key: str, default: bool) -> bool:
    value = limits.get(key)
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _parse_hf_data_uri(uri: str) -> tuple[str, str, str, str]:
    parsed = urlparse(uri)
    ref = f"{parsed.netloc}{parsed.path}".strip("/")
    repo_ref, _, revision_and_path = ref.rpartition("@")
    revision, _, subpath = revision_and_path.partition("/")
    query = parse_qs(parsed.query)
    repo_type = (query.get("repo_type") or query.get("type") or ["dataset"])[0]
    if repo_ref.startswith("datasets/"):
        repo_ref = repo_ref.removeprefix("datasets/")
        repo_type = "dataset"
    elif repo_ref.startswith("models/"):
        repo_ref = repo_ref.removeprefix("models/")
        repo_type = "model"
    if not repo_ref or not revision:
        raise SandboxRuntimeError(f"Invalid hf data mount URI: {uri}")
    return repo_ref, revision, unquote(subpath), repo_type


def _fetch_hf_data_mount(mount: DataMount, cache_path: Path) -> Path:
    try:
        from huggingface_hub import snapshot_download
    except ImportError as exc:
        raise SandboxRuntimeError(
            "huggingface_hub is required to materialize hf:// data mounts"
        ) from exc

    repo_id, revision, subpath, repo_type = _parse_hf_data_uri(mount.uri)
    try:
        local_path = Path(
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                repo_type=repo_type or None,
                local_dir=str(cache_path),
            )
        ).resolve()
    except Exception as exc:
        raise SandboxRuntimeError(
            f"hf data mount fetch failed for {mount.uri}: {exc}"
        ) from exc
    return local_path / subpath if subpath else local_path


def _fetch_s3_data_mount(mount: DataMount, cache_path: Path) -> Path:
    parsed = urlparse(mount.uri)
    bucket = parsed.netloc
    key = unquote(parsed.path.lstrip("/"))
    version_id = (parse_qs(parsed.query).get("versionId") or [""])[0]
    if not bucket or not key or not version_id:
        raise SandboxRuntimeError(f"Invalid s3 data mount URI: {mount.uri}")

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        import boto3
    except ImportError:
        aws = shutil.which("aws")
        if not aws:
            raise SandboxRuntimeError(
                "boto3 or the aws CLI is required to materialize s3:// data mounts"
            )
        result = subprocess.run(
            [
                aws,
                "s3api",
                "get-object",
                "--bucket",
                bucket,
                "--key",
                key,
                "--version-id",
                version_id,
                str(cache_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()
            raise SandboxRuntimeError(f"aws s3api get-object failed: {error}")
        return cache_path.resolve()

    try:
        client = boto3.client("s3")
        client.download_file(
            bucket,
            key,
            str(cache_path),
            ExtraArgs={"VersionId": version_id},
        )
    except Exception as exc:
        raise SandboxRuntimeError(
            f"s3 data mount fetch failed for {mount.uri}: {exc}"
        ) from exc
    return cache_path.resolve()


@dataclass(frozen=True)
class SandboxMount:
    """Host path mounted into a sandbox."""

    host_path: str
    container_path: str
    read_only: bool = True

    def to_docker_volume(self) -> str:
        mode = "ro" if self.read_only else "rw"
        return f"{self.host_path}:{self.container_path}:{mode}"


class DataMountResolver:
    """Materialize Stage 1 data URIs to host paths for bind mounts."""

    def __init__(
        self,
        *,
        cache_root: str | Path | None = None,
        base_dir: str | Path | None = None,
        hf_fetcher: DataMountFetcher | None | object = _DEFAULT_REMOTE_FETCHER,
        s3_fetcher: DataMountFetcher | None | object = _DEFAULT_REMOTE_FETCHER,
    ) -> None:
        default_cache = Path.home() / ".cache" / "quantagentbench" / "data"
        self.cache_root = Path(cache_root or default_cache).expanduser()
        self.base_dir = Path(base_dir or os.getcwd()).expanduser()
        self.hf_fetcher = (
            _fetch_hf_data_mount
            if hf_fetcher is _DEFAULT_REMOTE_FETCHER
            else hf_fetcher
        )
        self.s3_fetcher = (
            _fetch_s3_data_mount
            if s3_fetcher is _DEFAULT_REMOTE_FETCHER
            else s3_fetcher
        )

    def resolve(self, mount: DataMount) -> SandboxMount:
        parsed = urlparse(mount.uri)
        scheme = parsed.scheme.lower()
        if scheme == "file":
            host_path = self._resolve_file(mount.uri)
        elif scheme == "hf":
            host_path = self._resolve_cached("hf", mount, self.hf_fetcher)
        elif scheme == "s3":
            host_path = self._resolve_cached("s3", mount, self.s3_fetcher)
        else:
            raise SandboxRuntimeError(f"Unsupported data URI scheme: {scheme}")

        return SandboxMount(
            host_path=str(host_path),
            container_path=mount.target_path,
            read_only=mount.read_only,
        )

    def resolve_all(self, mounts: Sequence[DataMount]) -> tuple[SandboxMount, ...]:
        return tuple(self.resolve(mount) for mount in mounts)

    def _resolve_file(self, uri: str) -> Path:
        raw = uri.removeprefix("file://")
        if raw.startswith("localhost/"):
            raw = raw.removeprefix("localhost")
        path = Path(unquote(raw)).expanduser()
        if not path.is_absolute():
            path = self.base_dir / path
        path = path.resolve()
        if not path.exists():
            raise SandboxRuntimeError(f"file data mount does not exist: {path}")
        return path

    def _resolve_cached(
        self,
        scheme: str,
        mount: DataMount,
        fetcher: DataMountFetcher | None,
    ) -> Path:
        cache_path = self.cache_root / scheme / quote(mount.uri, safe="")
        hf_subpath = ""
        if scheme == "hf":
            _, _, hf_subpath, _ = _parse_hf_data_uri(mount.uri)
        if cache_path.exists():
            if hf_subpath:
                subpath_cache = cache_path / hf_subpath
                if subpath_cache.exists():
                    return subpath_cache.resolve()
            elif not hf_subpath:
                return cache_path.resolve()
        if fetcher is not None:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            fetched_path = Path(fetcher(mount, cache_path)).expanduser().resolve()
            if not fetched_path.exists():
                raise SandboxRuntimeError(
                    f"{scheme} fetcher returned missing path: {fetched_path}"
                )
            return fetched_path
        if not cache_path.exists():
            raise SandboxRuntimeError(
                f"{scheme} data mount is not materialized in cache: {mount.uri}"
            )
        if hf_subpath:
            raise SandboxRuntimeError(
                f"hf data mount subpath is not materialized in cache: {mount.uri}"
            )
        return cache_path.resolve()


@dataclass(frozen=True)
class SandboxCreateRequest:
    """Request to create an isolated sandbox process."""

    image_uri: str
    sandbox_id: str | None = None
    mounts: tuple[SandboxMount, ...] = ()
    data_mounts: tuple[DataMount, ...] = ()
    env: Mapping[str, str] = field(default_factory=dict)
    network_enabled: bool = False
    cpus: str = "1"
    memory: str = "768m"
    resource_limits: Mapping[str, Any] = field(default_factory=dict)
    workdir: str = "/workspace"
    command: tuple[str, ...] = ("sleep", "infinity")
    pull_policy: str = "missing"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "mounts", tuple(self.mounts))
        object.__setattr__(self, "data_mounts", tuple(self.data_mounts))
        object.__setattr__(self, "command", tuple(self.command))

    @classmethod
    def from_spec(
        cls,
        spec: SandboxSpec,
        *,
        sandbox_id: str | None = None,
        mounts: Sequence[SandboxMount] = (),
        data_mounts: Sequence[DataMount] = (),
        env: Mapping[str, str] | None = None,
        network_enabled: bool | None = None,
        workdir: str = "/workspace",
        command: Sequence[str] = ("sleep", "infinity"),
        pull_policy: str = "missing",
        metadata: Mapping[str, Any] | None = None,
    ) -> "SandboxCreateRequest":
        limits = dict(spec.resource_limits)
        effective_network = _bool_limit(
            limits,
            "network_enabled",
            False if network_enabled is None else network_enabled,
        )
        request_metadata = dict(metadata or {})
        if "wall_timeout_seconds" in limits:
            request_metadata["wall_timeout_seconds"] = limits["wall_timeout_seconds"]
        request_metadata.setdefault(
            "sandbox_digest",
            build_sandbox_digest(
                spec.image_uri,
                resource_limits=limits,
                data_mounts=data_mounts,
            ),
        )
        return cls(
            image_uri=spec.image_uri,
            sandbox_id=sandbox_id,
            mounts=tuple(mounts),
            data_mounts=tuple(data_mounts),
            env=dict(env or {}),
            network_enabled=effective_network,
            cpus=_limit_str(limits, "cpus", ("cpu_count",), "1"),
            memory=_memory_limit(limits, "768m"),
            resource_limits=limits,
            workdir=workdir,
            command=tuple(command),
            pull_policy=pull_policy,
            metadata=request_metadata,
        )


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
        data_mount_resolver: DataMountResolver | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.router = router or ToolRouter()
        self._runner = runner
        self.data_mount_resolver = data_mount_resolver or DataMountResolver()
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
        request = self._prepare_request(request)
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
            {
                "sandbox_id": sandbox_id,
                "image_uri": request.image_uri,
                "data_mount_count": len(request.data_mounts),
            },
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

    def _prepare_request(
        self, request: SandboxCreateRequest
    ) -> SandboxCreateRequest:
        metadata = dict(request.metadata)
        metadata.setdefault(
            "sandbox_digest",
            build_sandbox_digest(
                request.image_uri,
                resource_limits=request.resource_limits,
                data_mounts=request.data_mounts,
            ),
        )
        if not request.data_mounts:
            return replace(request, metadata=metadata)

        resolved_mounts = self.data_mount_resolver.resolve_all(request.data_mounts)
        metadata["resolved_data_mounts"] = [
            {
                "uri": data_mount.uri,
                "host_path": sandbox_mount.host_path,
                "target_path": sandbox_mount.container_path,
                "read_only": sandbox_mount.read_only,
            }
            for data_mount, sandbox_mount in zip(
                request.data_mounts, resolved_mounts, strict=True
            )
        ]
        digest = dict(metadata["sandbox_digest"])
        digest["data_mounts"] = [asdict(mount) for mount in request.data_mounts]
        metadata["sandbox_digest"] = digest
        return replace(
            request,
            mounts=(*request.mounts, *resolved_mounts),
            metadata=metadata,
        )

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
        data_mount_resolver: DataMountResolver | None = None,
    ) -> None:
        self.telemetry = telemetry
        self.router = router or ToolRouter()
        self.data_mount_resolver = data_mount_resolver or DataMountResolver()
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
        request = self._prepare_request(request)
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
            {
                "sandbox_id": sandbox_id,
                "runtime": "local",
                "data_mount_count": len(request.data_mounts),
            },
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

    def _prepare_request(
        self, request: SandboxCreateRequest
    ) -> SandboxCreateRequest:
        metadata = dict(request.metadata)
        metadata.setdefault(
            "sandbox_digest",
            build_sandbox_digest(
                request.image_uri,
                resource_limits=request.resource_limits,
                data_mounts=request.data_mounts,
            ),
        )
        if not request.data_mounts:
            return replace(request, metadata=metadata)

        resolved_mounts = self.data_mount_resolver.resolve_all(request.data_mounts)
        metadata["resolved_data_mounts"] = [
            {
                "uri": data_mount.uri,
                "host_path": sandbox_mount.host_path,
                "target_path": sandbox_mount.container_path,
                "read_only": sandbox_mount.read_only,
            }
            for data_mount, sandbox_mount in zip(
                request.data_mounts, resolved_mounts, strict=True
            )
        ]
        digest = dict(metadata["sandbox_digest"])
        digest["data_mounts"] = [asdict(mount) for mount in request.data_mounts]
        metadata["sandbox_digest"] = digest
        return replace(
            request,
            mounts=(*request.mounts, *resolved_mounts),
            metadata=metadata,
        )

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
