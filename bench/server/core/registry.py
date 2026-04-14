"""MCP Server Registry for QuantTutorBench.

Creates configured MCPProxy instances with the correct tools for each task.
"""

import os
import random
import threading
from contextlib import contextmanager
from functools import wraps
from typing import Optional

from server.core.distractors.distractor_tools import DISTRACTOR_TOOLS
from server.core.proxy import MCPProxy
from server.core.tools import CORE_TOOLS

# Total task-visible tool slots available to the agent
# (core + convenient + distractors).
_TOTAL_TOOL_SLOTS = 15
_ENV_LOCK = threading.RLock()


@contextmanager
def _temporary_env(overrides: dict[str, str] | None):
    """Temporarily apply environment overrides for local-mode tool calls."""
    if not overrides:
        yield
        return

    with _ENV_LOCK:
        missing: list[str] = []
        previous: dict[str, str] = {}
        for key, value in overrides.items():
            if key in os.environ:
                previous[key] = os.environ[key]
            else:
                missing.append(key)
            os.environ[key] = value
        try:
            yield
        finally:
            for key, value in previous.items():
                os.environ[key] = value
            for key in missing:
                os.environ.pop(key, None)


def _wrap_local_tool_with_env(func, env_overrides: dict[str, str] | None):
    """Wrap a local tool so it sees per-session directory and context env vars."""
    if not env_overrides:
        return func

    @wraps(func)
    def _wrapped(*args, **kwargs):
        with _temporary_env(env_overrides):
            return func(*args, **kwargs)

    return _wrapped


def _register_core_tool(
    proxy,
    name,
    container_manager,
    container_id,
    workspace_path,
    use_docker,
    env_overrides=None,
):
    """Register a single core/convenient tool on the proxy.

    In Docker mode, ALL core tools are routed through the container
    executor daemon via ``make_container_tool()``.  In local mode,
    ``shell_exec`` gets a subprocess wrapper (cwd=workspace) and the
    remaining tools use their original implementations.
    """
    if name not in CORE_TOOLS:
        return
    tool_info = CORE_TOOLS[name]

    if use_docker and container_manager is not None:
        # Docker mode: every tool goes through the in-container executor.
        from server.core.tools.tool_wrappers import make_container_tool

        func = make_container_tool(
            tool_name=name,
            local_func=tool_info["func"],
            container_manager=container_manager,
            container_id=container_id,
            use_docker=use_docker,
        )
    elif name == "shell_exec" and container_manager is not None:
        # Local mode: shell_exec needs explicit cwd=workspace_path.
        from server.core.tools.tool_wrappers import make_shell_exec

        func = make_shell_exec(
            container_manager,
            container_id,
            workspace_path,
            use_docker=False,
        )
    else:
        func = tool_info["func"]

    if not use_docker:
        func = _wrap_local_tool_with_env(func, env_overrides)

    proxy.register_tool(
        name=name,
        func=func,
        description=tool_info["description"],
        params=tool_info.get("params", {}),
    )


def populate_proxy_for_task(
    proxy: MCPProxy,
    core_tool_names: list[str],
    convenient_tool_names: list[str] | None = None,
    seed: Optional[int] = None,
    container_manager=None,
    container_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    use_docker: bool = False,
    env_overrides: dict[str, str] | None = None,
) -> None:
    """Register task-specific tools on an existing MCPProxy.

    Same logic as ``create_proxy_for_task`` but operates on an existing
    proxy instance.
    """
    convenient_tool_names = convenient_tool_names or []

    for name in core_tool_names:
        _register_core_tool(
            proxy,
            name,
            container_manager,
            container_id,
            workspace_path,
            use_docker,
            env_overrides=env_overrides,
        )
    for name in convenient_tool_names:
        _register_core_tool(
            proxy,
            name,
            container_manager,
            container_id,
            workspace_path,
            use_docker,
            env_overrides=env_overrides,
        )

    excluded = set(core_tool_names) | set(convenient_tool_names)
    available = [d for d in DISTRACTOR_TOOLS if d not in excluded]
    n = _TOTAL_TOOL_SLOTS - len(core_tool_names) - len(convenient_tool_names)
    n = max(0, min(n, len(available)))
    rng = random.Random(seed) if seed is not None else random.Random()
    selected = rng.sample(available, n)

    for name in selected:
        info = DISTRACTOR_TOOLS[name]
        proxy.register_distractor(
            name=name,
            error_message=info.get("error", ""),
            description=info["description"],
            params=info.get("params", {}),
            func=info.get("func"),
        )


def create_proxy_for_task(
    core_tool_names: list[str],
    convenient_tool_names: list[str] | None = None,
    seed: Optional[int] = None,
    container_manager=None,
    container_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    use_docker: bool = False,
    env_overrides: dict[str, str] | None = None,
) -> MCPProxy:
    """Create a configured MCPProxy for a specific task.

    Args:
        core_tool_names: List of core tool names to make available.
        convenient_tool_names: Optional list of convenient tool names
            (bonus-eligible shortcuts).  Registered as regular tools
            but tracked separately for evaluation.
        seed: Random seed for reproducible distractor selection.
        container_manager: ContainerManager instance for Docker execution.
        container_id: Docker container ID for code execution tools.
        workspace_path: Host-side workspace path (bind-mounted into container).
        use_docker: Whether Docker execution is active.

    Returns:
        Configured MCPProxy instance.
    """
    convenient_tool_names = convenient_tool_names or []
    proxy = MCPProxy(workspace_path=workspace_path)

    # Register core tools
    for name in core_tool_names:
        _register_core_tool(
            proxy,
            name,
            container_manager,
            container_id,
            workspace_path,
            use_docker,
            env_overrides=env_overrides,
        )

    # Register convenient tools (same mechanism as core — agent sees no difference)
    for name in convenient_tool_names:
        _register_core_tool(
            proxy,
            name,
            container_manager,
            container_id,
            workspace_path,
            use_docker,
            env_overrides=env_overrides,
        )

    # Sample distractors from global pool, excluding core and convenient.
    excluded = set(core_tool_names) | set(convenient_tool_names)
    available = [d for d in DISTRACTOR_TOOLS if d not in excluded]

    n = _TOTAL_TOOL_SLOTS - len(core_tool_names) - len(convenient_tool_names)
    n = max(0, min(n, len(available)))

    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random.Random()

    selected = rng.sample(available, n)

    for name in selected:
        info = DISTRACTOR_TOOLS[name]
        proxy.register_distractor(
            name=name,
            error_message=info.get("error", ""),
            description=info["description"],
            params=info.get("params", {}),
            func=info.get("func"),  # functional distractors have a callable
        )

    return proxy
