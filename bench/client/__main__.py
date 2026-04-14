"""QuantTutorBench Baseline Client entry point.

Usage::

    # Single task
    python -m client --server http://localhost:8000/mcp --task X01_ma_offbyone

    # Multiple tasks
    python -m client --server http://localhost:8000/mcp --tasks S01 D01 X01 --workers 3

    # By category
    python -m client --server http://localhost:8000/mcp --group strategy --workers 3

"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

# Ensure bench/ is importable
_BENCH_ROOT = Path(__file__).parent.parent
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))


def _resolve_task_ids(args) -> list[str]:
    """Resolve task IDs from --task, --tasks, or --group."""
    if args.task:
        return [args.task]
    if args.tasks:
        return args.tasks
    if args.group:
        return _list_tasks_by_group(args.group)
    return []


def _list_tasks_by_group(group: str) -> list[str]:
    """List task IDs for a category group from tasks/layer2/{group}/."""
    tasks_dir = _BENCH_ROOT / "tasks" / "layer2" / group
    if not tasks_dir.is_dir():
        logging.error("Task group directory not found: %s", tasks_dir)
        return []
    ids = sorted(p.stem for p in tasks_dir.glob("*.json"))
    logging.info("Group '%s': %d tasks — %s", group, len(ids), ids)
    return ids


def _make_adapter_factory(args):
    """Create a factory function that produces fresh adapter instances."""

    def factory():
        from client.adapters.anthropic_adapter import ClaudeAgentAdapter

        return ClaudeAgentAdapter(
            system_prompt=args.system_prompt if hasattr(args, "system_prompt") else "",
            agent_name="baseline_client",
        )

    return factory


def main():
    parser = argparse.ArgumentParser(
        description="QuantTutorBench Baseline Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--server",
        required=True,
        help="MCP server URL (e.g. http://localhost:8000/mcp)",
    )

    # Task selection (mutually exclusive)
    task_group = parser.add_mutually_exclusive_group()
    task_group.add_argument("--task", help="Single task ID")
    task_group.add_argument("--tasks", nargs="+", help="Multiple task IDs")
    task_group.add_argument(
        "--group",
        help="Task category group (e.g. strategy, debug)",
    )

    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Max concurrent tasks (default: 1)",
    )
    parser.add_argument(
        "--result-dir",
        default=str(_BENCH_ROOT / "results" / "client"),
        help="Client-side result directory",
    )
    parser.add_argument(
        "--system-prompt",
        default="",
        help="Override system prompt (default: CLEAN_SYSTEM_PROMPT)",
    )
    parser.add_argument(
        "--agent-max-steps",
        type=int,
        default=200,
        help="Agent loop iteration cap (default: 200, set 0 for unlimited)",
    )
    parser.add_argument(
        "--persona",
        help="Optional explicit persona_id for deterministic reruns",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    task_ids = _resolve_task_ids(args)
    if not task_ids:
        parser.error("No tasks specified. Use --task, --tasks, or --group.")

    adapter_factory = _make_adapter_factory(args)
    result_dir = Path(args.result_dir)

    logger = logging.getLogger(__name__)
    logger.info(
        "Running %d task(s) with %d worker(s) against %s",
        len(task_ids),
        args.workers,
        args.server,
    )

    from client.runner import run_multiple_tasks, run_single_task

    if len(task_ids) == 1:
        result = asyncio.run(
            run_single_task(
                args.server,
                task_ids[0],
                adapter_factory,
                result_dir,
                args.agent_max_steps,
                args.persona,
            ),
        )
        results = [result]
    else:
        results = asyncio.run(
            run_multiple_tasks(
                args.server,
                task_ids,
                adapter_factory,
                args.workers,
                result_dir,
                args.agent_max_steps,
                args.persona,
            ),
        )

    # Print summary
    print("\n" + "=" * 60)
    print("Results Summary")
    print("=" * 60)
    for r in results:
        tid = r.get("task_id", "?")
        sid = r.get("session_id", "?")
        dur = r.get("duration_seconds", 0)
        err = r.get("error", "")
        if err:
            print(f"\n  {tid}: FAILED — {err}")
        else:
            print(f"\n  {tid}")
            print(f"    session_id:  {sid}")
            print(f"    duration:    {dur}s")
            cost = r.get("agent_cost", {})
            if cost:
                print(
                    f"    cost:        ${cost.get('cost_usd', 0):.4f} ({cost.get('api_calls', 0)} API calls)"
                )
            print(f"    client trace: {result_dir}/{sid}/client_trace.md")
    print()


if __name__ == "__main__":
    main()
