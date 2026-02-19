"""MCP Server Registry for QuantTutorBench.

Creates configured MCPProxy instances with the correct tools for each task.
"""

import random
from typing import Optional

from mcp_servers.core.tools import CORE_TOOLS
from mcp_servers.distractors.distractor_tools import DISTRACTOR_TOOLS
from mcp_servers.proxy.mcp_proxy import MCPProxy

# Tools that execute arbitrary code and must run inside Docker
CODE_EXEC_TOOLS = {"shell_exec", "run_backtest", "plot_chart"}


def create_proxy_for_task(
    core_tool_names: list[str],
    distractor_pool: list[str],
    num_distractors: int = 5,
    seed: Optional[int] = None,
    container_manager=None,
    container_id: Optional[str] = None,
    workspace_path: Optional[str] = None,
    use_docker: bool = False,
) -> MCPProxy:
    """Create a configured MCPProxy for a specific task.

    Args:
        core_tool_names: List of core tool names to make available.
        distractor_pool: Pool of distractor tool names to sample from.
        num_distractors: Number of distractors to include.
        seed: Random seed for reproducible distractor selection.
        container_manager: ContainerManager instance for Docker execution.
        container_id: Docker container ID for code execution tools.
        workspace_path: Host-side workspace path (bind-mounted into container).
        use_docker: Whether Docker execution is active.

    Returns:
        Configured MCPProxy instance.
    """
    proxy = MCPProxy()

    # Register core tools
    for name in core_tool_names:
        if name in CORE_TOOLS:
            tool_info = CORE_TOOLS[name]

            # Code execution tools: use Docker-aware wrappers when container is available
            if name in CODE_EXEC_TOOLS and container_manager is not None:
                from mcp_servers.core.tool_wrappers import (
                    make_plot_chart,
                    make_run_backtest,
                    make_shell_exec,
                )

                factories = {
                    "shell_exec": make_shell_exec,
                    "run_backtest": make_run_backtest,
                    "plot_chart": make_plot_chart,
                }
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

    # Sample and register distractors
    available = [d for d in distractor_pool if d in DISTRACTOR_TOOLS]
    if seed is not None:
        rng = random.Random(seed)
    else:
        rng = random.Random()

    selected = rng.sample(available, min(num_distractors, len(available)))

    for name in selected:
        info = DISTRACTOR_TOOLS[name]
        proxy.register_distractor(
            name=name,
            error_message=info["error"],
            description=info["description"],
            params=info.get("params", {}),
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
