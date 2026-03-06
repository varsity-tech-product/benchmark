#!/usr/bin/env python3
"""Generate reference executions for QuantTutorBench tasks.

A strong "oracle" model runs each task once with the full tutor prompt.
Its trace, results, and conversation are recorded as the scoring anchor
for later evaluation phases (step efficiency, result comparison, process
alignment).

Usage:
    # Single task, specific persona
    python -m reference.generate_reference \\
        --task S01_ma_crossover --persona beginner_no_finance \\
        --agent openai --max-turns 15

    # Single task, all personas
    python -m reference.generate_reference \\
        --task S01_ma_crossover --agent openai --max-turns 15

    # All tasks
    python -m reference.generate_reference --all --agent openai --max-turns 15

    # List existing references
    python -m reference.generate_reference --list
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BENCH_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from dotenv import load_dotenv

load_dotenv(BENCH_ROOT.parent / ".env")

from config.llm_config import (
    OPENROUTER_BASE_URL,
    REFERENCE_DEFAULT_MODEL,
)
from config.model_resolver import get_model_for_agent

if os.environ.get("OPENROUTER_API_KEY") and not os.environ.get("OPENAI_API_KEY"):
    os.environ["OPENAI_API_KEY"] = os.environ["OPENROUTER_API_KEY"]
    os.environ["OPENAI_BASE_URL"] = OPENROUTER_BASE_URL

from reference.reference_store import ReferenceStore

# ===================================================================
# Agent creation (mirrors run_benchmark._create_agent logic)
# ===================================================================


def _create_agent(agent_type: str, model_override: Optional[str] = None):
    """Create an agent adapter for reference generation.

    Always uses the tutor prompt and tools-enabled condition.

    Model resolution order:
    1. ``--model`` CLI override (explicit)
    2. ``REFERENCE_DEFAULT_MODEL`` from llm_config (for generic/openai
       agents that route through OpenRouter)
    3. Per-SDK native model (for anthropic/google agents whose native
       APIs can't accept OpenRouter-format model names)
    """
    from config.prompt_config import TUTOR_SYSTEM_PROMPT

    system_prompt = TUTOR_SYSTEM_PROMPT

    if agent_type == "anthropic":
        from orchestrator.agent_adapters.anthropic_adapter import ClaudeAgentAdapter

        model = model_override or get_model_for_agent("anthropic")
        agent_name = f"reference_{agent_type}_{model.split('/')[-1]}"
        return ClaudeAgentAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )
    elif agent_type == "google":
        from orchestrator.agent_adapters.google_adapter import GoogleAdapter

        model = model_override or get_model_for_agent("google")
        agent_name = f"reference_{agent_type}_{model.split('/')[-1]}"
        return GoogleAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )
    elif agent_type == "openai":
        from orchestrator.agent_adapters.openai_adapter import OpenAIAgentAdapter

        model = model_override or REFERENCE_DEFAULT_MODEL
        agent_name = f"reference_{agent_type}_{model.split('/')[-1]}"
        return OpenAIAgentAdapter(
            model=model,
            base_url=OPENROUTER_BASE_URL,
            system_prompt=system_prompt,
            agent_name=agent_name,
        )
    else:  # generic — always routes through OpenRouter
        from orchestrator.agent_adapters.generic_adapter import GenericLLMAdapter

        model = model_override or REFERENCE_DEFAULT_MODEL
        agent_name = f"reference_{agent_type}_{model.split('/')[-1]}"
        return GenericLLMAdapter(
            model=model, system_prompt=system_prompt, agent_name=agent_name
        )


# ===================================================================
# Reference data extraction
# ===================================================================


def _describe_action(tool_name: str, args: dict) -> str:
    """Generate a human-readable action description from a tool call."""
    if tool_name == "fetch_market_data":
        symbol = args.get("symbol", "?")
        period = args.get("period", "")
        return f"fetch {symbol} market data" + (f" ({period})" if period else "")
    if tool_name == "compute_indicator":
        indicator = args.get("indicator", "?")
        params = args.get("params", {})
        return f"compute {indicator}" + (f" {params}" if params else "")
    if tool_name == "run_backtest":
        strategy = args.get("strategy", "?")
        return f"run {strategy} backtest"
    if tool_name == "analyze_backtest_results":
        return "analyze backtest results"
    if tool_name == "compute_statistics":
        test = args.get("test", "?")
        return f"compute {test} statistic"
    if tool_name == "evaluate_signal":
        return "evaluate signal quality metrics"
    if tool_name == "plot_chart":
        return "create chart visualization"
    if tool_name == "shell_exec":
        cmd = args.get("command", "")
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        return f"execute: {cmd}"
    if tool_name == "file_write":
        path = args.get("path", "?")
        return f"write file {path}"
    if tool_name == "file_read":
        path = args.get("path", "?")
        return f"read file {path}"
    if tool_name == "file_list":
        return "list directory"
    if tool_name == "search_docs":
        query = args.get("query", "?")
        return f'search docs for "{query}"'
    if tool_name == "get_environment_info":
        return "inspect environment"
    return f"call {tool_name}"


def _summarize_result(result: str, max_len: int = 150) -> str:
    """Truncate a tool result to a concise summary."""
    if not result:
        return ""
    # Try to parse as JSON and summarize structured data
    try:
        data = json.loads(result)
        if isinstance(data, dict):
            # Return key names + any scalar values
            summary_parts = []
            for k, v in list(data.items())[:6]:
                if isinstance(v, (int, float)):
                    summary_parts.append(f"{k}={v}")
                elif isinstance(v, str) and len(v) < 30:
                    summary_parts.append(f"{k}={v}")
                elif isinstance(v, list):
                    summary_parts.append(f"{k}=[{len(v)} items]")
                else:
                    summary_parts.append(k)
            return ", ".join(summary_parts)
    except (json.JSONDecodeError, TypeError):
        pass
    # Plain text truncation
    if len(result) > max_len:
        return result[: max_len - 3] + "..."
    return result


def _build_trace_summary(full_logs: list[dict]) -> list[dict]:
    """Build a human-readable trace summary from full tool logs."""
    summary = []
    for i, log in enumerate(full_logs, 1):
        summary.append(
            {
                "step": i,
                "action": _describe_action(log["name"], log.get("args", {})),
                "tool": log["name"],
                "result_summary": _summarize_result(log.get("result", "")),
            }
        )
    return summary


def _extract_key_results(workspace_path: str, full_logs: list[dict]) -> dict:
    """Extract key numerical results from workspace files and tool outputs.

    Scans two sources:
    1. JSON files in the workspace (e.g. backtest_metrics.json)
    2. Tool output strings from metric-producing tools

    Uses recursive extraction to capture nested structures (e.g.
    evaluate_signal's ``signal_metrics.ic_mean``).  Source 1 (workspace
    files = final saved state) takes precedence over Source 2 (tool
    output, possibly intermediate).
    """
    results: dict = {}

    def _collect_numeric_scalars(obj, prefix: str = ""):
        """Recursively walk *obj* and store every numeric leaf."""
        if isinstance(obj, dict):
            for key, value in obj.items():
                next_prefix = f"{prefix}.{key}" if prefix else str(key)
                _collect_numeric_scalars(value, next_prefix)
        elif isinstance(obj, list):
            for idx, value in enumerate(obj):
                _collect_numeric_scalars(value, f"{prefix}[{idx}]")
        elif isinstance(obj, (int, float)):
            results[prefix] = round(obj, 6) if isinstance(obj, float) else obj

    # --- Source 1: workspace JSON files (highest priority) ---
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(workspace_path, fname)
            try:
                with open(fpath) as f:
                    data = json.load(f)
                _collect_numeric_scalars(data)
            except Exception:
                pass

    # Snapshot Source 1 so Source 2 cannot overwrite.
    source1_snapshot = dict(results)

    # --- Source 2: tool output JSON ---
    _metric_tools = {
        "run_backtest",
        "analyze_backtest_results",
        "compute_statistics",
        "evaluate_signal",
    }
    for log in full_logs:
        if log["name"] not in _metric_tools:
            continue
        try:
            data = json.loads(log.get("result", ""))
            if isinstance(data, dict):
                metrics = data.get("metrics", data)
                if isinstance(metrics, dict):
                    _collect_numeric_scalars(metrics)
        except (json.JSONDecodeError, TypeError):
            pass

    # Restore Source 1 values (workspace files = final state, highest priority)
    results.update(source1_snapshot)

    return results


def _list_workspace_files(workspace_path: str) -> list[str]:
    """List files in the workspace directory."""
    if not workspace_path or not os.path.isdir(workspace_path):
        return []
    files = []
    for entry in sorted(os.listdir(workspace_path)):
        fpath = os.path.join(workspace_path, entry)
        if os.path.isfile(fpath):
            files.append(entry)
    return files


# ===================================================================
# Core reference generation
# ===================================================================


def generate_reference_for_task(
    task,
    persona,
    agent,
    orchestrator,
    max_turns: int = 15,
) -> dict:
    """Run a task with the oracle model and build the reference JSON.

    Uses ``run_single_task()`` with a ``pre_teardown_hook`` to capture
    the full (untruncated) proxy logs and workspace state before
    teardown destroys the container.

    Args:
        task: The QuantTutorTask to run.
        persona: The StudentPersona to use.
        agent: The agent adapter (oracle model).
        orchestrator: A BenchmarkOrchestrator instance.
        max_turns: Maximum conversation turns.

    Returns:
        A reference dict matching the Phase 0B schema.
    """
    captured: dict = {}

    def _capture_hook(*, result, proxy, workspace_path):
        """Pre-teardown hook: capture full logs and workspace state."""
        captured["full_logs"] = [
            {
                "name": log.name,
                "args": log.args,
                "result": log.result,
                "timestamp": log.timestamp,
                "duration_ms": round(log.duration_ms, 2),
                "success": log.success,
                "turn_index": log.turn_index,
            }
            for log in proxy.get_logs()
        ]
        captured["workspace_files"] = _list_workspace_files(workspace_path)
        captured["key_results"] = _extract_key_results(
            workspace_path, captured["full_logs"]
        )

    result = orchestrator.run_single_task(
        task=task,
        persona=persona,
        agent=agent,
        max_turns=max_turns,
        pre_teardown_hook=_capture_hook,
    )

    full_logs = captured.get("full_logs", [])
    tool_logs = full_logs

    reference = {
        "task_id": task.task_id,
        "persona_id": persona.persona_id,
        "oracle_model": getattr(agent, "model", "unknown"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "trace_summary": _build_trace_summary(tool_logs),
        "step_count": len(tool_logs),
        "key_results": captured.get("key_results", {}),
        "workspace_files": captured.get("workspace_files", []),
        "full_trace": full_logs,
        "conversation": [{"role": t.role, "content": t.content} for t in result.turns],
    }

    return reference


# ===================================================================
# CLI
# ===================================================================


def cmd_generate(args):
    """Generate reference(s) for specified task(s)."""
    from orchestrator.orchestrator import BenchmarkOrchestrator

    orchestrator = BenchmarkOrchestrator(
        bench_root=str(BENCH_ROOT),
        use_docker=getattr(args, "docker", False),
        eval_model=getattr(args, "eval_model", None),
        simulator_model=getattr(args, "simulator_model", None),
    )

    agent = _create_agent(
        agent_type=args.agent,
        model_override=getattr(args, "model", None),
    )

    store = ReferenceStore()
    max_turns = getattr(args, "max_turns", 15)

    # Discover tasks
    if args.all:
        task_ids = []
        for category_dir in (BENCH_ROOT / "tasks" / "layer2").iterdir():
            if category_dir.is_dir():
                for f in sorted(category_dir.glob("*.json")):
                    task_ids.append(f.stem)
    else:
        task_ids = [args.task]

    for task_id in task_ids:
        # Find the task file
        task_file = None
        for category_dir in (BENCH_ROOT / "tasks" / "layer2").iterdir():
            if category_dir.is_dir():
                for f in category_dir.glob("*.json"):
                    if f.stem == task_id:
                        task_file = f
                        break
                if task_file:
                    break

        if not task_file:
            print(f"Task not found: {task_id}")
            continue

        task = orchestrator.load_task(str(task_file))

        # Determine personas to generate for
        if args.persona:
            persona_ids = [args.persona]
        else:
            persona_ids = task.persona_ids

        for persona_id in persona_ids:
            # Skip if reference already exists (unless --force)
            if not args.force and store.has_reference(task_id, persona_id):
                print(
                    f"  SKIP {task_id}/{persona_id} — reference exists "
                    f"(use --force to overwrite)"
                )
                continue

            print(f"\n{'='*60}")
            print(f"Generating reference: {task_id} / {persona_id}")
            print(f"Agent: {agent.agent_name} (model={agent.model})")
            print(f"Max turns: {max_turns}")
            print(f"{'='*60}")

            try:
                persona = orchestrator.load_persona(persona_id)
            except FileNotFoundError:
                print(f"  Persona not found: {persona_id}")
                continue

            try:
                ref = generate_reference_for_task(
                    task=task,
                    persona=persona,
                    agent=agent,
                    orchestrator=orchestrator,
                    max_turns=max_turns,
                )
                path = store.save(ref)
                print(f"\n  Reference saved: {path}")
                print(f"  Step count: {ref['step_count']}")
                print(f"  Key results: {json.dumps(ref['key_results'], indent=2)}")
                print(f"  Workspace files: {ref['workspace_files']}")
                print(f"  Conversation turns: {len(ref['conversation'])}")
            except Exception as e:
                print(f"  ERROR generating reference for {task_id}/{persona_id}: {e}")
                import traceback

                traceback.print_exc()


def cmd_list(args):
    """List all available reference files."""
    store = ReferenceStore()
    refs = store.list_available()

    if not refs:
        print("No reference files found.")
        return

    print(f"{'Task ID':<30} {'Persona':<25} {'Model':<25} {'Steps':>5}")
    print("-" * 90)
    for r in refs:
        if r.get("error"):
            print(f"{r['task_id']:<30} {'ERROR':<25}")
        else:
            print(
                f"{r['task_id']:<30} {r['persona_id']:<25} "
                f"{r['oracle_model']:<25} {r['step_count']:>5}"
            )


def cmd_show(args):
    """Show details of a specific reference."""
    store = ReferenceStore()
    ref = store.load(args.task, args.persona)

    if ref is None:
        print(f"No reference found for {args.task}")
        return

    print(f"Task: {ref['task_id']}")
    print(f"Persona: {ref.get('persona_id', 'unknown')}")
    print(f"Oracle model: {ref.get('oracle_model', 'unknown')}")
    print(f"Generated: {ref.get('generated_at', 'unknown')}")
    print(f"Step count: {ref.get('step_count', 0)}")
    print()

    print("--- Trace Summary ---")
    for step in ref.get("trace_summary", []):
        print(
            f"  [{step['step']}] {step['tool']}: {step['action']}"
            f" → {step['result_summary'][:80]}"
        )
    print()

    print("--- Key Results ---")
    for k, v in ref.get("key_results", {}).items():
        print(f"  {k}: {v}")
    print()

    print(f"Workspace files: {ref.get('workspace_files', [])}")
    print(f"Conversation turns: {len(ref.get('conversation', []))}")
    print(f"Full trace entries: {len(ref.get('full_trace', []))}")


def main():
    parser = argparse.ArgumentParser(
        description="Generate reference executions for QuantTutorBench"
    )
    subparsers = parser.add_subparsers(dest="command")

    # generate command
    gen_parser = subparsers.add_parser("generate", help="Generate reference(s)")
    gen_parser.add_argument("--task", help="Task ID to generate reference for")
    gen_parser.add_argument("--all", action="store_true", help="Generate for all tasks")
    gen_parser.add_argument(
        "--persona", help="Specific persona (default: all personas)"
    )
    gen_parser.add_argument(
        "--agent",
        default="openai",
        choices=["generic", "openai", "anthropic", "google"],
        help="Agent SDK to use as oracle",
    )
    gen_parser.add_argument("--model", default=None, help="Override oracle model")
    gen_parser.add_argument("--docker", action="store_true", help="Use Docker sandbox")
    gen_parser.add_argument(
        "--max-turns", type=int, default=15, help="Max conversation turns"
    )
    gen_parser.add_argument(
        "--force", action="store_true", help="Overwrite existing references"
    )
    gen_parser.add_argument("--eval-model", default=None, help="Eval model override")
    gen_parser.add_argument(
        "--simulator-model", default=None, help="Simulator model override"
    )

    # list command
    subparsers.add_parser("list", help="List available references")

    # show command
    show_parser = subparsers.add_parser("show", help="Show reference details")
    show_parser.add_argument("--task", required=True, help="Task ID")
    show_parser.add_argument("--persona", default=None, help="Persona ID")

    args = parser.parse_args()

    if args.command == "generate":
        if not args.task and not args.all:
            gen_parser.error("Either --task or --all is required")
        cmd_generate(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "show":
        cmd_show(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
