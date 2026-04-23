#!/usr/bin/env python3
"""CLI entry point for the student simulator stability experiment."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from experiments.student_sim_stability.core.paths import BENCH_ROOT

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from server.config.bootstrap import load_server_env  # noqa: E402

load_server_env(BENCH_ROOT)

_ROBUSTNESS_DIMENSIONS = ("D1", "D2", "D3", "D4", "control")
_CONTROLLED_DIMENSIONS = ("P1", "B1")


def _add_output_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Experiment results directory (default: config.OUTPUT_DIR)",
    )


def _make_parser() -> argparse.ArgumentParser:
    from experiments.student_sim_stability.core.config import (
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
        help="Run full issue83 pipeline: probes → scripted → generate → render → judge panel → aggregate → audit → report → validate",
    )
    _add_output_arg(run_all)
    run_all.add_argument("-w", "--workers", type=int, default=None)
    run_all.add_argument("--judge-workers", type=int, default=JUDGE_MAX_WORKERS)
    run_all.add_argument(
        "--judge-model-workers",
        type=int,
        default=None,
        help="Judge models to run in parallel; defaults to all configured judge models",
    )

    generate = sub.add_parser("generate", help="Generate conversations")
    _add_output_arg(generate)
    generate.add_argument("-w", "--workers", type=int, default=None)
    generate.add_argument("-n", "--limit", type=int, default=None)
    generate.add_argument("--phase", choices=["live", "control"], default=None)

    render = sub.add_parser("render-judges", help="Render judge prompt files")
    _add_output_arg(render)
    render.add_argument(
        "--dimension",
        default="all",
        choices=["D1", "D2", "D3", "D4", "control", "P1", "B1", "all"],
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
        choices=["D1", "D2", "D3", "D4", "control", "P1", "B1", "all"],
    )
    judge.add_argument("--manifest", type=Path, default=None)
    judge.add_argument("-n", "--limit", type=int, default=None)
    judge.add_argument("-w", "--workers", type=int, default=JUDGE_MAX_WORKERS)
    judge.add_argument("--model", default=JUDGE_MODEL)
    judge.add_argument(
        "--all-models",
        action="store_true",
        help="Run all configured judge models and mirror the primary judge outputs for aggregation",
    )
    judge.add_argument(
        "--judge-model-workers",
        type=int,
        default=None,
        help="Judge models to run in parallel when --all-models is used",
    )
    judge.add_argument("--temperature", type=float, default=JUDGE_TEMPERATURE)
    judge.add_argument("--overwrite", action="store_true")
    judge.add_argument("--max-retries", type=int, default=3)
    judge.add_argument("--retry-delay", type=float, default=2.0)

    aggregate = sub.add_parser("aggregate", help="Aggregate judge outputs")
    _add_output_arg(aggregate)
    aggregate.add_argument("--strict", action="store_true")
    aggregate.add_argument("--profile", choices=["full", "pilot"], default="full")

    validate = sub.add_parser("validate", help="Validate generated artifacts")
    _add_output_arg(validate)
    validate.add_argument("--strict", action="store_true")
    validate.add_argument("--json", action="store_true")
    validate.add_argument("--profile", choices=["full", "pilot"], default="full")

    report = sub.add_parser("report", help="Generate HTML report")
    _add_output_arg(report)
    report.add_argument("--profile", choices=["full", "pilot"], default="full")

    probes = sub.add_parser("probes", help="Run targeted persona probes")
    _add_output_arg(probes)
    probes.add_argument("--model", default=None)
    probes.add_argument("-n", "--limit", type=int, default=None)
    probes.add_argument("-w", "--workers", type=int, default=None)

    scripted = sub.add_parser("scripted", help="Run scripted multi-turn dialogues")
    _add_output_arg(scripted)
    scripted.add_argument("--model", default=None)
    scripted.add_argument("-n", "--limit", type=int, default=None)
    scripted.add_argument("-w", "--workers", type=int, default=None)

    audit = sub.add_parser("audit", help="Write data quality audit artifacts")
    _add_output_arg(audit)
    audit.add_argument("--profile", choices=["full", "pilot"], default="full")

    human = sub.add_parser("human-alignment", help="Initialize human label artifacts")
    _add_output_arg(human)
    human.add_argument("--sample-limit", type=int, default=50)
    human.add_argument("--compute", action="store_true")
    human.add_argument("--labels", type=Path, default=None)

    agreement = sub.add_parser("judge-agreement", help="Compute multi-judge agreement")
    _add_output_arg(agreement)

    pilot = sub.add_parser(
        "pilot",
        help="Generate a small issue83 pilot: snapshots, probes, scripted dialogues, and limited live/control conversations",
    )
    pilot.add_argument(
        "--output-dir",
        default="experiments/student_sim_stability/results/issue83_pilot",
        help="Pilot results directory",
    )
    pilot.add_argument("--model", default=None)
    pilot.add_argument("--conversation-limit", type=int, default=10)
    pilot.add_argument("-w", "--workers", type=int, default=None)
    pilot.add_argument("--judge-workers", type=int, default=JUDGE_MAX_WORKERS)
    pilot.add_argument(
        "--judge-model-workers",
        type=int,
        default=None,
        help="Judge models to run in parallel; defaults to all configured judge models",
    )

    return parser


def _print_dry_run() -> None:
    from experiments.student_sim_stability.core.config import (
        JUDGE_MODELS,
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
    print(f"  Judge models:       {', '.join(m.split('/')[-1] for m in JUDGE_MODELS)}")
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
    from experiments.student_sim_stability.pipeline.runner import ExperimentRunner

    return ExperimentRunner(output_dir=output_dir)


def _results_dir(output_dir: str | None) -> Path:
    from experiments.student_sim_stability.core.config import OUTPUT_DIR

    out = Path(output_dir or OUTPUT_DIR)
    if not out.is_absolute():
        out = BENCH_ROOT / out
    return out


def _score_controlled_gate(judge_input_dir: Path, judge_output_dir: Path) -> dict:
    """Score P1/B1 only from outputs matching the current rendered inputs."""
    required = [("P1", "overall_probe_pass"), ("B1", "contract_fit")]
    threshold = 3.0
    scores: list[float] = []
    scores_by_prefix: dict[str, list[float]] = {prefix: [] for prefix, _ in required}
    missing: list[str] = []
    malformed: list[str] = []
    b1_identity_mismatches: list[str] = []
    b1_identity_compared = 0
    b1_identity_correct = 0
    counts: dict[str, int] = {}

    for prefix, field in required:
        input_files = sorted(judge_input_dir.glob(f"{prefix}__*.json"))
        counts[prefix] = len(input_files)
        if not input_files:
            missing.append(f"{prefix}: no current judge inputs")
            continue
        for input_path in input_files:
            output_path = judge_output_dir / input_path.name
            if not output_path.exists():
                missing.append(output_path.name)
                continue
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            output_scores = payload.get("scores", {})
            if prefix == "B1":
                input_payload = json.loads(input_path.read_text(encoding="utf-8"))
                expected_persona = str(
                    input_payload.get("metadata", {}).get("persona_id", "")
                ).strip()
                identified_persona = str(
                    output_scores.get("identified_persona", "")
                ).strip()
                if not expected_persona:
                    malformed.append(f"{input_path.name}: missing metadata persona_id")
                    continue
                if not identified_persona:
                    malformed.append(f"{output_path.name}: missing identified_persona")
                    continue
                b1_identity_compared += 1
                if identified_persona == expected_persona:
                    b1_identity_correct += 1
                else:
                    b1_identity_mismatches.append(
                        f"{output_path.name}: expected {expected_persona}, got {identified_persona}"
                    )
            value = output_scores.get(field)
            if not isinstance(value, (int, float)):
                malformed.append(f"{output_path.name}: missing numeric {field}")
                continue
            score = float(value)
            scores.append(score)
            scores_by_prefix[prefix].append(score)

    mean = sum(scores) / len(scores) if scores else 0.0
    dimension_means = {
        prefix: sum(values) / len(values) if values else 0.0
        for prefix, values in scores_by_prefix.items()
    }
    dimension_ok = {
        prefix: bool(scores_by_prefix[prefix]) and dimension_means[prefix] >= threshold
        for prefix, _ in required
    }
    b1_identity_ok = (
        bool(counts.get("B1"))
        and b1_identity_compared == counts["B1"]
        and not b1_identity_mismatches
    )
    dimension_ok["B1"] = dimension_ok["B1"] and b1_identity_ok
    return {
        "ok": not missing
        and not malformed
        and not b1_identity_mismatches
        and all(dimension_ok.values()),
        "mean": mean,
        "n": len(scores),
        "threshold": threshold,
        "dimension_means": dimension_means,
        "dimension_counts": {
            prefix: len(values) for prefix, values in scores_by_prefix.items()
        },
        "dimension_ok": dimension_ok,
        "input_counts": counts,
        "b1_identification": {
            "correct": b1_identity_correct,
            "compared": b1_identity_compared,
            "accuracy": (
                b1_identity_correct / b1_identity_compared
                if b1_identity_compared
                else 0.0
            ),
            "mismatches": b1_identity_mismatches,
        },
        "missing": missing,
        "malformed": malformed,
    }


def _pilot_trial_keys(model: str, live_limit: int) -> tuple[set[str], set[str], dict]:
    from experiments.student_sim_stability.core.config import TrialKey

    if live_limit < 8:
        raise ValueError(
            "Pilot live limit must be at least 8 to cover 4 personas across 2 tutor temperatures."
        )

    task_personas = [
        ("I01_implement_sma", "developer_crossover"),
        ("I01_implement_sma", "fullstack_practitioner"),
        ("S03_mean_reversion_research", "finance_veteran"),
        ("S03_mean_reversion_research", "double_novice"),
    ]
    live_keys: list[str] = []
    for task_id, persona_id in task_personas:
        for tutor_temp in [0.0, 1.0]:
            live_keys.append(
                TrialKey("live", task_id, persona_id, model, 0, tutor_temp).key
            )
    extra_candidates = [
        TrialKey("live", task_id, persona_id, model, 1, 0.0).key
        for task_id, persona_id in task_personas
    ]
    live_keys.extend(extra_candidates[: max(0, live_limit - len(live_keys))])
    live_keys = live_keys[:live_limit]
    control_keys = {
        TrialKey("control", task_id, persona_id, model, 0, 0.0).key
        for task_id, persona_id in task_personas
    }
    metadata = {
        "pilot_tasks": sorted({task_id for task_id, _ in task_personas}),
        "pilot_personas": sorted({persona_id for _, persona_id in task_personas}),
        "pilot_model": model,
        "live_keys": live_keys,
        "control_keys": sorted(control_keys),
    }
    return set(live_keys), control_keys, metadata


def _render_judges(args: argparse.Namespace) -> None:
    from experiments.student_sim_stability.core.artifacts import (
        snapshot_static_artifacts,
    )
    from experiments.student_sim_stability.pipeline.render_judge_prompts import (
        clean_rendered_prompts,
        render_b1,
        render_control,
        render_d1,
        render_d2,
        render_d3,
        render_d4,
        render_p1,
    )

    results_dir = _results_dir(args.output_dir)
    snapshot_static_artifacts(results_dir)
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
    if args.dimension in ("P1", "all"):
        render_p1(results_dir, judge_input_dir)
    if args.dimension in ("B1", "all"):
        render_b1(results_dir, judge_input_dir)


def _judge(args: argparse.Namespace) -> int:
    from experiments.student_sim_stability.pipeline.judge import (
        run_judge,
        run_judge_for_models,
    )

    results_dir = _results_dir(args.output_dir)
    if args.all_models:
        stats = run_judge_for_models(
            input_dir=results_dir / "judge_inputs",
            primary_output_dir=results_dir / "judge_outputs",
            by_model_output_dir=results_dir / "judge_outputs_by_model",
            dimension=args.dimension,
            manifest=args.manifest,
            limit=args.limit,
            workers=args.workers,
            temperature=args.temperature,
            overwrite=args.overwrite,
            max_retries=args.max_retries,
            retry_delay=args.retry_delay,
            require_inputs=True,
            clean_stale=args.overwrite,
            model_workers=args.judge_model_workers,
        )
    else:
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
            require_inputs=True,
        )
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 1 if stats["failed"] else 0


def _aggregate(args: argparse.Namespace) -> None:
    from experiments.student_sim_stability.pipeline.aggregate import aggregate

    results_dir = _results_dir(args.output_dir)
    summary = aggregate(
        judge_input_dir=results_dir / "judge_inputs",
        judge_output_dir=results_dir / "judge_outputs",
        output_path=results_dir / "evaluations" / "all_evaluations.json",
        strict=args.strict,
        profile=args.profile,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def _run_all(args: argparse.Namespace) -> int:
    """Run the full experiment pipeline end-to-end."""
    import os

    from experiments.student_sim_stability.analysis.data_quality import run_audit
    from experiments.student_sim_stability.analysis.human_alignment import (
        init_human_alignment,
    )
    from experiments.student_sim_stability.analysis.judge_agreement import (
        compute_judge_agreement,
    )
    from experiments.student_sim_stability.analysis.validate import validate
    from experiments.student_sim_stability.core.artifacts import (
        snapshot_static_artifacts,
    )
    from experiments.student_sim_stability.core.config import (
        D1_SAMPLE_POLICY,
        JUDGE_MODELS,
        MAX_WORKERS,
    )
    from experiments.student_sim_stability.pipeline.aggregate import aggregate
    from experiments.student_sim_stability.pipeline.judge import (
        clean_judge_outputs,
        run_judge_for_models,
        safe_model_dir,
    )
    from experiments.student_sim_stability.pipeline.probes import run_probes
    from experiments.student_sim_stability.pipeline.render_judge_prompts import (
        clean_rendered_prompts,
        render_b1,
        render_control,
        render_d1,
        render_d2,
        render_d3,
        render_d4,
        render_p1,
    )
    from experiments.student_sim_stability.pipeline.runner import ExperimentRunner
    from experiments.student_sim_stability.pipeline.scripted_dialogues import (
        run_scripted_dialogues,
    )

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY environment variable is not set")
        return 1

    results_dir = _results_dir(args.output_dir)
    snapshot_static_artifacts(results_dir)
    conv_dir = results_dir / "conversations"
    judge_input_dir = results_dir / "judge_inputs"
    judge_output_dir = results_dir / "judge_outputs"
    eval_path = results_dir / "evaluations" / "all_evaluations.json"

    # Step 1: Generate conversations
    print("\n=== Step 1/7: Run controlled probes and scripted dialogues ===")
    controlled_workers = args.workers or MAX_WORKERS
    print(
        json.dumps(
            run_probes(results_dir, workers=controlled_workers),
            indent=2,
            ensure_ascii=False,
        )
    )
    print(
        json.dumps(
            run_scripted_dialogues(results_dir, workers=controlled_workers),
            indent=2,
            ensure_ascii=False,
        )
    )
    clean_rendered_prompts(judge_input_dir, "P1")
    clean_rendered_prompts(judge_input_dir, "B1")
    render_p1(results_dir, judge_input_dir)
    render_b1(results_dir, judge_input_dir)
    clean_judge_outputs(judge_output_dir, "P1")
    clean_judge_outputs(judge_output_dir, "B1")
    for judge_model in JUDGE_MODELS:
        clean_judge_outputs(
            results_dir / "judge_outputs_by_model" / safe_model_dir(judge_model),
            "P1",
        )
        clean_judge_outputs(
            results_dir / "judge_outputs_by_model" / safe_model_dir(judge_model),
            "B1",
        )
    print("\n=== Step 1b/7: Judge controlled probes before live robustness ===")
    probe_judge_stats = run_judge_for_models(
        input_dir=judge_input_dir,
        primary_output_dir=judge_output_dir,
        by_model_output_dir=results_dir / "judge_outputs_by_model",
        dimension="P1",
        workers=args.judge_workers,
        model_workers=args.judge_model_workers,
        require_inputs=True,
        clean_stale=True,
    )
    blind_judge_stats = run_judge_for_models(
        input_dir=judge_input_dir,
        primary_output_dir=judge_output_dir,
        by_model_output_dir=results_dir / "judge_outputs_by_model",
        dimension="B1",
        workers=args.judge_workers,
        model_workers=args.judge_model_workers,
        require_inputs=True,
        clean_stale=True,
    )
    print(json.dumps({"P1": probe_judge_stats, "B1": blind_judge_stats}, indent=2))
    if probe_judge_stats["failed"] or blind_judge_stats["failed"]:
        print("ERROR: controlled validation judge calls failed")
        return 1
    controlled_gate = _score_controlled_gate(judge_input_dir, judge_output_dir)
    if not controlled_gate["ok"]:
        print(
            "ERROR: controlled persona validation did not pass before live run "
            f"(mean={controlled_gate['mean']:.2f}, n={controlled_gate['n']}, "
            f"dimension_means={controlled_gate['dimension_means']}, "
            f"dimension_ok={controlled_gate['dimension_ok']}, "
            f"missing={controlled_gate['missing'][:5]}, "
            f"malformed={controlled_gate['malformed'][:5]})"
        )
        return 1

    print("\n=== Step 2/7: Generate conversations ===")
    runner = ExperimentRunner(output_dir=args.output_dir)
    generate_stats = runner.run_generate(max_workers=args.workers or MAX_WORKERS)
    print(json.dumps(generate_stats, indent=2, ensure_ascii=False))
    if generate_stats["failed"]:
        print(f"ERROR: {generate_stats['failed']} conversation trials failed")
        return 1

    # Step 2: Render judge prompts
    print("\n=== Step 3/7: Render judge prompts ===")
    for dimension in _ROBUSTNESS_DIMENSIONS:
        clean_rendered_prompts(judge_input_dir, dimension)
        clean_judge_outputs(judge_output_dir, dimension)
        for judge_model in JUDGE_MODELS:
            clean_judge_outputs(
                results_dir / "judge_outputs_by_model" / safe_model_dir(judge_model),
                dimension,
            )
    render_d1(conv_dir, judge_input_dir, sample_policy=D1_SAMPLE_POLICY)
    render_d2(conv_dir, judge_input_dir)
    render_d3(conv_dir, judge_input_dir)
    render_d4(conv_dir, judge_input_dir)
    render_control(conv_dir, judge_input_dir)
    init_human_alignment(results_dir)

    # Step 3: Run judge
    print("\n=== Step 4/7: Run configured judge panel ===")
    stats = run_judge_for_models(
        input_dir=judge_input_dir,
        primary_output_dir=judge_output_dir,
        by_model_output_dir=results_dir / "judge_outputs_by_model",
        dimension="all",
        workers=args.judge_workers,
        model_workers=args.judge_model_workers,
        exclude_dimensions=_CONTROLLED_DIMENSIONS,
        require_inputs=True,
        clean_stale=True,
    )
    print(json.dumps(stats, indent=2))
    if stats["failed"]:
        print(f"ERROR: {stats['failed']} judge calls failed")
        return 1

    print("\n=== Step 4b/7: Compute multi-judge agreement ===")
    print(
        json.dumps(compute_judge_agreement(results_dir), indent=2, ensure_ascii=False)
    )

    # Step 5: Aggregate
    print("\n=== Step 5/7: Aggregate primary judge outputs ===")
    summary = aggregate(
        judge_input_dir=judge_input_dir,
        judge_output_dir=judge_output_dir,
        output_path=eval_path,
        strict=True,
        profile="full",
    )
    print(json.dumps(summary, indent=2))
    init_human_alignment(results_dir)

    # Step 6: Data quality audit artifacts
    print("\n=== Step 6/7: Data quality audit ===")
    audit = run_audit(results_dir, profile="full")
    print(json.dumps({"ok": audit["ok"]}, indent=2))
    if not audit["ok"]:
        print("ERROR: data quality audit failed")
        return 1

    # Step 7: Report
    print("\n=== Step 7/7: Generate report ===")
    try:
        from experiments.student_sim_stability.analysis.report import ReportGenerator
    except ModuleNotFoundError as exc:
        if exc.name == "matplotlib":
            print("ERROR: report generation requires matplotlib")
            return 1
        raise

    gen = ReportGenerator(str(eval_path), str(results_dir / "report"))
    report_path = gen.generate()
    print(f"Report: {report_path}")

    # Step 7b: Final validation
    print("\n=== Step 7b/7: Final validation ===")
    checks, _ = validate(results_dir, profile="full")
    failures = [check for check in checks if not check.ok and check.required]
    for check in checks:
        status = "OK" if check.ok else ("FAIL" if check.required else "WARN")
        print(f"[{status}] {check.name}: {check.message}")
    if failures:
        print(f"ERROR: {len(failures)} required validation checks failed")
        return 1
    return 0


def _validate(args: argparse.Namespace) -> int:
    from experiments.student_sim_stability.analysis.validate import validate

    checks, summary = validate(_results_dir(args.output_dir), profile=args.profile)
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
        from experiments.student_sim_stability.core.config import MAX_WORKERS

        runner = _runner(args.output_dir)
        stats = runner.run_generate(
            max_workers=args.workers or MAX_WORKERS,
            limit=args.limit,
            phase=args.phase,
        )
        print(json.dumps(stats, indent=2, ensure_ascii=False))
        return 1 if stats["failed"] else 0

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
            from experiments.student_sim_stability.analysis.report import (
                ReportGenerator,
            )
            from experiments.student_sim_stability.analysis.validate import validate
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
        checks, _ = validate(results_dir, profile=args.profile, require_audit=False)
        failures = [check for check in checks if not check.ok and check.required]
        if failures:
            print("ERROR: Cannot generate report from incomplete artifacts:")
            for check in failures[:10]:
                print(f"  - {check.name}: {check.message}")
            return 1
        gen = ReportGenerator(str(eval_path), str(results_dir / "report"))
        report_path = gen.generate()
        print(f"Report: {report_path}")
        return 0

    if args.command == "probes":
        from experiments.student_sim_stability.core.artifacts import (
            snapshot_static_artifacts,
        )
        from experiments.student_sim_stability.pipeline.probes import run_probes

        results_dir = _results_dir(args.output_dir)
        snapshot_static_artifacts(results_dir)
        from experiments.student_sim_stability.core.config import MAX_WORKERS

        manifest = run_probes(
            results_dir,
            model=args.model,
            limit=args.limit,
            workers=args.workers or MAX_WORKERS,
        )
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return 0

    if args.command == "scripted":
        from experiments.student_sim_stability.core.artifacts import (
            snapshot_static_artifacts,
        )
        from experiments.student_sim_stability.pipeline.scripted_dialogues import (
            run_scripted_dialogues,
        )

        results_dir = _results_dir(args.output_dir)
        snapshot_static_artifacts(results_dir)
        from experiments.student_sim_stability.core.config import MAX_WORKERS

        manifest = run_scripted_dialogues(
            results_dir,
            model=args.model,
            limit=args.limit,
            workers=args.workers or MAX_WORKERS,
        )
        print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return 0

    if args.command == "audit":
        from experiments.student_sim_stability.analysis.data_quality import run_audit

        audit = run_audit(_results_dir(args.output_dir), profile=args.profile)
        print(json.dumps(audit, indent=2, ensure_ascii=False))
        return 0 if audit["ok"] else 1

    if args.command == "human-alignment":
        from experiments.student_sim_stability.analysis.human_alignment import (
            compute_human_agreement,
            init_human_alignment,
        )

        results_dir = _results_dir(args.output_dir)
        if args.compute:
            report = compute_human_agreement(results_dir, labels_path=args.labels)
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            manifest = init_human_alignment(
                results_dir,
                sample_limit=args.sample_limit,
            )
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
        return 0

    if args.command == "judge-agreement":
        from experiments.student_sim_stability.analysis.judge_agreement import (
            compute_judge_agreement,
        )

        report = compute_judge_agreement(_results_dir(args.output_dir))
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.command == "pilot":
        from experiments.student_sim_stability.analysis.data_quality import run_audit
        from experiments.student_sim_stability.analysis.human_alignment import (
            init_human_alignment,
        )
        from experiments.student_sim_stability.analysis.judge_agreement import (
            compute_judge_agreement,
        )
        from experiments.student_sim_stability.analysis.report import ReportGenerator
        from experiments.student_sim_stability.analysis.validate import validate
        from experiments.student_sim_stability.core.artifacts import (
            snapshot_static_artifacts,
        )
        from experiments.student_sim_stability.core.config import (
            D1_SAMPLE_POLICY,
            JUDGE_MODELS,
            MAX_WORKERS,
            STUDENT_MODELS,
        )
        from experiments.student_sim_stability.pipeline.aggregate import aggregate
        from experiments.student_sim_stability.pipeline.judge import (
            clean_judge_outputs,
            run_judge_for_models,
            safe_model_dir,
        )
        from experiments.student_sim_stability.pipeline.probes import run_probes
        from experiments.student_sim_stability.pipeline.render_judge_prompts import (
            clean_rendered_prompts,
            render_b1,
            render_control,
            render_d1,
            render_d2,
            render_d3,
            render_d4,
            render_p1,
        )
        from experiments.student_sim_stability.pipeline.runner import ExperimentRunner
        from experiments.student_sim_stability.pipeline.scripted_dialogues import (
            run_scripted_dialogues,
        )

        results_dir = _results_dir(args.output_dir)
        pilot_model = args.model or STUDENT_MODELS[0]
        try:
            live_keys, control_keys, pilot_metadata = _pilot_trial_keys(
                pilot_model, args.conversation_limit
            )
        except ValueError as exc:
            print(f"ERROR: {exc}")
            return 1
        snapshot_static_artifacts(results_dir)

        judge_input_dir = results_dir / "judge_inputs"
        judge_output_dir = results_dir / "judge_outputs"
        by_model_dir = results_dir / "judge_outputs_by_model"
        conv_dir = results_dir / "conversations"
        eval_path = results_dir / "evaluations" / "all_evaluations.json"

        pilot_workers = args.workers or min(6, MAX_WORKERS)
        probe_manifest = run_probes(
            results_dir,
            model=pilot_model,
            workers=pilot_workers,
        )
        scripted_manifest = run_scripted_dialogues(
            results_dir,
            model=pilot_model,
            workers=pilot_workers,
        )
        clean_rendered_prompts(judge_input_dir, "P1")
        clean_rendered_prompts(judge_input_dir, "B1")
        render_p1(results_dir, judge_input_dir)
        render_b1(results_dir, judge_input_dir)
        clean_judge_outputs(judge_output_dir, "P1")
        clean_judge_outputs(judge_output_dir, "B1")
        for judge_model in JUDGE_MODELS:
            clean_judge_outputs(by_model_dir / safe_model_dir(judge_model), "P1")
            clean_judge_outputs(by_model_dir / safe_model_dir(judge_model), "B1")
        probe_judge_stats = run_judge_for_models(
            input_dir=judge_input_dir,
            primary_output_dir=judge_output_dir,
            by_model_output_dir=by_model_dir,
            dimension="P1",
            workers=args.judge_workers,
            model_workers=args.judge_model_workers,
            require_inputs=True,
            clean_stale=True,
        )
        blind_judge_stats = run_judge_for_models(
            input_dir=judge_input_dir,
            primary_output_dir=judge_output_dir,
            by_model_output_dir=by_model_dir,
            dimension="B1",
            workers=args.judge_workers,
            model_workers=args.judge_model_workers,
            require_inputs=True,
            clean_stale=True,
        )
        controlled_gate = _score_controlled_gate(judge_input_dir, judge_output_dir)
        if (
            probe_judge_stats["failed"]
            or blind_judge_stats["failed"]
            or not controlled_gate["ok"]
        ):
            print(
                "ERROR: pilot controlled validation failed before live robustness: "
                + json.dumps(
                    {
                        "P1": probe_judge_stats,
                        "B1": blind_judge_stats,
                        "gate": controlled_gate,
                    },
                    ensure_ascii=False,
                )
            )
            return 1

        runner = ExperimentRunner(output_dir=args.output_dir)
        live_stats = runner.run_generate(
            max_workers=pilot_workers,
            phase="live",
            allowed_keys=live_keys,
        )
        control_stats = runner.run_generate(
            max_workers=pilot_workers,
            phase="control",
            allowed_keys=control_keys,
        )
        if live_stats["failed"] or control_stats["failed"]:
            print("ERROR: pilot conversation generation failed")
            return 1

        for dimension in _ROBUSTNESS_DIMENSIONS:
            clean_rendered_prompts(judge_input_dir, dimension)
            clean_judge_outputs(judge_output_dir, dimension)
            for judge_model in JUDGE_MODELS:
                clean_judge_outputs(
                    by_model_dir / safe_model_dir(judge_model),
                    dimension,
                )
        render_d1(conv_dir, judge_input_dir, sample_policy=D1_SAMPLE_POLICY)
        render_d2(conv_dir, judge_input_dir)
        render_d3(conv_dir, judge_input_dir)
        render_d4(conv_dir, judge_input_dir)
        render_control(conv_dir, judge_input_dir)
        judge_stats = run_judge_for_models(
            input_dir=judge_input_dir,
            primary_output_dir=judge_output_dir,
            by_model_output_dir=by_model_dir,
            dimension="all",
            workers=args.judge_workers,
            model_workers=args.judge_model_workers,
            exclude_dimensions=_CONTROLLED_DIMENSIONS,
            require_inputs=True,
            clean_stale=True,
        )
        if judge_stats["failed"]:
            print("ERROR: pilot judge panel failed")
            print(json.dumps(judge_stats, indent=2, ensure_ascii=False))
            return 1
        agreement = compute_judge_agreement(results_dir)
        aggregate_summary = aggregate(
            judge_input_dir=judge_input_dir,
            judge_output_dir=judge_output_dir,
            output_path=eval_path,
            strict=True,
            profile="pilot",
        )
        human_manifest = init_human_alignment(results_dir)
        audit = run_audit(results_dir, profile="pilot")
        if not audit["ok"]:
            print("ERROR: pilot data quality audit failed")
            print(json.dumps(audit, indent=2, ensure_ascii=False))
            return 1
        report_path = ReportGenerator(
            str(eval_path), str(results_dir / "report")
        ).generate()
        checks, _ = validate(results_dir, profile="pilot")
        failures = [check for check in checks if not check.ok]
        if failures:
            print("ERROR: pilot strict validation failed")
            for check in failures[:20]:
                print(f"  - {check.name}: {check.message}")
            return 1
        print(
            json.dumps(
                {
                    "results_dir": str(results_dir),
                    "pilot_selection": pilot_metadata,
                    "probes": probe_manifest,
                    "scripted": scripted_manifest,
                    "controlled_gate": controlled_gate,
                    "live_generation": live_stats,
                    "control_generation": control_stats,
                    "judge": judge_stats,
                    "judge_agreement": agreement,
                    "aggregate": aggregate_summary,
                    "human_alignment": human_manifest,
                    "audit_ok": audit["ok"],
                    "report": str(report_path),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
