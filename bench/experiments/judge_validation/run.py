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
from server.eval.judges.runtime.conv_pairwise import (  # noqa: E402
    EwanPairwiseGEval,
    PairwiseTestCase,
)
from server.eval.judges.runtime.model_resolver import resolve_ewan_model  # noqa: E402

from experiments.judge_validation.pairwise_stats import (  # noqa: E402
    compute_pairwise_stats,
    write_pairwise_reports,
)

DEFAULT_CORPUS = Path(__file__).resolve().parent / "pilot_corpus.json"
DEFAULT_HUMAN_LABELS = Path(__file__).resolve().parent / "human_labels.json"
DEFAULT_HUMAN_SAMPLE_MAP = (
    Path(__file__).resolve().parent / "human_review_sample_map.json"
)
DEFAULT_RUBRIC_REGISTRY = BENCH_ROOT / "server/eval/rubrics/rubric_registry.json"
DEFAULT_OUTPUT_DIR = Path("experiments/judge_validation/results")
DEFAULT_JUDGE_RUNS = DEFAULT_OUTPUT_DIR / "judge_runs.json"
DEFAULT_REVIEW_PACKET_DIR = DEFAULT_OUTPUT_DIR / "human_review_packet"
DEFAULT_REPEAT_COUNT = 3
DEFAULT_PROMPT_VARIANT = "baseline"
AUTO_HUMAN_SAMPLE_MAP = "auto"
SUPPORTED_PROMPT_VARIANTS = {
    "baseline",
    "role_blocks",
    "markdown_transcript",
}


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else BENCH_ROOT / p


def _same_path(left: Path, right: Path) -> bool:
    return left.resolve() == right.resolve()


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
                "stage3_primary_gate": stats["stage3_primary_gate"],
                "multi_judge": {
                    "overall": stats["multi_judge"]["overall"],
                    "gate": stats["multi_judge"]["gate"],
                },
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
    language = str(args.language or "en").strip().lower()
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
        language=language,
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
                "language": language,
            },
            indent=2,
        )
    )
    return 0


def _human_alignment(args: argparse.Namespace) -> int:
    corpus_path = _resolve(args.corpus)
    runs_path = _resolve(args.runs)
    labels_path = _resolve(args.labels)
    corpus = _load_json(corpus_path)
    runs = _load_json(runs_path)
    labels = load_human_labels(labels_path)
    sample_map_arg = str(args.sample_map or "").strip()
    sample_id_map = None
    if sample_map_arg == AUTO_HUMAN_SAMPLE_MAP:
        if (
            _same_path(corpus_path, DEFAULT_CORPUS)
            and _same_path(runs_path, _resolve(DEFAULT_JUDGE_RUNS))
            and _same_path(labels_path, DEFAULT_HUMAN_LABELS)
        ):
            sample_id_map = load_sample_id_map(DEFAULT_HUMAN_SAMPLE_MAP)
    elif sample_map_arg.lower() != "none" and sample_map_arg:
        sample_id_map = load_sample_id_map(_resolve(sample_map_arg))
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


DEFAULT_PAIRWISE_REPEATS = 3
PAIRWISE_MAX_ATTEMPTS = 3
PAIRWISE_RETRY_SLEEP_SECONDS = 1.0
_PAIRWISE_SWAP_ASSIGNMENTS = [
    ("stronger", "weaker"),  # A = stronger, B = weaker
    ("weaker", "stronger"),  # A = weaker,   B = stronger
]

_QR_AGENT_RESULT_HEADER = "## Agent Result"


def _pair_student_message(stronger_turns: list[Turn], weaker_turns: list[Turn]) -> str:
    """First user turn of the pair — expected to be identical between sides.

    If the two transcripts diverge in the student turn, return the stronger
    side's student message and emit a warning — the prompt assumes a shared
    opening turn.
    """
    if not stronger_turns or stronger_turns[0].role != "user":
        return ""
    msg = stronger_turns[0].content
    if weaker_turns and weaker_turns[0].role == "user":
        if weaker_turns[0].content != msg:
            return msg
    return msg


def _tutor_response(turns: list[Turn]) -> str:
    """Concatenate non-user turns into a single response string for pairwise.

    Keeps role markers so a multi-turn tutor response still reads cleanly.
    """
    blocks = []
    for turn in turns:
        if turn.role == "user":
            continue
        role = turn.role.title() if turn.role else "Assistant"
        blocks.append(f"[{role}]\n{turn.content}")
    return "\n\n".join(blocks)


