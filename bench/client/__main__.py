"""QuantTutorBench Baseline Client entry point.

Two subcommands::

    # Attach to an existing run (created via website or API)
    python -m client attach --server http://localhost:8000 --run-token qtb_xYz123...

    # Create a run + attach in one step (open-source one-liner)
    python -m client run --server http://localhost:8000 --task D01

    # Batch execution
    python -m client run --server http://localhost:8000 --tasks D01 D02 X01 --workers 3

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


def _make_adapter_factory(args):
    """Create a factory function that produces fresh adapter instances."""

    def factory():
        from client.adapters.anthropic_adapter import ClaudeAgentAdapter

        return ClaudeAgentAdapter(
            system_prompt=getattr(args, "system_prompt", ""),
            agent_name="baseline_client",
        )

    return factory


def _cmd_attach(args):
    """Handle 'attach' subcommand."""
    from client.runner import run_via_attach

    adapter_factory = _make_adapter_factory(args)
    result_dir = Path(args.result_dir)

    result = asyncio.run(
        run_via_attach(
            server_base_url=args.server,
            token=args.run_token,
            adapter_factory=adapter_factory,
            result_dir=result_dir,
            agent_max_steps=args.agent_max_steps,
            protocol=args.protocol,
        )
    )
    _print_results([result], result_dir)


def _cmd_run(args):
    """Handle 'run' subcommand."""
    from client.runner import run_multiple_tasks, run_task

    tasks = _resolve_tasks(args)
    if not tasks:
        print(
            "Error: No tasks specified. Use --task, --tasks, or --group.",
            file=sys.stderr,
        )
        sys.exit(1)

    adapter_factory = _make_adapter_factory(args)
    result_dir = Path(args.result_dir)

    logger = logging.getLogger(__name__)
    logger.info(
        "Running %d task(s) with %d worker(s) against %s [%s]",
        len(tasks),
        args.workers,
        args.server,
        args.protocol,
    )

    if len(tasks) == 1:
        result = asyncio.run(
            run_task(
                server_base_url=args.server,
                task=tasks[0],
                adapter_factory=adapter_factory,
                result_dir=result_dir,
                agent_max_steps=args.agent_max_steps,
                protocol=args.protocol,
            )
        )
        results = [result]
    else:
        results = asyncio.run(
            run_multiple_tasks(
                server_base_url=args.server,
                tasks=tasks,
                adapter_factory=adapter_factory,
                workers=args.workers,
                result_dir=result_dir,
                agent_max_steps=args.agent_max_steps,
                protocol=args.protocol,
            )
        )

    _print_results(results, result_dir)


def _resolve_tasks(args) -> list[str]:
    """Resolve task labels from --task, --tasks, or --group."""
    if args.task:
        return [args.task]
    if args.tasks:
        return args.tasks
    if args.group:
        return _list_tasks_by_group(args.group)
    return []


def _list_tasks_by_group(group: str) -> list[str]:
    """List task labels for a category group."""
    import re

    tasks_dir = _BENCH_ROOT / "tasks" / "layer2" / group
    if not tasks_dir.is_dir():
        logging.error("Task group directory not found: %s", tasks_dir)
        return []
    labels = []
    for p in sorted(tasks_dir.glob("*.json")):
        m = re.match(r"^([A-Z]\d{2})_", p.stem)
        if m:
            labels.append(m.group(1))
    logging.info("Group '%s': %d tasks — %s", group, len(labels), labels)
    return labels


def _print_results(results: list[dict], result_dir: Path):
    """Print summary table."""
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


# ---------------------------------------------------------------------------
# Shared arguments
# ---------------------------------------------------------------------------


def _add_common_args(parser: argparse.ArgumentParser):
    """Add arguments shared between attach and run."""
    parser.add_argument(
        "--server",
        required=True,
        help="Server base URL (e.g. http://localhost:8000)",
    )
    parser.add_argument(
        "--protocol",
        default="mcp",
        choices=["mcp", "rest"],
        help="Session protocol (default: mcp)",
    )
    parser.add_argument(
        "--result-dir",
        default=str(_BENCH_ROOT / "results" / "client"),
        help="Client-side result directory",
    )
    parser.add_argument(
        "--system-prompt",
        default="",
        help="Override system prompt",
    )
    parser.add_argument(
        "--agent-max-steps",
        type=int,
        default=200,
        help="Agent loop iteration cap (default: 200)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )


def main():
    parser = argparse.ArgumentParser(
        description="QuantTutorBench Baseline Client",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    # ── attach ──
    p_attach = subparsers.add_parser(
        "attach",
        help="Attach to an existing run (from website or API)",
    )
    _add_common_args(p_attach)
    p_attach.add_argument(
        "--run-token",
        required=True,
        help="Run token (from website or /client/runs/start)",
    )

    # ── run ──
    p_run = subparsers.add_parser(
        "run",
        help="Create a run + execute (one-step)",
    )
    _add_common_args(p_run)
    task_group = p_run.add_mutually_exclusive_group(required=True)
    task_group.add_argument("--task", help="Single task label (e.g. D01)")
    task_group.add_argument("--tasks", nargs="+", help="Multiple task labels")
    task_group.add_argument(
        "--group", help="Task category group (e.g. strategy, debug)"
    )
    p_run.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Max concurrent tasks (default: 1)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.command == "attach":
        _cmd_attach(args)
    elif args.command == "run":
        _cmd_run(args)


if __name__ == "__main__":
    main()
