#!/usr/bin/env python3
"""Reference management CLI for QuantTutorBench.

Promotes high-quality benchmark runs to scoring references, and provides
list/show commands for inspecting existing references.

Workflow:
    1. Run benchmark with ``--condition reference`` (oracle prompt):
       python -m run_benchmark run-single --task D01_load_csv \
           --agent openai --condition reference --save-result --docker

    2. Review scores.md / trace.md in the results directory.

    3. Promote the best run to a reference:
       python -m reference.generate_reference promote \
           --from bench/results/run-single/openai/.../D01_load_csv/beginner_no_finance

    4. Future evaluations will use this reference for comparison.

Usage:
    # Promote a run_state.json to reference
    python -m reference.generate_reference promote \
        --from results/run-single/openai/gpt-5.2/data_handling/D01_load_csv/beginner_no_finance

    # List existing references
    python -m reference.generate_reference list

    # Show details of a reference
    python -m reference.generate_reference show --task D01_load_csv
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
BENCH_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from reference.reference_store import ReferenceStore

# ===================================================================
# Promote command
# ===================================================================


def cmd_promote(args):
    """Promote a benchmark run to a scoring reference.

    Reads ``run_state.json`` from the given result directory and writes
    a reference JSON to ``reference/refs/{task_id}_{persona_id}.json``.
    """
    result_dir = Path(args.source).resolve()

    # Accept either the directory or the run_state.json file itself
    if result_dir.name == "run_state.json":
        state_path = result_dir
        result_dir = result_dir.parent
    else:
        state_path = result_dir / "run_state.json"

    if not state_path.exists():
        print(f"ERROR: run_state.json not found at {state_path}")
        print(
            "Hint: Run with --save-result (or --runonly) to generate "
            "run_state.json first."
        )
        sys.exit(1)

    state = json.loads(state_path.read_text(encoding="utf-8"))

    task_id = state["task_id"]
    persona_id = state.get("persona_id", "default")

    # Determine oracle model from agent_cost
    oracle_model = state.get("agent_cost", {}).get("model", "unknown")

    # Check for required fields
    missing = []
    for field in ("key_results", "trace_summary", "step_count"):
        if field not in state:
            missing.append(field)

    if missing:
        print(
            f"ERROR: run_state.json is missing reference fields: {missing}\n"
            f"This run_state.json was generated before the format extension.\n"
            f"Re-run with --runonly to regenerate it, or use --evalonly on "
            f"the existing results."
        )
        sys.exit(1)

    store = ReferenceStore()

    # Check for existing reference
    if not args.force and store.has_reference(task_id, persona_id):
        print(
            f"Reference already exists for {task_id}/{persona_id}. "
            f"Use --force to overwrite."
        )
        sys.exit(1)

    # Build reference JSON (same schema as before)
    ref = {
        "task_id": task_id,
        "persona_id": persona_id,
        "oracle_model": oracle_model,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "promoted_from": str(result_dir),
        "key_results": state["key_results"],
        "trace_summary": state["trace_summary"],
        "step_count": state["step_count"],
        "workspace_files": state.get("workspace_files", []),
        "full_trace": state.get("tool_logs", []),
        "conversation": state.get("conversation", []),
    }

    path = store.save(ref)

    print(f"Reference promoted: {task_id} / {persona_id}")
    print(f"  Oracle model: {oracle_model}")
    print(f"  Step count: {ref['step_count']}")
    print(f"  Key results: {len(ref['key_results'])} metrics")
    print(f"  Workspace files: {ref['workspace_files']}")
    print(f"  Conversation turns: {len(ref['conversation'])}")
    print(f"  Saved to: {path}")


# ===================================================================
# List / Show commands (preserved from original)
# ===================================================================


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
    if ref.get("promoted_from"):
        print(f"Promoted from: {ref['promoted_from']}")
    print()

    print("--- Trace Summary ---")
    for step in ref.get("trace_summary", []):
        print(
            f"  [{step['step']}] {step['tool']}: {step['action']}"
            f" -> {step.get('result_summary', '')[:80]}"
        )
    print()

    print("--- Key Results ---")
    for k, v in ref.get("key_results", {}).items():
        print(f"  {k}: {v}")
    print()

    print(f"Workspace files: {ref.get('workspace_files', [])}")
    print(f"Conversation turns: {len(ref.get('conversation', []))}")
    print(f"Full trace entries: {len(ref.get('full_trace', []))}")


# ===================================================================
# CLI entry point
# ===================================================================


def main():
    parser = argparse.ArgumentParser(
        description="Reference management for QuantTutorBench"
    )
    subparsers = parser.add_subparsers(dest="command")

    # promote command
    promote_parser = subparsers.add_parser(
        "promote",
        help="Promote a benchmark run to a scoring reference",
    )
    promote_parser.add_argument(
        "--from",
        dest="source",
        required=True,
        help="Path to result directory (containing run_state.json)",
    )
    promote_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing reference",
    )

    # list command
    subparsers.add_parser("list", help="List available references")

    # show command
    show_parser = subparsers.add_parser("show", help="Show reference details")
    show_parser.add_argument("--task", required=True, help="Task ID")
    show_parser.add_argument("--persona", default=None, help="Persona ID")

    args = parser.parse_args()

    if args.command == "promote":
        cmd_promote(args)
    elif args.command == "list":
        cmd_list(args)
    elif args.command == "show":
        cmd_show(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
