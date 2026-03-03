"""MCP Server Registry for QuantTutorBench.

Creates configured MCPProxy instances with the correct tools for each task.
"""

import random
from typing import Optional

from mcp_servers.core.tools import CORE_TOOLS
from mcp_servers.distractors.distractor_tools import DISTRACTOR_TOOLS
from mcp_servers.proxy.mcp_proxy import MCPProxy

# Only shell_exec executes arbitrary code and needs Docker routing.
# run_backtest and plot_chart are now self-contained (pandas/numpy/exec)
# and don't need subprocess or Docker wrapping.
CODE_EXEC_TOOLS = {"shell_exec"}

# Total tool slots available to the agent (core + convenient + distractors).
_TOTAL_TOOL_SLOTS = 15


def _register_core_tool(
    proxy, name, container_manager, container_id, workspace_path, use_docker
):
    """Register a single core/convenient tool on the proxy."""
    if name not in CORE_TOOLS:
        return
    tool_info = CORE_TOOLS[name]

    if name in CODE_EXEC_TOOLS and container_manager is not None:
        from mcp_servers.core.tool_wrappers import make_shell_exec

        factories = {"shell_exec": make_shell_exec}
        func = factories[name](
            container_manager,
            container_id,
            workspace_path,
            use_docker,
        )
    else:
        func = tool_info["func"]

    proxy.register_tool(
        name=name,
        func=func,
        description=tool_info["description"],
        params=tool_info.get("params", {}),
    )


def create_proxy_for_task(
    core_tool_names: list[str],
    convenient_tool_names: list[str] | None = None,
    seed: Optional[int] = None,
    container_manager=None,
    container_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    use_docker: bool = False,
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
    proxy = MCPProxy()

    # Register core tools
    for name in core_tool_names:
        _register_core_tool(
            proxy,
            name,
            container_manager,
            container_id,
            workspace_path,
            use_docker,
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


def get_all_tool_schemas() -> dict:
    """Get schemas for all tools (core + distractor)."""
    schemas = {}
    for name, info in CORE_TOOLS.items():
        schemas[name] = {
            "name": name,
            "description": info["description"],
            "parameters": info.get("params", {}),
            "type": "core",
        }
    for name, info in DISTRACTOR_TOOLS.items():
        schemas[name] = {
            "name": name,
            "description": info["description"],
            "parameters": info.get("params", {}),
            "type": "distractor",
        }
    return schemas
