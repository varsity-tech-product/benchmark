#!/usr/bin/env python3
"""CLI entry point for the student simulator stability experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

BENCH_ROOT = Path(__file__).resolve().parents[2]
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from server.config.bootstrap import load_server_env  # noqa: E402

load_server_env(BENCH_ROOT)

from experiments.student_sim_stability.core.rubrics import (
    DIMENSION_TO_FILE,
)

_ALL_JUDGE_DIMENSIONS = tuple(DIMENSION_TO_FILE)

# The paper appendix carries exactly these 23 artifacts. Anything else
# emitted by the report (HTML wrappers, unused matplotlib PDFs, drift
# tables, intermediate manifests) stays in the components directory but is
# not copied into ``paper/figs/student_sim/``.
_PAPER_EXPORT_TEX = (
    "judge_qualification.tex",
    "d1_by_model.tex",
    "d1_by_persona.tex",
    "d1_by_task.tex",
    "d2_by_model.tex",
    "d2_by_model_temp.tex",
    "d3_drift.tex",
    "judge_configuration.tex",
    "multi_judge_view.tex",
    "ranking_table.tex",
    "failure_taxonomy.tex",
    "failure_by_dimension.tex",
    "human_alignment_metrics.tex",
    "human_alignment_b1_breakdown.tex",
    "human_alignment_b1_per_judge.tex",
)
_PAPER_EXPORT_PGF = (
    "d1_heatmap.pgf.tex",
    "d3_curves.pgf.tex",
    "b1_identification.pgf.tex",
    "control_bars.pgf.tex",
)
_PAPER_EXPORT_CSV = (
    "d1_heatmap.csv",
    "d3_curves.csv",
    "b1_identification.csv",
    "control_bars.csv",
)


def _paper_export(target_dir: Path, components_dir: Path) -> dict:
    """Copy the appendix-bound subset of component artifacts into
    ``target_dir`` and emit a ``manifest.json`` with ``sha256`` per asset.

    The paper allowlist is the 23 artifacts defined above (15 ``.tex``
    tables, 4 ``.pgf.tex`` figures, 4 backing CSVs). Stale files that match
    the legacy ``.tex/.pdf/.csv/.html`` glob but are no longer in the
    allowlist (e.g. ``d2_bars.pdf``, ``data_quality_audit.tex``) are
    deleted from ``target_dir`` so the directory ends each run with
    exactly the allowlisted contents plus the manifest. Idempotent against
    unchanged components.
    """
    target_dir = Path(target_dir)
    components_dir = Path(components_dir)
    if not components_dir.exists():
        raise FileNotFoundError(f"components directory not found: {components_dir}")
    target_dir.mkdir(parents=True, exist_ok=True)
    allowlist = set(_PAPER_EXPORT_TEX) | set(_PAPER_EXPORT_PGF) | set(_PAPER_EXPORT_CSV)
    keep = allowlist | {"manifest.json"}
    for stale in target_dir.iterdir():
        if stale.is_file() and stale.name not in keep:
            stale.unlink()
    assets: list[dict] = []
    for name in sorted(allowlist):
        src = components_dir / name
        if not src.exists():
            continue
        payload = src.read_bytes()
        digest = hashlib.sha256(payload).hexdigest()
        target = target_dir / name
        target.write_bytes(payload)
        kind = "pgf" if name.endswith(".pgf.tex") else name.rsplit(".", 1)[-1]
        assets.append(
            {
                "name": name,
                "kind": kind,
                "size_bytes": len(payload),
                "sha256": digest,
            }
        )
    by_kind: dict[str, int] = {}
    for asset in assets:
        by_kind[asset["kind"]] = by_kind.get(asset["kind"], 0) + 1
    manifest = {
        "schema_version": "paper_export_v2",
        "n_assets": len(assets),
        "by_kind": by_kind,
        "assets": assets,
    }
    manifest_path = target_dir / "manifest.json"
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return manifest


def _add_output_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Experiment results directory (default: config.OUTPUT_DIR)",
    )


def _add_judge_qualification_dir_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--judge-qualification-dir",
        default=None,
        help="Standalone judge-qualification directory (default: results/judge_qualification)",
    )


def _make_parser() -> argparse.ArgumentParser:
    from experiments.student_sim_stability.core.config import (
        JUDGE_MAX_WORKERS,
        JUDGE_MODEL,
        JUDGE_TEMPERATURE,
        S1_SAMPLE_POLICY,
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
        help="Run the full student-sim-stability pipeline: probes → live generation → render (S1-S6) → judge panel → aggregate → audit → report → validate",
    )
    _add_output_arg(run_all)
    _add_judge_qualification_dir_arg(run_all)
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
        choices=(*DIMENSION_TO_FILE, "all"),
    )
    render.add_argument(
        "--s1-sample-policy",
        default=S1_SAMPLE_POLICY,
        choices=["all", "live-r0-tt0"],
        help="S1 prompt sampling policy for rendered judge inputs",
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
        choices=(*DIMENSION_TO_FILE, "all"),
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

    aggregate_mj = sub.add_parser(
        "aggregate-multi-judge",
        help=(
            "Compute 5-view multi-judge aggregates from judge_outputs_by_model/; "
            "output goes to evaluations/multi_judge_aggregates.json. Purely local, "
            "no LLM calls. Re-runnable any time after judge has completed."
        ),
    )
    _add_output_arg(aggregate_mj)
    aggregate_mj.add_argument("--strict", action="store_true")

    validate = sub.add_parser("validate", help="Validate generated artifacts")
    _add_output_arg(validate)
    validate.add_argument("--strict", action="store_true")
    validate.add_argument("--json", action="store_true")
    validate.add_argument("--profile", choices=["full", "pilot"], default="full")

    report = sub.add_parser("report", help="Generate HTML report")
    _add_output_arg(report)
    _add_judge_qualification_dir_arg(report)
    report.add_argument("--profile", choices=["full", "pilot"], default="full")
    report.add_argument(
        "--skip-validate",
        action="store_true",
        help="Downgrade validator failures to warnings and generate the report against existing artifacts (e.g. when stale judge outputs cannot be regenerated).",
    )

    paper_export = sub.add_parser(
        "paper-export",
        help="Bundle Component artifacts (.tex/.pdf/.csv/.html) into a paper asset directory + manifest.",
    )
    paper_export.add_argument(
        "--target",
        type=Path,
        required=True,
        help="Destination directory (created if missing).",
    )
    paper_export.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Source components directory (default: <results-dir>/report/components/).",
    )
    _add_output_arg(paper_export)

    probes = sub.add_parser("probes", help="Run targeted persona probes")
    _add_output_arg(probes)
    probes.add_argument("--model", default=None)
    probes.add_argument("-n", "--limit", type=int, default=None)
    probes.add_argument("-w", "--workers", type=int, default=None)

    audit = sub.add_parser("audit", help="Write data quality audit artifacts")
    _add_output_arg(audit)
    audit.add_argument("--profile", choices=["full", "pilot"], default="full")

    human = sub.add_parser("human-alignment", help="Initialize human label artifacts")
    _add_output_arg(human)
    human.add_argument("--sample-limit", type=int, default=50)
    human.add_argument("--compute", action="store_true")
    human.add_argument("--labels", type=Path, default=None)

    human_extend = sub.add_parser(
        "human-alignment-extend",
        help="Extend the human-alignment sample pool to per-cell targets",
    )
    human_extend.add_argument(
        "--target",
        required=True,
        help="DIM=N[,DIM=N...] or, with --dimension, scalar N / DIM=N",
    )
    human_extend.add_argument(
        "--key-fields",
        default="dimension,persona_id,model",
        help="Comma-separated cell key fields (default: dimension,persona_id,model)",
    )
    human_extend.add_argument(
        "--dimension",
        choices=tuple(DIMENSION_TO_FILE),
        default=None,
        help="Restrict candidates to one dimension",
    )
    human_extend.add_argument("--seed", type=int, default=2026)
    _add_output_arg(human_extend)
    human_extend.add_argument("--dry-run", action="store_true")

    agreement = sub.add_parser("judge-agreement", help="Compute multi-judge agreement")
    _add_output_arg(agreement)

    judge_qualification = sub.add_parser(
        "judge-qualification",
        help="Run the judge-qualification reliability/sensitivity gate against the fixed golden corpus",
    )
    judge_qualification_sub = judge_qualification.add_subparsers(
        dest="judge_qualification_command",
        required=True,
    )

    judge_qualification_render = judge_qualification_sub.add_parser(
        "render",
        help="Render judge-qualification inputs from the fixed corpus",
    )
    _add_judge_qualification_dir_arg(judge_qualification_render)
    judge_qualification_render.add_argument("--corpus", type=Path, default=None)
    judge_qualification_render.add_argument("--repeats", type=int, default=None)
    judge_qualification_render.add_argument("--prompt-variants", default=None)
    judge_qualification_render.add_argument("--clean", action="store_true")

    judge_qualification_judge = judge_qualification_sub.add_parser(
        "judge",
        help=(
            "Judge rendered judge-qualification inputs against all configured "
            "JUDGE_MODELS. Single-judge qualification is intentionally not "
            "supported: the gate exists to qualify the full panel, so every "
            "judge run must cover every model."
        ),
    )
    _add_judge_qualification_dir_arg(judge_qualification_judge)
    judge_qualification_judge.add_argument(
        "--dimension",
        default="all",
        choices=(*DIMENSION_TO_FILE, "all"),
    )
    judge_qualification_judge.add_argument(
        "-w", "--workers", type=int, default=JUDGE_MAX_WORKERS
    )
    judge_qualification_judge.add_argument(
        "--judge-model-workers", type=int, default=None
    )
    judge_qualification_judge.add_argument(
        "--temperature", type=float, default=JUDGE_TEMPERATURE
    )
    judge_qualification_judge.add_argument("--overwrite", action="store_true")
    judge_qualification_judge.add_argument("--max-retries", type=int, default=3)
    judge_qualification_judge.add_argument("--retry-delay", type=float, default=2.0)

    judge_qualification_report = judge_qualification_sub.add_parser(
        "report",
        help="Compute judge-qualification reliability and sensitivity stats",
    )
    _add_judge_qualification_dir_arg(judge_qualification_report)
    judge_qualification_report.add_argument("--corpus", type=Path, default=None)

    judge_qualification_cost = judge_qualification_sub.add_parser(
        "cost",
        help="Estimate LLM cost for rendered judge-qualification inputs",
    )
    _add_judge_qualification_dir_arg(judge_qualification_cost)
    judge_qualification_cost.add_argument("--models", default=None)
    judge_qualification_cost.add_argument("--all-models", action="store_true")

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


def _judge_qualification_dir(output_dir: str | None) -> Path:
    from experiments.student_sim_stability.judge_qualification.render import (
        DEFAULT_GATE_RESULTS_DIR,
    )

    out = Path(output_dir) if output_dir else DEFAULT_GATE_RESULTS_DIR
    if not out.is_absolute():
        out = BENCH_ROOT / out
    return out


@dataclass
class LoadedQualificationStats:
    stats: dict
    stats_path: Path
    gate_dir: Path


def _load_judge_qualification_stats(gate_dir: Path) -> LoadedQualificationStats:
    stats_path = gate_dir / "report" / "judge_qualification_stats.json"
    if not stats_path.exists():
        raise FileNotFoundError(
            f"judge qualification stats not found: {stats_path}. "
            "Run `judge-qualification render`, `judge-qualification judge`, and `judge-qualification report` first."
        )
    with open(stats_path, encoding="utf-8") as fh:
        stats = json.load(fh)
    return LoadedQualificationStats(
        stats=stats, stats_path=stats_path, gate_dir=gate_dir
    )


def _write_judge_qualification_reference(
    *,
    results_dir: Path,
    loaded: LoadedQualificationStats,
) -> Path:
    report_dir = results_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "judge_qualification_reference_v1",
        "gate_dir": str(loaded.gate_dir),
        "stats_path": str(loaded.stats_path),
        "ok": bool(loaded.stats.get("ok")),
        "corpus_version": loaded.stats.get("corpus_version"),
        "counts": loaded.stats.get("counts", {}),
        "report_paths": loaded.stats.get("report_paths", {}),
    }
    path = report_dir / "judge_qualification_reference.json"
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return path


def _require_judge_qualification_ok(
    *, gate_dir: Path, results_dir: Path
) -> LoadedQualificationStats:
    """Load gate stats, enforce freshness vs current code, and require ok=true.

    Freshness guards prevent a previously-passing gate artifact from
    unblocking the full pipeline after the corpus, rubrics, or persona
    contracts have changed. If any version imprint drifts, the user must
    rerun `judge-qualification render + judge + report` against the current
    code before the full pipeline is allowed to proceed.
    """
    from experiments.student_sim_stability.core.contracts import CONTRACT_VERSION
    from experiments.student_sim_stability.core.rubrics import RUBRIC_VERSION
    from experiments.student_sim_stability.judge_qualification.render import load_corpus

    loaded = _load_judge_qualification_stats(gate_dir)
    _write_judge_qualification_reference(results_dir=results_dir, loaded=loaded)
    stats = loaded.stats
    current_corpus_version = load_corpus().get("version")
    version_mismatches: list[str] = []
    if stats.get("corpus_version") != current_corpus_version:
        version_mismatches.append(
            f"corpus_version={stats.get('corpus_version')!r} "
            f"(current={current_corpus_version!r})"
        )
    if stats.get("rubric_version") != RUBRIC_VERSION:
        version_mismatches.append(
            f"rubric_version={stats.get('rubric_version')!r} "
            f"(current={RUBRIC_VERSION!r})"
        )
    if stats.get("contract_version") != CONTRACT_VERSION:
        version_mismatches.append(
            f"contract_version={stats.get('contract_version')!r} "
            f"(current={CONTRACT_VERSION!r})"
        )
    if version_mismatches:
        raise RuntimeError(
            f"judge qualification stats in {gate_dir} are stale relative to current code: "
            + "; ".join(version_mismatches)
            + ". Rerun `judge-qualification render --clean + judge --all-models + report`."
        )
    if not stats.get("ok"):
        raise RuntimeError(
            f"judge qualification failed in {gate_dir}; fix the gate before running the full pipeline"
        )
    return loaded


def _reference_judge_qualification_if_available(
    *,
    gate_dir: Path,
    results_dir: Path,
) -> LoadedQualificationStats | None:
    try:
        loaded = _load_judge_qualification_stats(gate_dir)
    except FileNotFoundError:
        return None
    _write_judge_qualification_reference(results_dir=results_dir, loaded=loaded)
    return loaded


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
        render_p1,
    )

    results_dir = _results_dir(args.output_dir)
    snapshot_static_artifacts(results_dir)
    conv_dir = results_dir / "conversations"
    judge_input_dir = results_dir / "judge_inputs"

    if args.clean:
        removed = clean_rendered_prompts(judge_input_dir, args.dimension)
        print(f"clean: removed {removed} old prompts")

    if args.dimension in ("S1", "all"):
        render_d1(conv_dir, judge_input_dir, sample_policy=args.s1_sample_policy)
    if args.dimension in ("S3", "all"):
        render_d2(conv_dir, judge_input_dir)
    if args.dimension in ("S2", "all"):
        render_d3(conv_dir, judge_input_dir)
    if args.dimension in ("S6", "all"):
        render_control(conv_dir, judge_input_dir)
    if args.dimension in ("S5", "all"):
        render_p1(results_dir, judge_input_dir)
    if args.dimension in ("S4", "all"):
        render_b1(conv_dir, judge_input_dir)


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
    """End-to-end student-sim-stability pipeline (the ``all`` command).

    Flow:
      Step 0: Judge qualification gate (pre-validated on the fixed golden corpus)
      Step 1: Run targeted persona probes (S5 input generation)
      Step 2: Generate live + control conversations
      Step 3: Render judge prompts for S1/S2/S3/S4/S5/S6
              (S4 reads live conversations; tutor + fixture opening stripped,
              only student-generated turns reach the S4 judge.)
      Step 4: Run judge panel across all dimensions in a single pass
      Step 5: Aggregate (primary + 5-view multi-judge)
      Step 6: Data-quality audit
      Step 7: Report + validation
    """
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
        JUDGE_MODELS,
        MAX_WORKERS,
        S1_SAMPLE_POLICY,
    )
    from experiments.student_sim_stability.pipeline.aggregate import aggregate
    from experiments.student_sim_stability.pipeline.aggregate_multi_judge import (
        aggregate_multi_judge,
    )
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
        render_p1,
    )
    from experiments.student_sim_stability.pipeline.runner import ExperimentRunner
    from server.config.llm_config import require_openrouter_api_key

    try:
        require_openrouter_api_key(purpose="student-sim-stability full pipeline")
    except RuntimeError as exc:
        print(f"ERROR: {exc}")
        return 1

    results_dir = _results_dir(args.output_dir)
    try:
        judge_qualification = _require_judge_qualification_ok(
            gate_dir=_judge_qualification_dir(args.judge_qualification_dir),
            results_dir=results_dir,
        )
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"ERROR: {exc}")
        return 1

    print(
        "\n=== Step 0/7: Judge qualification gate passed ===\n"
        + json.dumps(
            {
                "gate_dir": str(judge_qualification.gate_dir),
                "stats_path": str(judge_qualification.stats_path),
                "corpus_version": judge_qualification.stats.get("corpus_version"),
                "counts": judge_qualification.stats.get("counts", {}),
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    snapshot_static_artifacts(results_dir)
    conv_dir = results_dir / "conversations"
    judge_input_dir = results_dir / "judge_inputs"
    judge_output_dir = results_dir / "judge_outputs"
    by_model_dir = results_dir / "judge_outputs_by_model"
    eval_path = results_dir / "evaluations" / "all_evaluations.json"

    print("\n=== Step 1/7: Targeted persona probes (S5 input) ===")
    probe_workers = args.workers or MAX_WORKERS
    probe_manifest = run_probes(results_dir, workers=probe_workers)
    print(json.dumps(probe_manifest, indent=2, ensure_ascii=False))

    print("\n=== Step 2/7: Generate live + control conversations ===")
    runner = ExperimentRunner(output_dir=args.output_dir)
    generate_stats = runner.run_generate(max_workers=probe_workers)
    print(json.dumps(generate_stats, indent=2, ensure_ascii=False))
    if generate_stats["failed"]:
        print(f"ERROR: {generate_stats['failed']} conversation trials failed")
        return 1

    print("\n=== Step 3/7: Render judge prompts (S1/S2/S3/S4/S5/S6) ===")
    for dimension in _ALL_JUDGE_DIMENSIONS:
        clean_rendered_prompts(judge_input_dir, dimension)
        clean_judge_outputs(judge_output_dir, dimension)
        for judge_model in JUDGE_MODELS:
            clean_judge_outputs(
                by_model_dir / safe_model_dir(judge_model),
                dimension,
            )
    render_d1(conv_dir, judge_input_dir, sample_policy=S1_SAMPLE_POLICY)
    render_d2(conv_dir, judge_input_dir)
    render_d3(conv_dir, judge_input_dir)
    render_control(conv_dir, judge_input_dir)
    render_p1(results_dir, judge_input_dir)
    render_b1(conv_dir, judge_input_dir)
    # Pre-seed the human-alignment manifest so labelers can start before the
    # judge panel finishes.
    init_human_alignment(results_dir)

    print("\n=== Step 4/7: Run configured judge panel (all dimensions) ===")
    judge_stats = run_judge_for_models(
        input_dir=judge_input_dir,
        primary_output_dir=judge_output_dir,
        by_model_output_dir=by_model_dir,
        dimension="all",
        workers=args.judge_workers,
        model_workers=args.judge_model_workers,
        require_inputs=True,
        clean_stale=True,
    )
    print(json.dumps(judge_stats, indent=2))
    if judge_stats["failed"]:
        print(f"ERROR: {judge_stats['failed']} judge calls failed")
        return 1

    print("\n=== Step 4b/7: Compute multi-judge agreement ===")
    agreement = compute_judge_agreement(results_dir)
    print(json.dumps(agreement, indent=2, ensure_ascii=False))

    print("\n=== Step 5/7: Aggregate primary + 5-view multi-judge ===")
    aggregate_summary = aggregate(
        judge_input_dir=judge_input_dir,
        judge_output_dir=judge_output_dir,
        output_path=eval_path,
        strict=True,
        profile="full",
    )
    print(json.dumps(aggregate_summary, indent=2))
    mj_summary = aggregate_multi_judge(results_dir)
    print(json.dumps(mj_summary, indent=2, ensure_ascii=False))
    init_human_alignment(results_dir)

    print("\n=== Step 6/7: Data quality audit ===")
    audit = run_audit(results_dir, profile="full")
    print(json.dumps({"ok": audit["ok"]}, indent=2))
    if not audit["ok"]:
        print("ERROR: data quality audit failed")
        return 1

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

    if args.command == "aggregate-multi-judge":
        from experiments.student_sim_stability.pipeline.aggregate_multi_judge import (
            aggregate_multi_judge,
        )

        summary = aggregate_multi_judge(
            _results_dir(args.output_dir), strict=args.strict
        )
        print(json.dumps(summary, indent=2, ensure_ascii=False))
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
        _reference_judge_qualification_if_available(
            gate_dir=_judge_qualification_dir(args.judge_qualification_dir),
            results_dir=results_dir,
        )
        checks, _ = validate(results_dir, profile=args.profile, require_audit=False)
        failures = [check for check in checks if not check.ok and check.required]
        if failures:
            if args.skip_validate:
                print(
                    "WARNING: Generating report against incomplete artifacts (--skip-validate):"
                )
                for check in failures[:10]:
                    print(f"  - {check.name}: {check.message}")
            else:
                print("ERROR: Cannot generate report from incomplete artifacts:")
                for check in failures[:10]:
                    print(f"  - {check.name}: {check.message}")
                return 1
        gen = ReportGenerator(str(eval_path), str(results_dir / "report"))
        report_path = gen.generate()
        print(f"Report: {report_path}")
        return 0

    if args.command == "paper-export":
        results_dir = _results_dir(args.output_dir)
        source = args.source or (results_dir / "report" / "components")
        if not source.exists():
            print(
                f"ERROR: components directory not found at {source}. "
                "Run `cli report` first."
            )
            return 1
        manifest = _paper_export(args.target, source)
        print(
            f"Exported {manifest['n_assets']} assets to {args.target} "
            f"({manifest['by_kind']}). Manifest: {Path(args.target) / 'manifest.json'}"
        )
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

    if args.command == "human-alignment-extend":
        from experiments.student_sim_stability.analysis.human_alignment import (
            extend_alignment_pool,
        )

        try:
            report = extend_alignment_pool(
                _results_dir(args.output_dir),
                target=args.target,
                key_fields=args.key_fields,
                dimension=args.dimension,
                seed=args.seed,
                dry_run=args.dry_run,
            )
        except (FileNotFoundError, ValueError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.command == "judge-agreement":
        from experiments.student_sim_stability.analysis.judge_agreement import (
            compute_judge_agreement,
        )

        report = compute_judge_agreement(_results_dir(args.output_dir))
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    if args.command == "judge-qualification":
        from experiments.student_sim_stability.judge_qualification.render import (
            parse_prompt_variants,
            render_judge_qualification_inputs,
        )

        gate_dir = _judge_qualification_dir(args.judge_qualification_dir)
        if args.judge_qualification_command == "render":
            variants = (
                parse_prompt_variants(args.prompt_variants)
                if args.prompt_variants
                else None
            )
            manifest = render_judge_qualification_inputs(
                gate_dir=gate_dir,
                corpus_path=args.corpus,
                repeats=args.repeats,
                prompt_variants=variants,
                clean=args.clean,
            )
            print(json.dumps(manifest, indent=2, ensure_ascii=False))
            return 0

        if args.judge_qualification_command == "judge":
            from experiments.student_sim_stability.judge_qualification.report import (
                write_judge_qualification_report,
            )
            from experiments.student_sim_stability.pipeline.judge import (
                run_judge_for_models,
            )

            # The qualification gate always runs the full JUDGE_MODELS panel.
            # Single-judge qualification would silently bless a 3-judge
            # experiment with only one model validated, defeating the gate's
            # purpose.
            stats = run_judge_for_models(
                input_dir=gate_dir / "judge_inputs",
                primary_output_dir=gate_dir / "judge_outputs",
                by_model_output_dir=gate_dir / "judge_outputs_by_model",
                dimension=args.dimension,
                workers=args.workers,
                temperature=args.temperature,
                overwrite=args.overwrite,
                max_retries=args.max_retries,
                retry_delay=args.retry_delay,
                require_inputs=True,
                clean_stale=args.overwrite,
                model_workers=args.judge_model_workers,
            )
            report_dir = gate_dir / "report"
            report_dir.mkdir(parents=True, exist_ok=True)
            with open(report_dir / "judge_run_stats.json", "w", encoding="utf-8") as fh:
                json.dump(stats, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            report_stats = write_judge_qualification_report(gate_dir=gate_dir)
            stats["report"] = {
                "ok": report_stats.get("ok"),
                "paths": report_stats.get("report_paths", {}),
            }
            print(json.dumps(stats, indent=2, ensure_ascii=False))
            return 1 if stats["failed"] or not report_stats.get("ok") else 0

        if args.judge_qualification_command == "report":
            from experiments.student_sim_stability.judge_qualification.report import (
                write_judge_qualification_report,
            )

            stats = write_judge_qualification_report(
                gate_dir=gate_dir,
                corpus_path=args.corpus,
            )
            print(json.dumps(stats, indent=2, ensure_ascii=False))
            return 0 if stats["ok"] else 1

        if args.judge_qualification_command == "cost":
            from experiments.student_sim_stability.judge_qualification.cost import (
                _parse_models as parse_cost_models,
            )
            from experiments.student_sim_stability.judge_qualification.cost import (
                estimate_judge_qualification_cost,
            )

            estimate = estimate_judge_qualification_cost(
                gate_dir=gate_dir,
                models=parse_cost_models(args.models, all_models=args.all_models),
            )
            print(json.dumps(estimate, indent=2, ensure_ascii=False))
            return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