def _split_qr_context(context: str) -> tuple[str, str]:
    """Split a QR/QP context string into shared (task + RC) and per-side
    (agent result) halves.

    The corpus's QR context strings look like::

        ## Task
        ...
        ## Required Capabilities (Acceptance Criteria)
        ...
        ## Agent Result
        ...

    Everything up to and excluding ``## Agent Result`` is shared; the
    ``## Agent Result`` block onward is per-side. If the marker is
    absent, the full context is returned as the shared half and the
    per-side half is empty (the caller must fall back to conversation
    turns).
    """
    marker = _QR_AGENT_RESULT_HEADER
    idx = context.find(marker)
    if idx < 0:
        return context.rstrip(), ""
    shared = context[:idx].rstrip()
    agent_result = context[idx:].rstrip()
    return shared, agent_result


def _pairwise_inputs(
    stronger_item: dict[str, Any],
    weaker_item: dict[str, Any],
) -> tuple[str, str, str]:
    """Build (shared_context, stronger_response, weaker_response) for a pair.

    QR/QP items carry a structured ``context`` block that holds both the
    shared task/RC and the per-side agent result. Tutor items carry a
    ``conversation`` list; the shared context is the student opening turn
    and the per-side response is everything the tutor wrote.
    """
    track = str(stronger_item.get("track", "tutor"))
    if track in {"qr", "qp"}:
        stronger_ctx = str(stronger_item.get("context") or "")
        weaker_ctx = str(weaker_item.get("context") or "")
        stronger_shared, stronger_result = _split_qr_context(stronger_ctx)
        weaker_shared, weaker_result = _split_qr_context(weaker_ctx)
        shared = stronger_shared or weaker_shared
        stronger_response = stronger_result or stronger_shared
        weaker_response = weaker_result or weaker_shared
        return shared, stronger_response, weaker_response

    stronger_turns = [
        Turn(role=str(t.get("role", "")), content=str(t.get("content", "")))
        for t in stronger_item.get("conversation", [])
    ]
    weaker_turns = [
        Turn(role=str(t.get("role", "")), content=str(t.get("content", "")))
        for t in weaker_item.get("conversation", [])
    ]
    shared = _pair_student_message(stronger_turns, weaker_turns)
    return shared, _tutor_response(stronger_turns), _tutor_response(weaker_turns)


_PAIRWISE_RULES_SUFFIX = (
    "- Compare Response A and Response B against the scoring rubric.\n"
    "- The tutor (assistant) authored both responses; the student "
    "opening message is shared.\n"
    "- Pick the response that better satisfies the rubric. If the two "
    "are effectively equivalent, answer \"tie\".\n"
    "- Do not base the decision on slot letter, length, or ordering; "
    "use only rubric-grounded content."
)


def _pairwise_metric(
    *,
    pair: dict[str, Any],
    stronger_item: dict[str, Any],
    model: str,
) -> EwanPairwiseGEval:
    dimension = str(pair["dimension"])
    track = str(stronger_item.get("track", "tutor"))
    category = stronger_item.get("category")
    persona_id = stronger_item.get("persona_id", "double_novice")
    model_obj = resolve_ewan_model(model)

    extra = {
        "registry_rubric_id": pair.get("registry_rubric_id"),
        "pair_id": pair.get("pair_id"),
    }
    context_fields = ["response_a", "response_b"]
    transcript_source = "adversarial_pair"

    if track == "tutor":
        rubric = load_6d_rubric()
        criteria = build_rubric_text(rubric, dimension, persona_id, category)
        role = str(rubric.get("role", ""))
        rules_base = _rules_text(rubric.get("rules", []))
        metadata = build_rubric_metadata(
            rubric,
            dimension,
            rubric_name="tutor_6d",
            context_fields=context_fields,
            transcript_source=transcript_source,
            extra=extra,
        )
    elif track in {"qp", "qr"}:
        rubric = load_rubric(track)
        params = build_eval_params(
            rubric,
            dimension,
            rubric_name=track,
            context_fields=context_fields,
            transcript_source=transcript_source,
        )
        criteria = params["criteria"]
        role = params["role"]
        rules_base = params["rules"]
        metadata = dict(params["rubric_metadata"])
        metadata.update(extra)
    else:
        raise ValueError(f"Unsupported pairwise track: {track}")

    rules = f"{rules_base}\n{_PAIRWISE_RULES_SUFFIX}" if rules_base else _PAIRWISE_RULES_SUFFIX
    return EwanPairwiseGEval(
        name=str(pair["pair_id"]),
        criteria=criteria,
        role=role,
        rules=rules,
        model=model_obj,
        rubric_metadata=metadata,
    )


