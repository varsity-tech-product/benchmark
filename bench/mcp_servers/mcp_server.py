"""QuantTutorBench MCP Server.

Wraps the benchmark environment (proxy + session) as a standard MCP server.
Third-party agents connect via stdio transport and interact through tool calls.

Usage (standalone):
    python -m mcp_servers.mcp_server --task S01_ma_crossover --persona developer_crossover

Usage (from orchestrator):
    from mcp_servers.mcp_server import create_mcp_server, run_server_stdio
    server = create_mcp_server(proxy)
    run_server_stdio(server)
"""

import asyncio
import json
import logging
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger(__name__)


def create_mcp_server(proxy, name: str = "QuantTutorBench") -> Server:
    """Create an MCP server backed by a configured MCPProxy.

    All tools registered on the proxy (core, convenient, distractor,
    session) are exposed as MCP tools.  Tool calls are routed through
    ``proxy.call_tool()`` which handles logging, truncation, deadlines,
    and distractor detection.

    Args:
        proxy: Configured MCPProxy instance (with session tools registered).
        name: Server name advertised to clients.

    Returns:
        An ``mcp.server.Server`` ready to be run via a transport.
    """
    server = Server(name)

    @server.list_tools()
    async def handle_list_tools() -> list[Tool]:
        tools = []
        for schema in proxy.get_available_tools():
            tools.append(
                Tool(
                    name=schema["name"],
                    description=schema.get("description", ""),
                    inputSchema=schema.get(
                        "parameters", {"type": "object", "properties": {}}
                    ),
                )
            )
        return tools

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict[str, Any]
    ) -> list[TextContent]:
        # Route through proxy (synchronous) — run in thread to avoid
        # blocking the async event loop.
        result = await asyncio.to_thread(proxy.call_tool, name, **arguments)
        return [TextContent(type="text", text=str(result))]

    return server


async def run_server_stdio(server: Server) -> None:
    """Run the MCP server over stdio transport.

    The server reads JSON-RPC messages from stdin and writes responses
    to stdout.  This is the standard transport for Claude Desktop and
    Claude Code MCP integrations.
    """
    async with stdio_server() as (read_stream, write_stream):
        init_options = server.create_initialization_options()
        await server.run(read_stream, write_stream, init_options)


# ──────────────────────────────────────────────────────────────
# Standalone entry point
# ──────────────────────────────────────────────────────────────


