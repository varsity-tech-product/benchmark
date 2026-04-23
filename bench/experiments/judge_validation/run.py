#!/usr/bin/env python3
"""Judge reliability validation runner."""

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

from experiments.judge_validation.human_alignment import (  # noqa: E402
    convert_csv_to_human_labels,
    compute_human_alignment_stats,
    load_human_labels,
    load_sample_id_map,
    write_human_alignment_reports,
)
from experiments.judge_validation.report import (  # noqa: E402
    compute_reliability_stats,
    write_reports,
)
from experiments.judge_validation.review_packet import (  # noqa: E402
    build_review_packet,
    write_review_packet,
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
DEFAULT_HUMAN_LABELS = Path(__file__).resolve().parent / "human_labels.json"
DEFAULT_RUBRIC_REGISTRY = BENCH_ROOT / "server/eval/rubrics/rubric_registry.json"
DEFAULT_OUTPUT_DIR = Path("experiments/judge_validation/results")
DEFAULT_REVIEW_PACKET_DIR = DEFAULT_OUTPUT_DIR / "human_review_packet"
DEFAULT_REPEAT_COUNT = 3
DEFAULT_PROMPT_VARIANT = "baseline"
SUPPORTED_PROMPT_VARIANTS = {
    "baseline",
    "role_blocks",
    "markdown_transcript",
}


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


def _split_csv(value: str | list[str] | tuple[str, ...]) -> list[str]:
    if isinstance(value, (list, tuple)):
        parts = [str(part).strip() for part in value]
    else:
        parts = [part.strip() for part in str(value).split(",")]
    return [part for part in parts if part]


def _prompt_variants(value: str | list[str] | tuple[str, ...]) -> list[str]:
    variants = _split_csv(value)
    unknown = sorted(set(variants) - SUPPORTED_PROMPT_VARIANTS)
    if unknown:
        known = ", ".join(sorted(SUPPORTED_PROMPT_VARIANTS))
        raise ValueError(f"Unsupported prompt variants {unknown}; choose from {known}")
    return variants or [DEFAULT_PROMPT_VARIANT]


def _prompt_variants_for_item(
    item: dict[str, Any],
    requested_variants: list[str],
) -> list[str]:
    if item.get("context"):
        return [DEFAULT_PROMPT_VARIANT]
    return list(requested_variants)


def _turns(item: dict[str, Any]) -> list[Turn]:
    return [
        Turn(role=str(turn.get("role", "")), content=str(turn.get("content", "")))
        for turn in item.get("conversation", [])
    ]


def _format_turns_role_blocks(turns: list[Turn]) -> str:
    blocks = []
    for index, turn in enumerate(turns, start=1):
        role = turn.role.title() if turn.role else "Unknown"
        blocks.append(f"Turn {index} - {role}\n{turn.content}")
    return "\n\n".join(blocks)


def _format_turns_markdown(turns: list[Turn]) -> str:
    blocks = []
    for index, turn in enumerate(turns, start=1):
        role = turn.role.title() if turn.role else "Unknown"
        blocks.append(f"### Turn {index}: {role}\n\n{turn.content}")
    return "\n\n".join(blocks)


def _conversation_context(
    item: dict[str, Any],
    *,
    prompt_variant_id: str = DEFAULT_PROMPT_VARIANT,
) -> str:
    if prompt_variant_id not in SUPPORTED_PROMPT_VARIANTS:
        known = ", ".join(sorted(SUPPORTED_PROMPT_VARIANTS))
        raise ValueError(
            f"Unsupported prompt variant {prompt_variant_id}; choose from {known}"
        )

    turns = _turns(item)

    if item.get("context"):
        return str(item["context"])

    if prompt_variant_id == "role_blocks":
        return _format_turns_role_blocks(turns)
    if prompt_variant_id == "markdown_transcript":
        return _format_turns_markdown(turns)
    return format_turns(turns)


def _context_fields(item: dict[str, Any]) -> list[str]:
    return ["context"] if item.get("context") else ["conversation"]


def _rules_text(rules: list[str] | str) -> str:
    if isinstance(rules, list):
        return "\n".join(f"- {rule}" for rule in rules)
    return str(rules)


def _metric_for_item(
    item: dict[str, Any],
    *,
    model: str,
    prompt_variant_id: str = DEFAULT_PROMPT_VARIANT,
) -> EwanConvGEval:
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
            extra={
                "registry_rubric_id": item.get("registry_rubric_id"),
                "prompt_variant_id": prompt_variant_id,
            },
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
        params["rubric_metadata"]["prompt_variant_id"] = prompt_variant_id
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
    prompt_variant_id: str = DEFAULT_PROMPT_VARIANT,
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
        "prompt_variant_id": prompt_variant_id,
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
    prompt_variants = _prompt_variants(args.prompt_variants)
    for item in corpus.get("items", []):
        for prompt_variant_id in _prompt_variants_for_item(item, prompt_variants):
            context = _conversation_context(
                item,
                prompt_variant_id=prompt_variant_id,
            )
            test_case = EvalTestCase(context=context)
            metric = _metric_for_item(
                item,
                model=args.model,
                prompt_variant_id=prompt_variant_id,
            )
            for run_index in range(args.repeats):
                prompts.append(
                    {
                        **_record_base(
                            run_id=run_id,
                            item=item,
                            run_index=run_index,
                            metric=metric,
                            prompt_variant_id=prompt_variant_id,
                        ),
                        "prompt": metric.render_prompt(test_case),
                    }
                )

    payload = {
        "version": "judge_validation_prompts_v1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repeat_count": args.repeats,
        "prompt_variants": prompt_variants,
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
    prompt_variant_id: str = DEFAULT_PROMPT_VARIANT,
) -> dict[str, Any]:
    metric = _metric_for_item(
        item,
        model=model,
        prompt_variant_id=prompt_variant_id,
    )
    base = _record_base(
        run_id=run_id,
        item=item,
        run_index=run_index,
        metric=metric,
        prompt_variant_id=prompt_variant_id,
    )
    start = time.time()
    try:
        result = await llm_call_with_retry(
            lambda: _metric_for_item(
                item,
                model=model,
                prompt_variant_id=prompt_variant_id,
            ),
            EvalTestCase(
                context=_conversation_context(
                    item,
                    prompt_variant_id=prompt_variant_id,
                )
            ),
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
    prompt_variants = _prompt_variants(args.prompt_variants)
    tasks = [
        _judge_one(
            run_id=run_id,
            item=item,
            run_index=run_index,
            model=args.model,
            prompt_variant_id=prompt_variant_id,
        )
        for item in corpus.get("items", [])
        for prompt_variant_id in _prompt_variants_for_item(item, prompt_variants)
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
        "prompt_variants": prompt_variants,
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
                "prompt_format": {
                    "mean_absolute_variant_delta": stats["prompt_format"][
                        "mean_absolute_variant_delta"
                    ],
                    "within_one_variant_rate": stats["prompt_format"][
                        "within_one_variant_rate"
                    ],
                    "pass_fail_variant_flip_rate": stats["prompt_format"][
                        "pass_fail_variant_flip_rate"
                    ],
                },
                "adversarial": {
                    "ranking_pass_rate": stats["adversarial"]["ranking_pass_rate"]
                },
                "sensitivity": {
                    "pass_rate": stats["sensitivity"]["pass_rate"]
                },
                "evidence_consistency": {
                    "evidence_coverage_rate": stats["evidence_consistency"][
                        "evidence_coverage_rate"
                    ],
                    "reason_coverage_rate": stats["evidence_consistency"][
                        "reason_coverage_rate"
                    ],
                    "mean_pairwise_text_jaccard": stats["evidence_consistency"][
                        "mean_pairwise_text_jaccard"
                    ],
                },
            },
            indent=2,
        )
    )
    return 0


