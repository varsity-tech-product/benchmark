#!/usr/bin/env python3
"""Stage 1 judge reliability validation runner."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from experiments.judge_validation.report import (  # noqa: E402
    compute_reliability_stats,
    write_reports,
)
from server.eval.inputs.rubric_builder import (  # noqa: E402
    build_eval_params,
    build_rubric_metadata,
    build_rubric_text,
    get_max_score,
    load_6d_rubric,
    load_rubric,
)
from server.eval.judges.runtime.call_policy import llm_call_with_retry  # noqa: E402
from server.eval.judges.runtime.conv_geval import (  # noqa: E402
    EvalTestCase,
    EwanConvGEval,
    Turn,
    format_turns,
)
from server.eval.judges.runtime.model_resolver import resolve_ewan_model  # noqa: E402

DEFAULT_CORPUS = Path(__file__).resolve().parent / "pilot_corpus.json"
DEFAULT_OUTPUT_DIR = Path("experiments/judge_validation/results")
DEFAULT_REPEAT_COUNT = 3


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else BENCH_ROOT / p


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic_write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        tmp = Path(fh.name)
    tmp.replace(path)


def _conversation_context(item: dict[str, Any]) -> str:
    if item.get("context"):
        return str(item["context"])
    turns = [
        Turn(role=str(turn.get("role", "")), content=str(turn.get("content", "")))
        for turn in item.get("conversation", [])
    ]
    return format_turns(turns)


def _context_fields(item: dict[str, Any]) -> list[str]:
    return ["context"] if item.get("context") else ["conversation"]


def _rules_text(rules: list[str] | str) -> str:
    if isinstance(rules, list):
        return "\n".join(f"- {rule}" for rule in rules)
    return str(rules)


def _metric_for_item(item: dict[str, Any], *, model: str) -> EwanConvGEval:
    track = item["track"]
    dimension = item["dimension"]
    category = item.get("category")
    persona_id = item.get("persona_id", "double_novice")
    transcript_source = item.get("transcript_source", "pilot_corpus")
    context_fields = _context_fields(item)
    model_obj = resolve_ewan_model(model)

    if track == "tutor":
        rubric = load_6d_rubric()
        criteria = build_rubric_text(rubric, dimension, persona_id, category)
        metadata = build_rubric_metadata(
            rubric,
            dimension,
            rubric_name="tutor_6d",
            context_fields=context_fields,
            transcript_source=transcript_source,
            extra={"registry_rubric_id": item.get("registry_rubric_id")},
        )
        return EwanConvGEval(
            name=dimension,
            criteria=criteria,
            role=rubric.get("role", ""),
            rules=_rules_text(rubric.get("rules", [])),
            model=model_obj,
            max_score=get_max_score(rubric, dimension),
            rubric_metadata=metadata,
        )

    if track in {"qp", "qr"}:
        rubric = load_rubric(track)
        params = build_eval_params(
            rubric,
            dimension,
            rubric_name=track,
            context_fields=context_fields,
            transcript_source=transcript_source,
        )
        params["rubric_metadata"]["registry_rubric_id"] = item.get(
            "registry_rubric_id"
        )
        return EwanConvGEval(
            name=dimension,
            model=model_obj,
            **params,
        )

    raise ValueError(f"Unsupported judge track: {track}")


def _raw_from_normalized(score: float, metadata: dict[str, Any]) -> int:
    scale = metadata.get("score_scale") or {}
    lo = int(scale.get("min", 1))
    hi = int(scale.get("max", 5))
    return max(lo, min(hi, int(round(float(score) * (hi - lo) + lo))))


def _record_base(
    *,
    run_id: str,
    item: dict[str, Any],
    run_index: int,
    metric: EwanConvGEval,
) -> dict[str, Any]:
    metadata = metric.judge_metadata()
    metadata["run_timestamp"] = datetime.now(timezone.utc).isoformat()
    return {
        "run_id": run_id,
        "sample_id": item["sample_id"],
        "pair_id": item.get("pair_id"),
        "pair_role": item.get("pair_role"),
        "task_id": item.get("task_id"),
        "category": item.get("category"),
        "persona_id": item.get("persona_id"),
        "track": item.get("track"),
        "dimension": item.get("dimension"),
        "registry_rubric_id": item.get("registry_rubric_id"),
        "transcript_source": item.get("transcript_source"),
        "run_index": run_index,
        "judge_model": metadata.get("judge_model"),
        "judge_metadata": metadata,
    }


def _render(args: argparse.Namespace) -> int:
    corpus = _load_json(_resolve(args.corpus))
    output_dir = _resolve(args.output_dir)
    run_id = args.run_id or (
        f"jv_render_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    prompts: list[dict[str, Any]] = []
    for item in corpus.get("items", []):
        context = _conversation_context(item)
        test_case = EvalTestCase(context=context)
        metric = _metric_for_item(item, model=args.model)
        for run_index in range(args.repeats):
            prompts.append(
                {
                    **_record_base(
                        run_id=run_id,
                        item=item,
                        run_index=run_index,
                        metric=metric,
                    ),
                    "prompt": metric.render_prompt(test_case),
                }
            )

    payload = {
        "version": "judge_validation_prompts_v1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repeat_count": args.repeats,
        "counts": {"prompts": len(prompts)},
        "prompts": prompts,
    }
    output_path = output_dir / "judge_inputs.json"
    _atomic_write_json(output_path, payload)
    print(
        json.dumps(
            {"judge_inputs": str(output_path), "prompts": len(prompts)},
            indent=2,
        )
    )
    return 0


async def _judge_one(
    *,
    run_id: str,
    item: dict[str, Any],
    run_index: int,
    model: str,
) -> dict[str, Any]:
    metric = _metric_for_item(item, model=model)
    base = _record_base(
        run_id=run_id,
        item=item,
        run_index=run_index,
        metric=metric,
    )
    start = time.time()
    try:
        result = await llm_call_with_retry(
            lambda: _metric_for_item(item, model=model),
            EvalTestCase(context=_conversation_context(item)),
            dimension_name=str(item.get("dimension", "")),
        )
        metadata = result.get("judge_metadata") or metric.judge_metadata()
        if result.get("score") is None:
            base["judge_metadata"] = metadata
            if result.get("diagnostics"):
                base["diagnostics"] = result["diagnostics"]
            return {
                **base,
                "status": "failed",
                "score": None,
                "raw_score": None,
                "reason": result.get("error") or result.get("reason", ""),
                "evidence": result.get("evidence", []),
                "duration_seconds": round(time.time() - start, 3),
            }
        base["judge_metadata"] = metadata
        return {
            **base,
            "status": "success",
            "score": round(float(result["score"]), 4),
            "raw_score": _raw_from_normalized(float(result["score"]), metadata),
            "reason": result.get("reason", ""),
            "evidence": list(result.get("evidence", []) or []),
            "duration_seconds": round(time.time() - start, 3),
        }
    except Exception as exc:  # noqa: BLE001 - persisted as judge validation data
        return {
            **base,
            "status": "failed",
            "score": None,
            "raw_score": None,
            "reason": str(exc),
            "evidence": [],
            "duration_seconds": round(time.time() - start, 3),
        }


def _judge(args: argparse.Namespace) -> int:
    corpus = _load_json(_resolve(args.corpus))
    output_dir = _resolve(args.output_dir)
    run_id = args.run_id or (
        f"jv_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    tasks = [
        _judge_one(
            run_id=run_id,
            item=item,
            run_index=run_index,
            model=args.model,
        )
        for item in corpus.get("items", [])
        for run_index in range(args.repeats)
    ]

    async def _run_all() -> list[dict[str, Any]]:
        sem = asyncio.Semaphore(max(1, args.workers))

        async def _guarded(coro):
            async with sem:
                return await coro

        return await asyncio.gather(*[_guarded(task) for task in tasks])

    records = asyncio.run(_run_all())
    payload = {
        "version": "judge_validation_runs_v1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "judge_model": args.model,
        "repeat_count": args.repeats,
        "counts": {
            "records": len(records),
            "success": sum(1 for record in records if record["status"] == "success"),
            "failed": sum(1 for record in records if record["status"] == "failed"),
        },
        "records": records,
    }
    output_path = output_dir / "judge_runs.json"
    _atomic_write_json(output_path, payload)
    print(
        json.dumps(
            {"judge_runs": str(output_path), "counts": payload["counts"]},
            indent=2,
        )
    )
    return 1 if payload["counts"]["failed"] else 0


def _report(args: argparse.Namespace) -> int:
    corpus = _load_json(_resolve(args.corpus))
    runs = _load_json(_resolve(args.runs))
    records = runs.get("records", [])
    stats = compute_reliability_stats(corpus=corpus, records=records)
    output_dir = _resolve(args.output_dir)
    paths = write_reports(
        stats=stats,
        run_id=str(runs.get("run_id", "")),
        output_dir=output_dir,
    )
    print(
        json.dumps(
            {
                "run_id": runs.get("run_id"),
                "stats": paths["stats"],
                "markdown": paths["markdown"],
                "html": paths["html"],
                "stability": stats["stability"],
                "adversarial": {
                    "ranking_pass_rate": stats["adversarial"]["ranking_pass_rate"]
                },
            },
            indent=2,
        )
    )
    return 0


def _dry_run(args: argparse.Namespace) -> int:
    corpus = _load_json(_resolve(args.corpus))
    pairs = corpus.get("adversarial_pairs", [])
    items = corpus.get("items", [])
    print(
        json.dumps(
            {
                "corpus": str(_resolve(args.corpus)),
                "items": len(items),
                "adversarial_pairs": len(pairs),
                "planned_judge_records": len(items) * args.repeats,
                "repeat_count": args.repeats,
            },
            indent=2,
        )
    )
    return 0


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Judge reliability validation")
    parser.add_argument("--corpus", default=str(DEFAULT_CORPUS))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--model", default="anthropic/claude-sonnet-4-6")
    parser.add_argument("--repeats", type=int, default=DEFAULT_REPEAT_COUNT)
    parser.add_argument("--run-id", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--corpus", default=argparse.SUPPRESS)
        p.add_argument("--output-dir", default=argparse.SUPPRESS)
        p.add_argument("--model", default=argparse.SUPPRESS)
        p.add_argument("--repeats", type=int, default=argparse.SUPPRESS)
        p.add_argument("--run-id", default=argparse.SUPPRESS)

    dry_run_p = sub.add_parser("dry-run")
    add_common(dry_run_p)

    render_p = sub.add_parser("render")
    add_common(render_p)

    judge_p = sub.add_parser("judge")
    add_common(judge_p)
    judge_p.add_argument("-w", "--workers", type=int, default=2)

    report_p = sub.add_parser("report")
    add_common(report_p)
    report_p.add_argument(
        "--runs",
        default=str(DEFAULT_OUTPUT_DIR / "judge_runs.json"),
        help="Path to judge_runs.json",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    if args.command == "dry-run":
        return _dry_run(args)
    if args.command == "render":
        return _render(args)
    if args.command == "judge":
        return _judge(args)
    if args.command == "report":
        return _report(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
