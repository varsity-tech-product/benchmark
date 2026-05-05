"""MCP Server Registry for QuantTutorBench.

Creates configured MCPProxy instances with the correct tools for each task.
"""

import random
from typing import TYPE_CHECKING, Optional

from mcp_servers.core.tools import CORE_TOOLS
from mcp_servers.distractors.distractor_tools import DISTRACTOR_TOOLS
from mcp_servers.proxy.mcp_proxy import MCPProxy

if TYPE_CHECKING:
    from mcp_servers.session import TutoringSession

# Total tool slots available to the agent (core + convenient + distractors).
# Session tools (get_session_info, send_message) do NOT count toward this cap.
_TOTAL_TOOL_SLOTS = 15


def _register_core_tool(
    proxy, name, container_manager, container_id, workspace_path, use_docker
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
        from mcp_servers.core.tool_wrappers import make_container_tool

        func = make_container_tool(
            tool_name=name,
            local_func=tool_info["func"],
            container_manager=container_manager,
            container_id=container_id,
            use_docker=use_docker,
        )
    elif name == "shell_exec" and container_manager is not None:
        # Local mode: shell_exec needs explicit cwd=workspace_path.
        from mcp_servers.core.tool_wrappers import make_shell_exec

        func = make_shell_exec(
            container_manager,
            container_id,
            workspace_path,
            use_docker=False,
        )
    else:
        func = tool_info["func"]

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
) -> None:
    """Register task-specific tools on an existing MCPProxy.

    Same logic as ``create_proxy_for_task`` but operates on an existing
    proxy instance.  Used by the Exam Server to add domain tools to the
    same proxy that already hosts exam-phase tools.
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
        )
    for name in convenient_tool_names:
        _register_core_tool(
            proxy,
            name,
            container_manager,
            container_id,
            workspace_path,
            use_docker,
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


def register_session_tools(
    proxy: MCPProxy,
    session: "TutoringSession",
) -> None:
    """Register session-management tools on an existing proxy.

    These tools are infrastructure (like ``get_environment_info``) and do
    NOT count toward the 15-slot tool budget or tool_usage scoring.

    Args:
        proxy: The MCPProxy to add tools to.
        session: TutoringSession that backs the tool implementations.
    """
    proxy.register_tool(
        name="get_session_info",
        func=session.handle_get_session_info,
        description=(
            "Get the tutoring task description, user profile, and the "
            "user's opening message. Call this first to understand what "
            "you need to teach."
        ),
        params={"type": "object", "properties": {}, "required": []},
    )
    proxy.register_tool(
        name="send_message",
        func=session.handle_send_message,
        description=(
            "Send a message to the user. Returns the user's reply "
            "and session status. Use this to communicate with the user "
            "during the tutoring session. "
            "Optionally include 'reasoning' to record your private rationale "
            "for this turn — it is logged for analysis and is NOT shown to "
            "the user."
        ),
        params={
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "Your message to the user.",
                },
                "reasoning": {
                    "type": "string",
                    "description": (
                        "Private rationale for this turn — why you chose this "
                        "wording, what you expect to learn from the reply, or "
                        "what hypothesis you are testing. Recorded in tool logs "
                        "for post-hoc analysis. Not delivered to the user."
                    ),
                },
            },
            "required": ["text"],
        },
    )