def _is_pairwise_json_error(exc: Exception, raw: str | None) -> bool:
    text = str(exc).lower()
    return bool(raw) or "invalid pairwise preferred" in text or "json" in text


async def _pairwise_one(
    *,
    run_id: str,
    pair: dict[str, Any],
    items_by_id: dict[str, dict[str, Any]],
    model: str,
    a_side: str,
    run_index: int,
) -> dict[str, Any]:
    stronger_item = items_by_id[pair["stronger_sample_id"]]
    weaker_item = items_by_id[pair["weaker_sample_id"]]
    shared_context, stronger_response, weaker_response = _pairwise_inputs(
        stronger_item, weaker_item
    )

    if a_side == "stronger":
        response_a, response_b = stronger_response, weaker_response
    else:
        response_a, response_b = weaker_response, stronger_response
    b_side = "weaker" if a_side == "stronger" else "stronger"

    run_timestamp = datetime.now(timezone.utc).isoformat()
    base = {
        "run_id": run_id,
        "pair_id": pair["pair_id"],
        "dimension": pair["dimension"],
        "registry_rubric_id": pair.get("registry_rubric_id"),
        "stronger_sample_id": pair["stronger_sample_id"],
        "weaker_sample_id": pair["weaker_sample_id"],
        "a_side": a_side,
        "b_side": b_side,
        "run_index": run_index,
        "run_timestamp": run_timestamp,
    }

    test_case = PairwiseTestCase(
        shared_context=shared_context,
        response_a=response_a,
        response_b=response_b,
    )

    start = time.time()
    total_cost = 0.0
    last_exc: Exception | None = None
    last_raw: str | None = None
    last_metric: EwanPairwiseGEval | None = None
    for attempt in range(PAIRWISE_MAX_ATTEMPTS):
        metric = _pairwise_metric(
            pair=pair, stronger_item=stronger_item, model=model
        )
        last_metric = metric
        try:
            preferred = await metric.a_measure(test_case)
            total_cost += float(metric.evaluation_cost)
            metadata = dict(metric.judge_metadata())
            metadata["run_timestamp"] = run_timestamp
            metadata["attempts"] = attempt + 1
            if preferred == "A":
                preferred_side = a_side
            elif preferred == "B":
                preferred_side = b_side
            else:
                preferred_side = "tie"
            return {
                **base,
                "judge_model": metadata.get("judge_model"),
                "judge_metadata": metadata,
                "status": "success",
                "preferred_slot": preferred,
                "preferred_side": preferred_side,
                "margin": metric.margin,
                "reason": metric.reason,
                "evaluation_cost": round(total_cost, 6),
                "duration_seconds": round(time.time() - start, 3),
            }
        except Exception as exc:  # noqa: BLE001 - retry transient JSON failures
            last_exc = exc
            total_cost += float(getattr(metric, "evaluation_cost", 0.0) or 0.0)
            raw = getattr(metric, "_raw_failed_response", None)
            if raw:
                last_raw = str(raw)
            if attempt < PAIRWISE_MAX_ATTEMPTS - 1 and _is_pairwise_json_error(
                exc, last_raw
            ):
                await asyncio.sleep(PAIRWISE_RETRY_SLEEP_SECONDS)
                continue
            break

    metadata = dict(last_metric.judge_metadata()) if last_metric else {}
    metadata["run_timestamp"] = run_timestamp
    metadata["attempts"] = PAIRWISE_MAX_ATTEMPTS
    diagnostics: dict[str, Any] = {"attempts": PAIRWISE_MAX_ATTEMPTS}
    if last_raw:
        diagnostics["raw_response_excerpt"] = last_raw[:500]
    return {
        **base,
        "judge_model": metadata.get("judge_model"),
        "judge_metadata": metadata,
        "status": "failed",
        "preferred_slot": None,
        "preferred_side": None,
        "margin": "",
        "reason": str(last_exc) if last_exc else "pairwise judge failed",
        "evaluation_cost": round(total_cost, 6),
        "duration_seconds": round(time.time() - start, 3),
        "diagnostics": diagnostics,
    }