def _build_standalone_server(task_id: str, persona_id: str, use_docker: bool = True):
    """Build a fully configured MCP server for a single task.

    Performs all PHASE 1 setup (data staging, container creation, tool
    registration, session creation) and returns a ready-to-serve MCP server.
    """
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent))

    from config.benchmark_config import DATASET_REVISION
    from config.llm_config import SIMULATOR_DEFAULT_MODEL
    from config.prompt_config import build_scenario, build_user_description
    from orchestrator.container_manager import ContainerManager

    from mcp_servers.registry import create_proxy_for_task, register_session_tools
    from mcp_servers.session import TutoringSession
    from mcp_servers.student_sim import StudentSimulator
    from mcp_servers.tc_checker import TCChecker, parse_tc_items
    from scripts.data_manager import ensure_data

    # Load task and persona
    task = _load_task(task_id)
    persona = _load_persona(persona_id)

    # Download data
    sandbox_img = task.environment.sandbox_image if task.environment else ""
    if sandbox_img and "lean" in sandbox_img:
        paths = ensure_data(series="lean", revision=DATASET_REVISION)
    else:
        paths = ensure_data(series="normal", revision=DATASET_REVISION)

    # Create container
    container_manager = ContainerManager(use_docker=use_docker)
    data_files = task.environment.data_files if task.environment else []
    docs_available = task.environment.docs_available if task.environment else []

    # Import orchestrator for staged dir creation
    from orchestrator.orchestrator import BenchmarkOrchestrator

    orch = BenchmarkOrchestrator(use_docker=use_docker)
    custom_data_dir = getattr(paths, "custom_data", None)
    staged_data_dir, staged_docs_dir, staged_temp_dirs = orch._create_staged_dirs(
        data_files,
        docs_available,
        data_search_dirs=paths.data_search_dirs,
        docs_dir=paths.docs,
        force_temp_data_dir=bool(custom_data_dir),
    )

    student_code_dir = paths.student_code if task.category.value == "debug" else None
    container = container_manager.create_container(
        task_id=f"{task_id}_{persona_id}_mcp",
        data_dir=staged_data_dir,
        docs_dir=staged_docs_dir,
        student_code_dir=student_code_dir,
        sandbox_image=(task.environment.sandbox_image if task.environment else None),
        network_enabled=(
            task.environment.network_enabled if task.environment else False
        ),
        lean_data_dir=paths.lean_data,
        custom_data_dir=custom_data_dir,
    )

    max_bt = task.environment.max_backtest_trials if task.environment else 0
    task_core_tools = list(task.environment.core_mcp_tools if task.environment else [])
    sandbox_image = task.environment.sandbox_image if task.environment else ""
    is_lean_task = "lean" in str(sandbox_image).lower() or "run_lean_backtest" in task_core_tools
    if is_lean_task and "get_lean_template" not in task_core_tools:
        task_core_tools.append("get_lean_template")

    session_context = {
        "category": task.category.value,
        "requires_code": bool(task.requires_code),
        "docs_available": list(docs_available),
        "max_backtest_trials": max_bt,
        "sandbox_image": sandbox_image,
        "student_code_available": bool(student_code_dir),
    }
    task_id_upper = task_id.upper()
    if task.category.value == "debug" or task.sample_code:
        template_type = "debug"
    elif task_id_upper.startswith("I01"):
        template_type = "single_symbol"
    elif task_id_upper.startswith(("I07", "I08", "I09", "I10")):
        template_type = "framework"
    elif task_id_upper.startswith(("I02", "I03", "I04", "I05", "I06")):
        template_type = "multi_symbol"
    else:
        template_type = "generic"
    lean_template_context = {
        "category": task.category.value,
        "requires_code": bool(task.requires_code),
        "template_type": template_type,
        "expects_universe": template_type == "multi_symbol",
        "sandbox_image": sandbox_image,
        "student_code_available": bool(student_code_dir),
    }

    if container_manager.use_docker:
        container_manager.start_executor(
            container.container_id,
            env_vars={
                "QTB_MAX_BACKTEST_TRIALS": str(max_bt),
                "LEAN_RUN_TIMEOUT": "300",
                "QTB_SESSION_CONTEXT_JSON": json.dumps(session_context),
                "QTB_LEAN_TEMPLATE_CONTEXT_JSON": json.dumps(lean_template_context),
            },
        )

    # Create proxy with tools
    proxy = create_proxy_for_task(
        core_tool_names=task_core_tools,
        convenient_tool_names=(
            task.ground_truth.convenient_tools if task.ground_truth else []
        ),
        seed=task.seed if task.seed is not None else hash(f"{task_id}_0"),
        container_manager=container_manager,
        container_id=container.container_id,
        workspace_path=container.workspace_path,
        use_docker=container_manager.use_docker,
    )

    # Create session
    tc_text = task.ground_truth.termination_criteria if task.ground_truth else None
    tc_items = parse_tc_items(tc_text, task.category.value, persona_id=persona_id)
    has_tc = tc_items is not None

    from config.model_resolver import resolve_deepeval_model

    student_sim = StudentSimulator(
        scenario=build_scenario(task, persona_id, has_incremental_tc=has_tc),
        user_description=build_user_description(persona, has_incremental_tc=has_tc),
        model=resolve_deepeval_model(SIMULATOR_DEFAULT_MODEL),
    )

    tc_checker = TCChecker(tc_items) if tc_items else None

    # GoalChecker for non-TC categories (data_analysis, end_to_end, adversarial).
    # Replicates DeepEval stop_conversation() behavior.
    # Logic aligned with build_conversational_golden() (simulation.py:438-450).
    goal_checker = None
    if tc_items is None and task.ground_truth:
        gt = task.ground_truth
        if gt.termination_criteria:
            if task.category.value in ("implementation", "end_to_end", "debug"):
                expected_outcome = (
                    f"{gt.expected_outcome}\n\n"
                    f"Observable completion criteria:\n"
                    f"{gt.termination_criteria}"
                )
            else:
                expected_outcome = gt.termination_criteria
        else:
            expected_outcome = gt.expected_outcome
        if expected_outcome:
            from mcp_servers.session import GoalChecker

            goal_checker = GoalChecker(
                expected_outcome, resolve_deepeval_model(SIMULATOR_DEFAULT_MODEL)
            )

    session = TutoringSession(
        task=task,
        persona=persona,
        student_sim=student_sim,
        tc_checker=tc_checker,
        max_turns=task.max_turns,
        proxy=proxy,
        goal_checker=goal_checker,
    )
    register_session_tools(proxy, session)

    server = create_mcp_server(proxy)

    # Return server + cleanup info
    return server, container_manager, container, session, orch, staged_temp_dirs


def _load_task(task_id: str):
    """Load a task JSON by ID."""
    import json as _json
    from pathlib import Path

    tasks_dir = Path(__file__).parent.parent / "tasks"
    for json_path in tasks_dir.rglob(f"{task_id}.json"):
        from orchestrator.schemas import QuantTutorTask

        return QuantTutorTask(**_json.loads(json_path.read_text()))
    raise FileNotFoundError(f"Task not found: {task_id}")


def _load_persona(persona_id: str):
    """Load a persona JSON by ID."""
    import json as _json
    from pathlib import Path

    personas_dir = Path(__file__).parent.parent / "personas"
    for json_path in personas_dir.rglob(f"{persona_id}.json"):
        from orchestrator.schemas import StudentPersona

        return StudentPersona(**_json.loads(json_path.read_text()))
    raise FileNotFoundError(f"Persona not found: {persona_id}")


def main():
    """Standalone MCP server entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="QuantTutorBench MCP Server")
    parser.add_argument("--task", required=True, help="Task ID (e.g. S01_ma_crossover)")
    parser.add_argument(
        "--persona", required=True, help="Persona ID (e.g. developer_crossover)"
    )
    parser.add_argument("--no-docker", action="store_true", help="Run without Docker")
    args = parser.parse_args()

    server, container_manager, container, session, orch, staged_temp_dirs = (
        _build_standalone_server(args.task, args.persona, use_docker=not args.no_docker)
    )

    try:
        asyncio.run(run_server_stdio(server))
    finally:
        # Cleanup
        try:
            container_manager.destroy_container(container.container_id)
        except Exception:
            pass
        orch._cleanup_staged_dirs(staged_temp_dirs)


if __name__ == "__main__":
    main()
