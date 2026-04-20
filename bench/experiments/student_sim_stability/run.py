#!/usr/bin/env python3
"""CLI entry point for student simulator stability experiment.

Usage:
    python -m experiments.student_sim_stability.run dry-run         # Print scale
    python -m experiments.student_sim_stability.run generate        # Run all trials
    python -m experiments.student_sim_stability.run generate -w 3   # With 3 workers
    python -m experiments.student_sim_stability.run report          # Generate report
"""

import argparse
import logging
import sys
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))


def main():
    parser = argparse.ArgumentParser(
        description="Student simulator stability experiment"
    )
    parser.add_argument(
        "command",
        choices=["generate", "report", "dry-run"],
        help="Which stage to run",
    )
    parser.add_argument(
        "-w",
        "--workers",
        type=int,
        default=None,
        help="Number of parallel workers (default: config.MAX_WORKERS)",
    )
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.command == "dry-run":
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
        return

    if args.command == "generate":
        from experiments.student_sim_stability.config import MAX_WORKERS
        from experiments.student_sim_stability.runner import ExperimentRunner

        runner = ExperimentRunner(output_dir=args.output_dir)
        workers = args.workers or MAX_WORKERS
        runner.run_generate(max_workers=workers)

    elif args.command == "report":
        from experiments.student_sim_stability.report import ReportGenerator
        from experiments.student_sim_stability.runner import ExperimentRunner

        runner = ExperimentRunner(output_dir=args.output_dir)
        eval_path = runner.eval_dir / "all_evaluations.json"
        if not eval_path.exists():
            print(f"ERROR: No evaluation results at {eval_path}")
            sys.exit(1)
        gen = ReportGenerator(str(eval_path), str(runner.output_dir / "report"))
        report_path = gen.generate()
        print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