def _pairwise(args: argparse.Namespace) -> int:
    corpus = _load_json(_resolve(args.corpus))
    output_dir = _resolve(args.output_dir)
    run_id = args.run_id or (
        f"jvp_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    )
    pairs = corpus.get("adversarial_pairs", [])
    items_by_id = {item["sample_id"]: item for item in corpus.get("items", [])}
    missing = [
        pair["pair_id"]
        for pair in pairs
        if pair["stronger_sample_id"] not in items_by_id
        or pair["weaker_sample_id"] not in items_by_id
    ]
    if missing:
        parser_error = (
            f"adversarial_pairs reference missing sample_id(s): {missing}"
        )
        raise ValueError(parser_error)

    tasks = [
        _pairwise_one(
            run_id=run_id,
            pair=pair,
            items_by_id=items_by_id,
            model=args.model,
            a_side=a_side,
            run_index=run_index,
        )
        for pair in pairs
        for a_side, _b_side in _PAIRWISE_SWAP_ASSIGNMENTS
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
        "version": "judge_pairwise_runs_v1",
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "judge_model": args.model,
        "repeat_count": args.repeats,
        "pairs": len(pairs),
        "swap_assignments": [list(pair) for pair in _PAIRWISE_SWAP_ASSIGNMENTS],
        "counts": {
            "records": len(records),
            "success": sum(1 for record in records if record["status"] == "success"),
            "failed": sum(1 for record in records if record["status"] == "failed"),
        },
        "records": records,
    }
    output_path = output_dir / "judge_pairwise_runs.json"
    _atomic_write_json(output_path, payload)
    print(
        json.dumps(
            {"judge_pairwise_runs": str(output_path), "counts": payload["counts"]},
            indent=2,
        )
    )
    return 1 if payload["counts"]["failed"] else 0


def _report_pairwise(args: argparse.Namespace) -> int:
    corpus = _load_json(_resolve(args.corpus))
    runs = _load_json(_resolve(args.runs))
    records = runs.get("records", [])
    stats = compute_pairwise_stats(corpus=corpus, records=records)
    output_dir = _resolve(args.output_dir)
    paths = write_pairwise_reports(
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
                "overall": stats["overall"],
                "stage_pairwise_gate": stats["gate"],
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
    parser.add_argument("--model", default="anthropic/claude-sonnet-4.6")
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
        default=str(DEFAULT_JUDGE_RUNS),
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
    review_packet_p.add_argument(
        "--language",
        choices=["en", "zh"],
        default="en",
        help="Language for rubric metadata and Google Form blueprint",
    )

    human_alignment_p = sub.add_parser("human-alignment")
    add_common(human_alignment_p)
    human_alignment_p.add_argument(
        "--runs",
        default=str(DEFAULT_JUDGE_RUNS),
        help="Path to judge_runs.json",
    )
    human_alignment_p.add_argument(
        "--labels",
        default=str(DEFAULT_HUMAN_LABELS),
        help="Path to human_labels.json",
    )
    human_alignment_p.add_argument(
        "--sample-map",
        default=AUTO_HUMAN_SAMPLE_MAP,
        help=(
            "Optional blind review_sample_id to original_sample_id mapping; "
            "'auto' applies the bundled pilot map only with bundled artifacts, "
            "and 'none' disables mapping"
        ),
    )

    pairwise_p = sub.add_parser("pairwise")
    add_common(pairwise_p)
    pairwise_p.add_argument("-w", "--workers", type=int, default=2)

    report_pairwise_p = sub.add_parser("report-pairwise")
    add_common(report_pairwise_p)
    report_pairwise_p.add_argument(
        "--runs",
        default=str(DEFAULT_OUTPUT_DIR / "judge_pairwise_runs.json"),
        help="Path to judge_pairwise_runs.json",
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
    if args.command == "pairwise":
        return _pairwise(args)
    if args.command == "report-pairwise":
        return _report_pairwise(args)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
