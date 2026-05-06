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
from pathlib import Path, PurePosixPath
from typing import Optional, Sequence


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

    Resource limits per image type (based on actual memory profiling):
        Standard (v2.2): peak ~460 MiB → --memory 768m, --cpus 1
        LEAN (v2.2-lean): peak ~520 MiB → --memory 1g, --cpus 2
    """

    # Resource presets keyed by image name substring.
    # Each maps to (memory_limit, cpu_limit).
    _RESOURCE_PRESETS: dict[str, tuple[str, str]] = {
        "lean": ("1g", "2"),
        "_default": ("768m", "1"),
    }
    _HOST_MOUNT_DIR_MODE = 0o755

    def __init__(
        self, docker_image: str = "quant-tutor-env:v2.2", use_docker: bool = True
    ):
        self.docker_image = docker_image
        self.use_docker = use_docker and self._docker_available()
        self._containers: dict[str, ContainerInfo] = {}
        self._workspaces: dict[str, str] = {}
        self._executors: dict[str, _ExecutorHandle] = {}
        self._executor_env: dict[str, dict[str, str]] = {}  # env_vars per container

    def _docker_available(self) -> bool:
        try:
            result = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
            return result.returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    @classmethod
    def _resolve_resources(cls, image: str) -> tuple[str, str]:
        """Return (memory_limit, cpu_limit) for a Docker image name."""
        image_lower = image.lower()
        for key, preset in cls._RESOURCE_PRESETS.items():
            if key != "_default" and key in image_lower:
                return preset
        return cls._RESOURCE_PRESETS["_default"]

    @staticmethod
    def _apply_resource_limits(
        mem_limit: str,
        cpu_limit: str,
        resource_limits: dict | None,
    ) -> tuple[str, str]:
        if not resource_limits:
            return mem_limit, cpu_limit
        if resource_limits.get("memory"):
            mem_limit = str(resource_limits["memory"])
        elif resource_limits.get("memory_mb"):
            mem_limit = f"{int(resource_limits['memory_mb'])}m"
        cpu_value = resource_limits.get("cpus", resource_limits.get("cpu_count"))
        if cpu_value not in (None, ""):
            cpu_limit = str(cpu_value)
        return mem_limit, cpu_limit

    @staticmethod
    def _make_host_mount_dir_readable(path: str) -> None:
        os.chmod(path, ContainerManager._HOST_MOUNT_DIR_MODE)

    @staticmethod
    def _chown_workspace_to_sandbox(container_id: str) -> None:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "root",
                container_id,
                "chown",
                "-R",
                "sandbox:sandbox",
                "/workspace",
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(
                "docker workspace chown failed: "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )

    @staticmethod
    def _restore_workspace_host_ownership_once(
        container_id: str,
    ) -> subprocess.CompletedProcess:
        uid = os.getuid()
        gid = os.getgid()
        return subprocess.run(
            [
                "docker",
                "exec",
                "--user",
                "root",
                container_id,
                "sh",
                "-c",
                (
                    f"chown -R {uid}:{gid} /workspace 2>/dev/null "
                    "|| chmod -R a+rwX /workspace 2>/dev/null || true"
                ),
            ],
            capture_output=True,
            text=True,
        )

    @classmethod
    def _restore_workspace_host_ownership(cls, container_id: str) -> bool:
        result = cls._restore_workspace_host_ownership_once(container_id)
        if result.returncode == 0:
            return True

        state = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", container_id],
            capture_output=True,
            text=True,
        )
        if state.returncode == 0 and state.stdout.strip().lower() == "false":
            started = subprocess.run(
                ["docker", "start", container_id],
                capture_output=True,
                text=True,
            )
            if started.returncode == 0:
                result = cls._restore_workspace_host_ownership_once(container_id)
                return result.returncode == 0
        return False

    def restore_workspace_host_ownership(self, container_id: str) -> bool:
        if not self.use_docker or container_id.startswith("local_"):
            return True
        return self._restore_workspace_host_ownership(container_id)

    @staticmethod
    def _normalize_data_mounts(data_mounts: Sequence[object] | None):
        if not data_mounts:
            return ()
        from platform_api.contracts import DataMount

        normalized = []
        for item in data_mounts:
            if isinstance(item, DataMount):
                normalized.append(item)
                continue
            if hasattr(item, "model_dump"):
                payload = item.model_dump()
            elif isinstance(item, dict):
                payload = dict(item)
            else:
                payload = {
                    "uri": getattr(item, "uri"),
                    "target_path": getattr(item, "target_path"),
                    "read_only": getattr(item, "read_only", True),
                }
            normalized.append(
                DataMount(
                    uri=str(payload["uri"]),
                    target_path=str(payload["target_path"]),
                    read_only=bool(payload.get("read_only", True)),
                )
            )
        return tuple(normalized)

    @staticmethod
    def _prepare_nested_mount_targets(
        mounts: Sequence[object],
        parent_mounts: Sequence[tuple[str | None, str]],
    ) -> None:
        for mount in mounts:
            container_path = PurePosixPath(getattr(mount, "container_path"))
            host_path = Path(str(getattr(mount, "host_path")))
            for parent_host, parent_container in parent_mounts:
                if not parent_host:
                    continue
                parent_path = PurePosixPath(parent_container)
                try:
                    relative_target = container_path.relative_to(parent_path)
                except ValueError:
                    continue
                if not relative_target.parts:
                    continue
                staged_target = Path(parent_host, *relative_target.parts)
                if host_path.is_dir():
                    staged_target.mkdir(parents=True, exist_ok=True)
                else:
                    staged_target.parent.mkdir(parents=True, exist_ok=True)
                    staged_target.touch(exist_ok=True)

    def create_container(
        self,
        task_id: str,
        data_dir: str,
        docs_dir: str,
        user_code_dir: Optional[str] = None,
        sandbox_image: Optional[str] = None,
        network_enabled: bool = False,
        lean_data_dir: Optional[str] = None,
        custom_data_dir: Optional[str] = None,
        data_mounts: Sequence[object] | None = None,
        resource_limits: dict | None = None,
    ) -> ContainerInfo:
        """Create a sandboxed Docker container (or local workspace fallback).

        Args:
            task_id: Unique identifier for this task run.
            data_dir: Host-side directory to mount as /data (read-only).
                      May be a staged/filtered directory.
            docs_dir: Host-side directory to mount as /docs (read-only).
                      May be a staged/filtered directory.
            user_code_dir: If provided, mounted as /user_code (read-only).
            lean_data_dir: If provided, mounted as /lean/Data (LEAN metadata only).
            sandbox_image: Docker image override (default: self.docker_image).
        """
        image = sandbox_image or self.docker_image
        workspace = tempfile.mkdtemp(prefix=f"qtb_{task_id}_")
        normalized_data_mounts = self._normalize_data_mounts(data_mounts)

        if self.use_docker:
            self._make_host_mount_dir_readable(workspace)
            # Docker cannot create a nested bind mount like /data/custom when /data
            # itself is a read-only bind mount. Pre-create the mountpoint inside the
            # staged task-data directory so the 12-col custom-data bind can attach.
            if custom_data_dir and data_dir and os.path.isdir(data_dir):
                os.makedirs(os.path.join(data_dir, "custom"), exist_ok=True)

            mounts = [
                f"-v {workspace}:/workspace",
                f"-v {data_dir}:/data:ro",
                f"-v {docs_dir}:/docs:ro",
            ]
            if user_code_dir:
                mounts.append(f"-v {user_code_dir}:/user_code:ro")
            if lean_data_dir:
                mounts.append(f"-v {lean_data_dir}:/lean/Data:ro")
            if custom_data_dir:
                mounts.append(f"-v {custom_data_dir}:/data/custom:ro")
            if normalized_data_mounts:
                from platform_api.runtime import DataMountResolver

                resolver = DataMountResolver(base_dir=Path.cwd())
                resolved_data_mounts = resolver.resolve_all(normalized_data_mounts)
                self._prepare_nested_mount_targets(
                    resolved_data_mounts,
                    (
                        (data_dir, "/data"),
                        (docs_dir, "/docs"),
                        (user_code_dir, "/user_code"),
                        (lean_data_dir, "/lean/Data"),
                        (custom_data_dir, "/data/custom"),
                    ),
                )
                mounts.extend(
                    f"-v {mount.to_docker_volume()}" for mount in resolved_data_mounts
                )

            network_flag = "--network bridge" if network_enabled else "--network none"
            network_mode = "bridge" if network_enabled else "none"
            mem_limit, cpu_limit = self._resolve_resources(image)
            mem_limit, cpu_limit = self._apply_resource_limits(
                mem_limit,
                cpu_limit,
                resource_limits,
            )
            cmd = (
                f"docker run -d --name qtb_{task_id}_{int(time.time())} "
                f"{network_flag} --cpus {cpu_limit} --memory {mem_limit} "
                f"{' '.join(mounts)} "
                f"{image} sleep infinity"
            )
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
            if result.returncode != 0:
                raise RuntimeError(
                    f"docker run failed (exit {result.returncode}): "
                    f"{result.stderr.strip() or result.stdout.strip()}"
                )
            container_id = result.stdout.strip()[:12]
            if not container_id:
                raise RuntimeError("docker run returned empty container ID")

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
            subprocess.run(
                [
                    "docker",
                    "cp",
                    str(bench_core / "trial_manager.py"),
                    f"{container_id}:/opt/bench/trial_manager.py",
                ],
                capture_output=True,
            )

            # Inject updated run_backtest.sh (the Docker image may have a stale
            # copy; this ensures the container always uses the latest version).
            run_backtest_sh = (
                Path(__file__).parent.parent / "docker" / "run_backtest.sh"
            )
            if run_backtest_sh.exists():
                subprocess.run(
                    [
                        "docker",
                        "cp",
                        str(run_backtest_sh),
                        f"{container_id}:/usr/local/bin/run_backtest",
                    ],
                    capture_output=True,
                )

            # Inject shared lean_config.py helper. run_backtest.sh imports this
            # from /lean/helpers/lean_config.py; without it, containers built
            # before the helper existed raise ModuleNotFoundError at [2b/4].
            lean_config_py = Path(__file__).parent.parent / "docker" / "lean_config.py"
            if lean_config_py.exists():
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "--user",
                        "root",
                        container_id,
                        "mkdir",
                        "-p",
                        "/lean/helpers",
                    ],
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "docker",
                        "cp",
                        str(lean_config_py),
                        f"{container_id}:/lean/helpers/lean_config.py",
                    ],
                    capture_output=True,
                )

            # Inject the latest 12-col strategy injector so run_backtest.sh does
            # not fall back to the stale image copy or skip injection entirely.
            inject_strategy_py = (
                Path(__file__).parent.parent / "scripts" / "inject_strategy.py"
            )
            if inject_strategy_py.exists():
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "--user",
                        "root",
                        container_id,
                        "mkdir",
                        "-p",
                        "/opt/bench/scripts",
                    ],
                    capture_output=True,
                )
                subprocess.run(
                    [
                        "docker",
                        "cp",
                        str(inject_strategy_py),
                        f"{container_id}:/opt/bench/scripts/inject_strategy.py",
                    ],
                    capture_output=True,
                )
                # docker cp does NOT preserve execute permissions — fix explicitly.
                # Must use --user root because the container default user (sandbox)
                # cannot chmod files owned by root.
                subprocess.run(
                    [
                        "docker",
                        "exec",
                        "--user",
                        "root",
                        container_id,
                        "chmod",
                        "+x",
                        "/usr/local/bin/run_backtest",
                    ],
                    capture_output=True,
                )
            self._chown_workspace_to_sandbox(container_id)
        else:
            container_id = f"local_{task_id}_{int(time.time())}"
            # Local fallback cannot enforce isolation; use host networking semantics.
            network_mode = "host"
            network_enabled = True

        # Warm up LEAN compilation for lean containers so the first
        # run_backtest call only does an incremental build (~13s vs ~190s).
        # Write a dummy Algorithm.cs to trigger a real recompile of the
        # Algorithm.CSharp project; without this the warmup is a no-op.
        if self.use_docker and "lean" in image.lower():
            subprocess.run(
                [
                    "docker",
                    "exec",
                    container_id,
                    "bash",
                    "-c",
                    "echo 'using QuantConnect; using QuantConnect.Algorithm; "
                    "using QuantConnect.Data; namespace QuantConnect.Algorithm"
                    ".CSharp { public class Algorithm : QCAlgorithm { "
                    "public override void Initialize() { SetStartDate(2022,1,1); "
                    'SetEndDate(2025,1,1); SetAccountCurrency("USDT"); '
                    "SetCash(100000); } "
                    "public override void OnData(Slice d) {} } }' "
                    "> /lean/Algorithm.CSharp/Algorithm.cs && "
                    "cd /lean && MSBUILDDISABLENODEREUSE=1 "
                    "dotnet build Algorithm.CSharp/QuantConnect.Algorithm.CSharp.csproj "
                    "-c Debug --no-restore > /dev/null 2>&1",
                ],
                capture_output=True,
                timeout=300,
            )

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

    def start_executor(
        self,
        container_id: str,
        timeout: float = 15.0,
        env_vars: dict[str, str] | None = None,
    ) -> None:
        """Start the tool_executor.py daemon inside a Docker container.

        Launches ``docker exec -i`` with the Python executor script and
        waits for the readiness signal.

        Args:
            container_id: The Docker container ID.
            timeout: Seconds to wait for the readiness signal.
            env_vars: Extra environment variables to pass into the container
                (e.g. ``{"QTB_MAX_BACKTEST_TRIALS": "5"}``).

        Raises:
            RuntimeError: If the executor fails to start within *timeout*.
        """
        if not self.use_docker or container_id.startswith("local_"):
            return  # No executor needed in local mode

        cmd = [
            "docker",
            "exec",
            "-i",
            "-e",
            "PYTHONPATH=/opt/bench",
        ]
        for k, v in (env_vars or {}).items():
            cmd.extend(["-e", f"{k}={v}"])
        cmd.extend(
            [
                container_id,
                "python3",
                "-u",
                "/opt/bench/tool_executor.py",
            ]
        )

        proc = subprocess.Popen(
            cmd,
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
                        # Remember env_vars for _restart_executor
                        if env_vars:
                            self._executor_env[container_id] = env_vars
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
        On timeout, the executor is killed and restarted so subsequent calls
        are not blocked by a stuck process.

        Returns:
            The tool's result string.

        Raises:
            RuntimeError: If the executor is dead or times out.
        """
        handle = self._executors.get(container_id)
        if handle is None or not handle.alive:
            # Attempt auto-restart if executor died or was killed after a prior timeout.
            self._restart_executor(container_id)
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

            # Timeout: kill the stuck executor so it can be restarted on next call.
            handle.alive = False
            handle.proc.kill()
            raise RuntimeError(f"Tool '{tool_name}' timed out after {timeout}s")

    def _restart_executor(self, container_id: str) -> None:
        """Kill a dead/stuck executor and start a fresh one."""
        old = self._executors.pop(container_id, None)
        if old is not None:
            if old.proc.poll() is None:
                old.proc.kill()
                try:
                    old.proc.wait(timeout=5)
                except Exception:
                    pass
        try:
            saved_env = self._executor_env.get(container_id)
            self.start_executor(container_id, env_vars=saved_env)
        except RuntimeError:
            pass  # Caller will check handle.alive

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
            self.restore_workspace_host_ownership(container_id)
            subprocess.run(
                f"docker rm -f {container_id}", shell=True, capture_output=True
            )
        workspace = self._workspaces.pop(container_id, None)
        if workspace and os.path.exists(workspace):
            shutil.rmtree(workspace, ignore_errors=True)
        self._containers.pop(container_id, None)