def _convert_human_labels(args: argparse.Namespace) -> int:
    csv_path = _resolve(args.csv)
    output_path = _resolve(args.labels_output)
    payload = convert_csv_to_human_labels(csv_path)
    _atomic_write_json(output_path, payload)
    print(
        json.dumps(
            {
                "human_labels": str(output_path),
                "labels": len(payload["labels"]),
            },
            indent=2,
        )
    )
    return 0


def _export_review_packet(args: argparse.Namespace) -> int:
    corpus = _load_json(_resolve(args.corpus))
    rubric_registry = _load_json(_resolve(args.rubric_registry))
    sample_ids = _split_csv(args.sample_ids) if args.sample_ids else None
    limit = args.limit if args.limit and args.limit > 0 else None
    packet_output_dir = (
        _resolve(args.packet_output_dir)
        if args.packet_output_dir
        else _resolve(args.output_dir) / "human_review_packet"
    )
    packet = build_review_packet(
        corpus=corpus,
        rubric_registry=rubric_registry,
        sample_ids=sample_ids,
        limit=limit,
    )
    paths = write_review_packet(
        packet=packet,
        output_dir=packet_output_dir,
    )
    print(
        json.dumps(
            {
                "review_packet": paths,
                "items": packet["counts"]["items"],
            },
            indent=2,
        )
    )
    return 0


