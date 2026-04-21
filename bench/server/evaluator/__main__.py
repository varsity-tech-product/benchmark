"""CLI entrypoint for the offline evaluator.

Usage::

    python -m server.evaluator --bundle /path/to/bundle/dir
    python -m server.evaluator --bundle /path/to/bundle --eval-mode qr_only
    python -m server.evaluator --bundle /path/to/bundle --bench-root /path/to/bench

Scores a single bundle and writes the result tree under
``{bench_root}/evaluations/server/{task_id}/{persona_id}/{sid8}/{eval_run_id}/``.

Batch invocation (``--all-pending``, concurrency, rate-limit pooling) is
issue #47 and lands on top of this single-bundle driver.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from server.evaluator.single import score_bundle
from server.storage.bundle import load_bundle


def _resolve_bench_root(arg_value: str | None) -> Path:
    if arg_value:
        return Path(arg_value).resolve()
    # Walk up from this file: bench/server/evaluator/__main__.py → bench/
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m server.evaluator")
    parser.add_argument(
        "--bundle",
        required=True,
        help="Bundle directory written by save_run_state (must contain manifest.json).",
    )
    parser.add_argument(
        "--bench-root",
        default=None,
        help="Repo root (defaults to the bench/ this module ships in).",
    )
    parser.add_argument(
        "--eval-model",
        default="anthropic/claude-sonnet-4-6",
        help="Judge LLM identifier passed to the pipeline.",
    )
    parser.add_argument(
        "--eval-mode",
        default="full",
        choices=["full", "qr_only", "qp_only", "tutor_only"],
    )
    parser.add_argument(
        "--tutor-dim",
        action="append",
        default=None,
        help="Restrict tutor scoring to one dimension; repeat for several.",
    )
    parser.add_argument(
        "--eval-run-id",
        default=None,
        help="Override the auto-generated eval_run_id (use for reruns).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable INFO logging from the evaluator + pipeline.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    bench_root = _resolve_bench_root(args.bench_root)
    bundle = load_bundle(args.bundle)
    task = _load_task(bench_root, bundle.task_id)
    persona = _load_persona(bench_root, bundle.persona_id)

    results = score_bundle(
        bundle_dir=bundle.bundle_dir,
        task=task,
        persona=persona,
        bench_root=bench_root,
        eval_model=args.eval_model,
        eval_mode=args.eval_mode,
        tutor_dims=args.tutor_dim,
        eval_run_id=args.eval_run_id,
    )

    out_dir = results.get("_eval_run_dir")
    print(f"scored: {bundle.session_id[:8]} → {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
