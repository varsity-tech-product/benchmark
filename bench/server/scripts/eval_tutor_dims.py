#!/usr/bin/env python3
"""Evaluate specific tutor dimensions on existing run_state.json files.

Calls the SAME evaluate_tutor_dimensions() used by the server pipeline.
No custom evaluation logic -- only I/O glue.

Usage:
    # Single result (D3+D4 only)
    python -m server.scripts.eval_tutor_dims \
        --result-dir results/run-single/anthropic/claude-sonnet-4-6/backtest/B01_.../developer_crossover_run1 \
        --dims D3_pedagogical_method D4_instructional_accuracy \
        --eval-model anthropic/claude-sonnet-4-6

    # Batch: scan all ICC data
    python -m server.scripts.eval_tutor_dims \
        --scan-dir results/run-single/anthropic \
        --dims D3_pedagogical_method D4_instructional_accuracy \
        --eval-model anthropic/claude-sonnet-4-6 \
        --output results/rubric_comparison/post_d3d4.json
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]  # bench/server/scripts/ -> bench/
sys.path.insert(0, str(BENCH_ROOT))

# Load .env from project root (benchmark/) for OPENROUTER_API_KEY etc.
_env_path = BENCH_ROOT.parent / ".env"
if _env_path.exists():
    for line in _env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip())


def load_task(task_id: str):
    """Load task JSON (mirrors session_api._load_task)."""
    from server.schemas import QuantTutorTask

    tasks_dir = BENCH_ROOT / "tasks"
    for json_path in tasks_dir.rglob(f"{task_id}.json"):
        return QuantTutorTask(**json.loads(json_path.read_text()))
    raise FileNotFoundError(f"Task not found: {task_id}")


def load_persona(persona_id: str):
    """Load persona JSON (mirrors session_api._load_persona)."""
    from server.schemas import StudentPersona

    personas_dir = BENCH_ROOT / "personas"
    for json_path in personas_dir.rglob(f"{persona_id}.json"):
        return StudentPersona(**json.loads(json_path.read_text()))
    raise FileNotFoundError(f"Persona not found: {persona_id}")


def eval_dims_on_result(
    result_dir: Path,
    dims: list[str],
    eval_model: str,
) -> dict | None:
    """Evaluate specified tutor dimensions on a single result_dir."""
    run_state_path = result_dir / "run_state.json"
    if not run_state_path.exists():
        print(f"  SKIP: {run_state_path} not found")
        return None

    run_state = json.loads(run_state_path.read_text())
    task_id = run_state["task_id"]
    persona_id = run_state["persona_id"]
    conversation = run_state["conversation"]
    tool_logs = run_state.get("tool_logs", [])

    task = load_task(task_id)
    persona = load_persona(persona_id)

    # Convert tool_log dicts to objects with attributes (enrichment expects .turn_index, .name, etc.)
    from types import SimpleNamespace

    tool_log_objs = [
        SimpleNamespace(**log) if isinstance(log, dict) else log for log in tool_logs
    ]

    # Build enriched conversation (identical to pipeline.py line 245)
    from server.eval.enrichment import enrich_conversation_with_tools

    enriched_conv = enrich_conversation_with_tools(conversation, tool_log_objs)

    # Build scenario and user_description (identical to pipeline.py lines 239-253)
    from server.config.prompt_config import build_scenario, build_user_description

    scenario = build_scenario(task, persona.persona_id)
    expected_outcome = task.ground_truth.expected_outcome if task.ground_truth else None
    user_description = build_user_description(persona)

    print(
        f"  {task_id}/{persona_id} dims={[d[:2] for d in dims]} model={eval_model.split('/')[-1]}"
    )

    # Call the SAME function as server pipeline (pipeline.py line 247)
    from server.eval.ewan_eval.tutor_conv_geval import evaluate_tutor_dimensions

    t0 = time.time()
    tutor_scores = evaluate_tutor_dimensions(
        conversation_turns=conversation,
        enriched_conversation_turns=enriched_conv,
        persona_id=persona.persona_id,
        scenario=scenario,
        expected_outcome=expected_outcome,
        user_description=user_description,
        model=eval_model,
        category=task.category.value,
        requires_code=task.requires_code,
        abort_event=None,
    )
    elapsed = time.time() - t0

    # Extract only requested dimensions
    dim_scores = {}
    for d in dims:
        if d in tutor_scores:
            dim_scores[d] = tutor_scores[d]

    print(
        f"    -> {' | '.join(f'{d[:2]}={v:.4f}' for d, v in dim_scores.items())} ({elapsed:.1f}s)"
    )

    return {
        "task_id": task_id,
        "persona_id": persona_id,
        "result_dir": str(result_dir),
        "dim_scores": dim_scores,
        "eval_model": eval_model,
    }


def find_all_run_dirs(scan_dir: Path) -> list[Path]:
    """Find all directories containing run_state.json (exclude _original)."""
    dirs = []
    for rs in sorted(scan_dir.rglob("run_state.json")):
        if "_original" in str(rs):
            continue
        dirs.append(rs.parent)
    return dirs


def main():
    parser = argparse.ArgumentParser(
        description="Evaluate specific tutor dimensions on existing results"
    )
    parser.add_argument("--result-dir", type=str, help="Single result directory")
    parser.add_argument("--scan-dir", type=str, help="Scan for all run_state.json")
    parser.add_argument(
        "--dims",
        nargs="+",
        default=["D3_pedagogical_method", "D4_instructional_accuracy"],
    )
    parser.add_argument("--eval-model", default="anthropic/claude-sonnet-4-6")
    parser.add_argument("--output", type=str, help="Save results JSON")
    args = parser.parse_args()

    dirs = []
    if args.result_dir:
        dirs.append(Path(args.result_dir).resolve())
    if args.scan_dir:
        dirs.extend(find_all_run_dirs(Path(args.scan_dir).resolve()))
    if not dirs:
        parser.error("Must specify --result-dir or --scan-dir")

    print(f"Found {len(dirs)} result directories")
    print(f"Dimensions: {args.dims}")
    print(f"Eval model: {args.eval_model}")
    print()

    all_results = []
    for i, d in enumerate(dirs):
        print(f"[{i + 1}/{len(dirs)}] {d.relative_to(BENCH_ROOT)}")
        try:
            result = eval_dims_on_result(d, args.dims, args.eval_model)
            if result:
                all_results.append(result)
        except Exception as e:
            print(f"  ERROR: {e}")
            all_results.append({"result_dir": str(d), "error": str(e)})

    if args.output:
        out = Path(args.output)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(all_results, indent=2, default=str))
        print(f"\nSaved {len(all_results)} results to {args.output}")

    # Summary
    ok = [r for r in all_results if "error" not in r]
    errs = [r for r in all_results if "error" in r]
    print(f"\nSummary: {len(ok)} succeeded, {len(errs)} errors")
    if ok:
        for dim in args.dims:
            vals = [r["dim_scores"][dim] for r in ok if dim in r.get("dim_scores", {})]
            if vals:
                import statistics

                print(
                    f"  {dim}: mean={statistics.mean(vals):.4f} "
                    f"std={statistics.stdev(vals) if len(vals) > 1 else 0:.4f} n={len(vals)}"
                )


if __name__ == "__main__":
    main()