def _human_alignment(args: argparse.Namespace) -> int:
    corpus = _load_json(_resolve(args.corpus))
    runs = _load_json(_resolve(args.runs))
    labels = load_human_labels(_resolve(args.labels))
    sample_id_map = (
        load_sample_id_map(_resolve(args.sample_map)) if args.sample_map else None
    )
    records = runs.get("records", [])
    stats = compute_human_alignment_stats(
        corpus=corpus,
        records=records,
        labels=labels,
        sample_id_map=sample_id_map,
    )
    output_dir = _resolve(args.output_dir)
    paths = write_human_alignment_reports(
        stats=stats,
        run_id=str(runs.get("run_id", "")),
        output_dir=output_dir,
    )
    print(
        json.dumps(
            {
                "run_id": runs.get("run_id"),
                "labels": len(labels),
                "stats": paths["stats"],
                "markdown": paths["markdown"],
                "html": paths["html"],
                "overall": stats["overall"],
                "stage3_gate": stats["stage3_gate"],
            },
            indent=2,
        )
    )
    return 0


def _dry_run(args: argparse.Namespace) -> int:
    corpus = _load_json(_resolve(args.corpus))
    pairs = corpus.get("adversarial_pairs", [])
    sensitivity_cases = corpus.get("sensitivity_cases", [])
    items = corpus.get("items", [])
    prompt_variants = _prompt_variants(args.prompt_variants)
    planned_records = sum(
        len(_prompt_variants_for_item(item, prompt_variants)) * args.repeats
        for item in items
    )
    print(
        json.dumps(
            {
                "corpus": str(_resolve(args.corpus)),
                "items": len(items),
                "adversarial_pairs": len(pairs),
                "sensitivity_cases": len(sensitivity_cases),
                "prompt_variants": prompt_variants,
                "planned_judge_records": planned_records,
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
    parser.add_argument("--prompt-variants", default=DEFAULT_PROMPT_VARIANT)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--corpus", default=argparse.SUPPRESS)
        p.add_argument("--output-dir", default=argparse.SUPPRESS)
        p.add_argument("--model", default=argparse.SUPPRESS)
        p.add_argument("--repeats", type=int, default=argparse.SUPPRESS)
        p.add_argument("--run-id", default=argparse.SUPPRESS)
        p.add_argument("--prompt-variants", default=argparse.SUPPRESS)

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

    convert_labels_p = sub.add_parser("convert-human-labels")
    convert_labels_p.add_argument(
        "--csv",
        required=True,
        help="Path to a Google Form CSV export",
    )
    convert_labels_p.add_argument(
        "--labels-output",
        default=str(DEFAULT_HUMAN_LABELS),
        help="Path for canonical human_labels.json",
    )

    review_packet_p = sub.add_parser("export-review-packet")
    add_common(review_packet_p)
    review_packet_p.add_argument(
        "--rubric-registry",
        default=str(DEFAULT_RUBRIC_REGISTRY),
        help="Path to rubric_registry.json",
    )
    review_packet_p.add_argument(
        "--packet-output-dir",
        default=None,
        help="Directory for reviewer packet JSON, Markdown, and CSV files",
    )
    review_packet_p.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of corpus items to export; 0 exports all selected items",
    )
    review_packet_p.add_argument(
        "--sample-ids",
        default="",
        help="Optional comma-separated sample IDs for a targeted packet",
    )

    human_alignment_p = sub.add_parser("human-alignment")
    add_common(human_alignment_p)
    human_alignment_p.add_argument(
        "--runs",
        default=str(DEFAULT_OUTPUT_DIR / "judge_runs.json"),
        help="Path to judge_runs.json",
    )
    human_alignment_p.add_argument(
        "--labels",
        default=str(DEFAULT_HUMAN_LABELS),
        help="Path to human_labels.json",
    )
    human_alignment_p.add_argument(
        "--sample-map",
        default="",
        help="Optional blind review_sample_id to original_sample_id mapping",
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
    if args.command == "convert-human-labels":
        return _convert_human_labels(args)
    if args.command == "export-review-packet":
        return _export_review_packet(args)
    if args.command == "human-alignment":
        return _human_alignment(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
