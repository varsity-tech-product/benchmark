#!/usr/bin/env python3
"""CLI entry point for student simulator stability experiment.

Usage:
    # Run everything (generate + evaluate + report)
    python -m experiments.student_sim_stability.run all

    # Run phases individually
    python -m experiments.student_sim_stability.run phase1        # Scripted tutor
    python -m experiments.student_sim_stability.run phase2        # Live tutor
    python -m experiments.student_sim_stability.run control       # Control group
    python -m experiments.student_sim_stability.run evaluate      # Run D1-D4 evaluation
    python -m experiments.student_sim_stability.run report        # Generate report only

    # Options
    python -m experiments.student_sim_stability.run all --output-dir results/my_run
    python -m experiments.student_sim_stability.run all --dry-run  # Print trial count only
"""

import argparse
import logging
import sys
from pathlib import Path

# Ensure bench root is on sys.path
BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))


def main():
    parser = argparse.ArgumentParser(
        description="Student simulator stability experiment"
    )
    parser.add_argument(
        "command",
        choices=["all", "phase1", "phase2", "control", "evaluate", "report", "dry-run"],
        help="Which stage(s) to run",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Override output directory",
    )
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
        from experiments.student_sim_stability.config import compute_trial_count

        counts = compute_trial_count()
        print("=== Experiment Scale ===")
        print(f"  Phase 1 (scripted): {counts['scripted']} trials")
        print(f"  Phase 2 (live):     {counts['live']} trials")
        print(f"  Control group:      {counts['control']} trials")
        print(f"  Total trials:       {counts['total']}")
        print(f"  Total messages:     {counts['total_messages']}")
        return

    from experiments.student_sim_stability.runner import ExperimentRunner

    runner = ExperimentRunner(output_dir=args.output_dir)

    if args.command == "all":
        runner.run_all()
        # Generate report after evaluation
        from experiments.student_sim_stability.report import ReportGenerator

        eval_path = runner.eval_dir / "all_evaluations.json"
        if eval_path.exists():
            gen = ReportGenerator(str(eval_path), str(runner.output_dir / "report"))
            report_path = gen.generate()
            print(f"\nReport: {report_path}")
    elif args.command == "phase1":
        runner.run_phase1()
    elif args.command == "phase2":
        runner.run_phase2()
    elif args.command == "control":
        runner.run_control()
    elif args.command == "evaluate":
        runner.run_evaluation()
    elif args.command == "report":
        from experiments.student_sim_stability.report import ReportGenerator

        eval_path = runner.eval_dir / "all_evaluations.json"
        if not eval_path.exists():
            print(f"ERROR: No evaluation results found at {eval_path}")
            print("Run 'evaluate' first.")
            sys.exit(1)
        gen = ReportGenerator(str(eval_path), str(runner.output_dir / "report"))
        report_path = gen.generate()
        print(f"Report: {report_path}")


if __name__ == "__main__":
    main()
