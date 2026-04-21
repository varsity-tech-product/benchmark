#!/usr/bin/env python3
"""CLI entry point for the student simulator stability experiment."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))


def _add_output_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Experiment results directory (default: config.OUTPUT_DIR)",
    )


def _make_parser() -> argparse.ArgumentParser:
    from experiments.student_sim_stability.config import (
        D1_SAMPLE_POLICY,
        JUDGE_MAX_WORKERS,
        JUDGE_MODEL,
        JUDGE_TEMPERATURE,
    )

    parser = argparse.ArgumentParser(
        description="Student simulator stability experiment"
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("dry-run", help="Print experiment scale")

    run_all = sub.add_parser(
        "all",
        help="Run full pipeline: generate → render → judge → aggregate → validate → report",
    )
    _add_output_arg(run_all)
    run_all.add_argument("-w", "--workers", type=int, default=None)
    run_all.add_argument("--judge-workers", type=int, default=JUDGE_MAX_WORKERS)

    generate = sub.add_parser("generate", help="Generate conversations")
    _add_output_arg(generate)
    generate.add_argument("-w", "--workers", type=int, default=None)
    generate.add_argument("-n", "--limit", type=int, default=None)

    render = sub.add_parser("render-judges", help="Render judge prompt files")
    _add_output_arg(render)
    render.add_argument(
        "--dimension",
        default="all",
        choices=["D1", "D2", "D3", "D4", "control", "all"],
    )
    render.add_argument(
        "--d1-sample-policy",
        default=D1_SAMPLE_POLICY,
        choices=["all", "live-r0-tt0"],
        help="D1 prompt sampling policy for rendered judge inputs",
    )
    render.add_argument(
        "--clean",
        action="store_true",
        help="Remove existing rendered prompts for selected dimension(s) first",
    )

    judge = sub.add_parser("judge", help="Run judge inputs through OpenRouter")
    _add_output_arg(judge)
    judge.add_argument(
        "--dimension",
        default="all",
        choices=["D1", "D2", "D3", "D4", "control", "all"],
    )
    judge.add_argument("--manifest", type=Path, default=None)
    judge.add_argument("-n", "--limit", type=int, default=None)
    judge.add_argument("-w", "--workers", type=int, default=JUDGE_MAX_WORKERS)
    judge.add_argument("--model", default=JUDGE_MODEL)
    judge.add_argument("--temperature", type=float, default=JUDGE_TEMPERATURE)
    judge.add_argument("--overwrite", action="store_true")
    judge.add_argument("--max-retries", type=int, default=3)
    judge.add_argument("--retry-delay", type=float, default=2.0)

    aggregate = sub.add_parser("aggregate", help="Aggregate judge outputs")
    _add_output_arg(aggregate)
    aggregate.add_argument("--strict", action="store_true")

    validate = sub.add_parser("validate", help="Validate generated artifacts")
    _add_output_arg(validate)
    validate.add_argument("--strict", action="store_true")
    validate.add_argument("--json", action="store_true")

    report = sub.add_parser("report", help="Generate HTML report")
    _add_output_arg(report)

    return parser


def _print_dry_run() -> None:
    from experiments.student_sim_stability.config import (
        STUDENT_MODELS,
        TUTOR_MODEL,
        TUTOR_TEMPERATURES,
        compute_trial_count,
    )

    counts = compute_trial_count()
    print("=== Experiment Scale ===")
    print(
        f"  Student models:     {', '.join(m.split('/')[-1] for m in STUDENT_MODELS)}"
    )
    print(f"  Tutor model:        {TUTOR_MODEL}")
    print(f"  Tutor temperatures: {TUTOR_TEMPERATURES}")
    print(f"  Live trials:        {counts['live']}")
    print(f"  Control trials:     {counts['control']}")
    print(f"  Total trials:       {counts['total']}")
    print(f"  Student messages:   {counts['student_messages']}")
    print(f"  Tutor messages:     {counts['tutor_messages']}")
    print(
        f"  Total API calls:    {counts['student_messages'] + counts['tutor_messages']}"
    )


def _runner(output_dir: str | None):
    from experiments.student_sim_stability.runner import ExperimentRunner

    return ExperimentRunner(output_dir=output_dir)


def _results_dir(output_dir: str | None) -> Path:
    from experiments.student_sim_stability.config import OUTPUT_DIR

    out = Path(output_dir or OUTPUT_DIR)
    if not out.is_absolute():
        out = BENCH_ROOT / out
    return out


def _render_judges(args: argparse.Namespace) -> None:
    from experiments.student_sim_stability.render_judge_prompts import (
        clean_rendered_prompts,
        render_control,
        render_d1,
        render_d2,
        render_d3,
        render_d4,
    )

    results_dir = _results_dir(args.output_dir)
    conv_dir = results_dir / "conversations"
    judge_input_dir = results_dir / "judge_inputs"

    if args.clean:
        removed = clean_rendered_prompts(judge_input_dir, args.dimension)
        print(f"clean: removed {removed} old prompts")

    if args.dimension in ("D1", "all"):
        render_d1(conv_dir, judge_input_dir, sample_policy=args.d1_sample_policy)
    if args.dimension in ("D2", "all"):
        render_d2(conv_dir, judge_input_dir)
    if args.dimension in ("D3", "all"):
        render_d3(conv_dir, judge_input_dir)
    if args.dimension in ("D4", "all"):
        render_d4(conv_dir, judge_input_dir)
    if args.dimension in ("control", "all"):
        render_control(conv_dir, judge_input_dir)


def _judge(args: argparse.Namespace) -> int:
    from experiments.student_sim_stability.judge_with_openrouter import run_judge

    results_dir = _results_dir(args.output_dir)
    stats = run_judge(
        input_dir=results_dir / "judge_inputs",
        output_dir=results_dir / "judge_outputs",
        dimension=args.dimension,
        manifest=args.manifest,
        limit=args.limit,
        workers=args.workers,
        model=args.model,
        temperature=args.temperature,
        overwrite=args.overwrite,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
    )
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 1 if stats["failed"] else 0


def _aggregate(args: argparse.Namespace) -> None:
    from experiments.student_sim_stability.aggregate_judge_outputs import aggregate

    results_dir = _results_dir(args.output_dir)
    summary = aggregate(
        judge_input_dir=results_dir / "judge_inputs",
        judge_output_dir=results_dir / "judge_outputs",
        output_path=results_dir / "evaluations" / "all_evaluations.json",
        strict=args.strict,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def _run_all(args: argparse.Namespace) -> int:
    """Run the full experiment pipeline end-to-end."""
    import os

    from experiments.student_sim_stability.aggregate_judge_outputs import aggregate
    from experiments.student_sim_stability.config import D1_SAMPLE_POLICY, MAX_WORKERS
    from experiments.student_sim_stability.judge_with_openrouter import run_judge
    from experiments.student_sim_stability.render_judge_prompts import (
        clean_rendered_prompts,
        render_control,
        render_d1,
        render_d2,
        render_d3,
        render_d4,
    )
    from experiments.student_sim_stability.runner import ExperimentRunner
    from experiments.student_sim_stability.validate_results import validate

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY environment variable is not set")
        return 1

    results_dir = _results_dir(args.output_dir)
    conv_dir = results_dir / "conversations"
    judge_input_dir = results_dir / "judge_inputs"
    judge_output_dir = results_dir / "judge_outputs"
    eval_path = results_dir / "evaluations" / "all_evaluations.json"

    # Step 1: Generate conversations
    print("\n=== Step 1/6: Generate conversations ===")
    runner = ExperimentRunner(output_dir=args.output_dir)
    runner.run_generate(max_workers=args.workers or MAX_WORKERS)

    # Step 2: Render judge prompts
    print("\n=== Step 2/6: Render judge prompts ===")
    clean_rendered_prompts(judge_input_dir, "all")
    render_d1(conv_dir, judge_input_dir, sample_policy=D1_SAMPLE_POLICY)
    render_d2(conv_dir, judge_input_dir)
    render_d3(conv_dir, judge_input_dir)
    render_d4(conv_dir, judge_input_dir)
    render_control(conv_dir, judge_input_dir)

    # Step 3: Run judge
    print("\n=== Step 3/6: Run judge ===")
    stats = run_judge(
        input_dir=judge_input_dir,
        output_dir=judge_output_dir,
        dimension="all",
        workers=args.judge_workers,
    )
    print(json.dumps(stats, indent=2))
    if stats["failed"]:
        print(f"ERROR: {stats['failed']} judge calls failed")
        return 1

    # Step 4: Aggregate
    print("\n=== Step 4/6: Aggregate ===")
    summary = aggregate(
        judge_input_dir=judge_input_dir,
        judge_output_dir=judge_output_dir,
        output_path=eval_path,
        strict=True,
    )
    print(json.dumps(summary, indent=2))

    # Step 5: Validate
    print("\n=== Step 5/6: Validate ===")
    checks, _ = validate(results_dir)
    failures = [check for check in checks if not check.ok and check.required]
    for check in checks:
        status = "OK" if check.ok else ("FAIL" if check.required else "WARN")
        print(f"[{status}] {check.name}: {check.message}")
    if failures:
        print(f"ERROR: {len(failures)} required validation checks failed")
        return 1

    # Step 6: Report
    print("\n=== Step 6/6: Generate report ===")
    try:
        from experiments.student_sim_stability.report import ReportGenerator
    except ModuleNotFoundError as exc:
        if exc.name == "matplotlib":
            print("ERROR: report generation requires matplotlib")
            return 1
        raise

    gen = ReportGenerator(str(eval_path), str(results_dir / "report"))
    report_path = gen.generate()
    print(f"Report: {report_path}")
    return 0


def _validate(args: argparse.Namespace) -> int:
    from experiments.student_sim_stability.validate_results import validate

    checks, summary = validate(_results_dir(args.output_dir))
    failures = [c for c in checks if not c.ok and (c.required or args.strict)]

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not failures,
                    "checks": [c.__dict__ for c in checks],
                    "summary": summary,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        for check in checks:
            status = "OK" if check.ok else ("FAIL" if check.required else "WARN")
            print(f"[{status}] {check.name}: {check.message}")
    return 1 if failures else 0


def main() -> int:
    parser = _make_parser()
    args = parser.parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "dry-run":
        _print_dry_run()
        return 0

    if args.command == "all":
        return _run_all(args)

    if args.command == "generate":
        from experiments.student_sim_stability.config import MAX_WORKERS

        runner = _runner(args.output_dir)
        runner.run_generate(max_workers=args.workers or MAX_WORKERS, limit=args.limit)
        return 0

    if args.command == "render-judges":
        _render_judges(args)
        return 0

    if args.command == "judge":
        return _judge(args)

    if args.command == "aggregate":
        _aggregate(args)
        return 0

    if args.command == "validate":
        return _validate(args)

    if args.command == "report":
        try:
            from experiments.student_sim_stability.report import ReportGenerator
        except ModuleNotFoundError as exc:
            if exc.name == "matplotlib":
                print(
                    "ERROR: report generation requires matplotlib. Install it in this Python environment."
                )
                return 1
            raise

        results_dir = _results_dir(args.output_dir)
        eval_path = results_dir / "evaluations" / "all_evaluations.json"
        if not eval_path.exists():
            print(f"ERROR: No evaluation results at {eval_path}")
            return 1
        gen = ReportGenerator(str(eval_path), str(results_dir / "report"))
        report_path = gen.generate()
        print(f"Report: {report_path}")
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
