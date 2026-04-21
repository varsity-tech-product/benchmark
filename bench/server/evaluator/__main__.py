"""CLI entrypoint for the offline evaluator.

Single-bundle mode::

    python -m server.evaluator --bundle /path/to/bundle

Batch mode (issue #47)::

    python -m server.evaluator --all-pending --concurrency 8
    python -m server.evaluator --session <sid> --force
    python -m server.evaluator --task-id I01 --persona fullstack_practitioner
    python -m server.evaluator --bundles-from bundles.txt --dry-run

Batch mode writes per-bundle outputs to the sibling tree and a
campaign-level manifest at
``{bench_root}/evaluations/campaigns/{campaign_id}/summary.json``.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Callable

from server.evaluator.batch import (
    BundleOutcome,
    resolve_bundles,
    run_campaign,
)
from server.evaluator.single import score_bundle
from server.storage.bundle import load_bundle


def _resolve_bench_root(arg_value: str | None) -> Path:
    if arg_value:
        return Path(arg_value).resolve()
    return Path(__file__).resolve().parents[2]


def _load_task(bench_root: Path, task_id: str):
    from server.schemas import QuantTutorTask

    for json_path in (bench_root / "tasks").rglob(f"{task_id}.json"):
        return QuantTutorTask(**json.loads(json_path.read_text(encoding="utf-8")))
    raise SystemExit(f"task {task_id!r} not found under {bench_root / 'tasks'}")


def _load_persona(bench_root: Path, persona_id: str):
    from server.schemas import StudentPersona

    for json_path in (bench_root / "personas").rglob(f"{persona_id}.json"):
        return StudentPersona(**json.loads(json_path.read_text(encoding="utf-8")))
    raise SystemExit(
        f"persona {persona_id!r} not found under {bench_root / 'personas'}"
    )


def _memoize(fn: Callable):
    cache: dict = {}

    def wrapped(key):
        if key not in cache:
            cache[key] = fn(key)
        return cache[key]

    return wrapped


def _make_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m server.evaluator")
    p.add_argument("--bench-root", default=None, help="Repo root (defaults to bench/).")
    p.add_argument(
        "--eval-model",
        "--judge",
        dest="eval_model",
        default="anthropic/claude-sonnet-4-6",
        help="Judge LLM identifier.",
    )
    p.add_argument(
        "--eval-mode",
        default="full",
        choices=["full", "qr_only", "qp_only", "tutor_only"],
    )
    p.add_argument(
        "--tutor-dim",
        action="append",
        default=None,
        help="Restrict tutor scoring to one dimension; repeat for several.",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Enable INFO logging."
    )

    # Selectors (at least one required)
    p.add_argument("--bundle", default=None, help="Single bundle directory (legacy single-bundle mode).")
    p.add_argument(
        "--session",
        action="append",
        default=None,
        help="Target session id(s). Repeat for several.",
    )
    p.add_argument(
        "--task-id",
        action="append",
        default=None,
        help="Filter bundles by task id. Repeat for several.",
    )
    p.add_argument(
        "--persona",
        action="append",
        default=None,
        help="Filter bundles by persona id. Repeat for several.",
    )
    p.add_argument(
        "--bundles-from",
        default=None,
        help="File with one bundle path per line.",
    )
    p.add_argument(
        "--all-pending",
        action="store_true",
        help="Score every bundle that lacks a sibling eval run with the same config.",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Score every bundle under results/server (combine with --force to redo).",
    )

    # Single-mode extra knob
    p.add_argument(
        "--eval-run-id",
        default=None,
        help="Override the auto-generated eval_run_id (single-bundle mode).",
    )

    # Batch controls
    p.add_argument("--concurrency", type=int, default=4, help="Parallel workers.")
    p.add_argument(
        "--rubric-version", default="", help="Rubric version tag (stamped into config_hash)."
    )
    p.add_argument(
        "--formula-version",
        default="",
        help="OAS formula version tag (stamped into config_hash).",
    )
    p.add_argument(
        "--skip-scored",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Skip bundles already scored with the same config (default on).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Re-score bundles even if a matching eval already exists.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve + print planned work; do not score.",
    )
    return p


def _run_single(args, bench_root: Path) -> int:
    bundle = load_bundle(args.bundle)
    task = _load_task(bench_root, bundle.task_id)
    persona = _load_persona(bench_root, bundle.persona_id)

    result = score_bundle(
        bundle_dir=bundle.bundle_dir,
        task=task,
        persona=persona,
        bench_root=bench_root,
        eval_model=args.eval_model,
        eval_mode=args.eval_mode,
        tutor_dims=args.tutor_dim,
        eval_run_id=args.eval_run_id,
    )
    print(f"scored: {bundle.session_id[:8]} → {result.get('_eval_run_dir')}")
    return 0


def _read_bundle_list(path: str) -> list[Path]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            out.append(Path(s))
    return out


def _run_batch(args, bench_root: Path) -> int:
    explicit_paths = (
        _read_bundle_list(args.bundles_from) if args.bundles_from else None
    )
    bundles = resolve_bundles(
        bench_root,
        session_ids=args.session,
        task_ids=args.task_id,
        persona_ids=args.persona,
        bundle_paths=explicit_paths,
        all_bundles=args.all_pending or args.all,
    )
    if not bundles:
        print("No bundles matched the given filters.")
        return 0

    print(f"Resolved {len(bundles)} bundles.")

    def _progress(outcome: BundleOutcome, done: int, total: int) -> None:
        tag = outcome.status.upper()
        sid = (outcome.session_id or "?")[:8]
        msg = f"[{done}/{total}] {tag} {outcome.task_id}/{outcome.persona_id}/{sid}"
        if outcome.overall_score is not None:
            msg += f" OAS={outcome.overall_score}"
        if outcome.error:
            msg += f" ERR={outcome.error}"
        print(msg)

    summary = run_campaign(
        bench_root=bench_root,
        bundles=bundles,
        task_loader=_memoize(lambda tid: _load_task(bench_root, tid)),
        persona_loader=_memoize(lambda pid: _load_persona(bench_root, pid)),
        eval_model=args.eval_model,
        eval_mode=args.eval_mode,
        tutor_dims=args.tutor_dim,
        concurrency=args.concurrency,
        skip_scored=args.skip_scored,
        force=args.force,
        rubric_version=args.rubric_version,
        formula_version=args.formula_version,
        dry_run=args.dry_run,
        on_progress=_progress,
    )

    totals = summary.totals
    print(
        f"\nCampaign {summary.campaign_id}: "
        f"scored={totals['scored']} skipped={totals['skipped']} "
        f"failed={totals['failed']} pending={totals['pending']} "
        f"total={totals['total']} duration={summary.duration_s}s"
    )
    if not args.dry_run:
        path = (
            bench_root / "evaluations" / "campaigns"
            / summary.campaign_id / "summary.json"
        )
        print(f"Summary: {path}")
    return 1 if totals["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    args = _make_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    bench_root = _resolve_bench_root(args.bench_root)

    if args.bundle:
        return _run_single(args, bench_root)

    if not (
        args.all_pending
        or args.all
        or args.session
        or args.task_id
        or args.persona
        or args.bundles_from
    ):
        print(
            "error: pick a selector — --bundle, --all-pending, --all, "
            "--session, --task-id, --persona, or --bundles-from.",
            file=sys.stderr,
        )
        return 2

    return _run_batch(args, bench_root)


if __name__ == "__main__":
    sys.exit(main())
