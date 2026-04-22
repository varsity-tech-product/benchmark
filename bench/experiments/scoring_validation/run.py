#!/usr/bin/env python3
"""One-file CLI for Tutor scoring validation.

Four essential operations:
1. Batch-run Tutor eval for the issue #48 corpus.
2. Export sampled judge-equivalent contexts for Codex omniscient review.
3. Store Codex omniscient labels in the generated JSON template.
4. Generate an HTML report from judge scores and omniscient labels.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from experiments.scoring_validation.config import (  # noqa: E402
    BATCH_SIZE,
    MAX_WORKERS,
    OMNISCIENT_D6_MIN,
    OMNISCIENT_SAMPLE_SIZE,
    OUTPUT_DIR,
    REPEATS,
    RESULTS_ROOT,
    TUTOR_DIMENSIONS,
    TUTOR_EVAL_MODEL,
    expected_combos,
    expected_session_count,
)
from experiments.scoring_validation.schemas import OMNISCIENT_LABEL_SCHEMA  # noqa: E402


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else BENCH_ROOT / p


def _paths(output_dir: str | Path) -> dict[str, Path]:
    root = _resolve(output_dir)
    return {
        "root": root,
        "manifest": root / "manifest.json",
        "eval_runs": root / "eval_runs.json",
        "scores": root / "scores.json",
        "contexts": root / "omniscient_sample" / "contexts.json",
        "labels": root / "omniscient_sample" / "labels.json",
        "report": root / "report" / "tutor_scoring_validation.html",
        "stats": root / "report" / "tutor_scoring_validation_stats.json",
    }


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_terminal_json(stdout: str) -> dict | None:
    """Return the final pretty-printed JSON object from a subprocess stdout stream."""
    decoder = json.JSONDecoder()
    for idx, char in enumerate(stdout):
        if char != "{":
            continue
        try:
            payload, end = decoder.raw_decode(stdout[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict) and not stdout[idx + end :].strip():
            return payload
    return None


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
        json.dump(data, fh, indent=2, ensure_ascii=False, default=str)
        fh.write("\n")
        tmp = Path(fh.name)
    tmp.replace(path)


def _result_session_id(run_dir: Path, run_state: dict) -> str:
    sid_file = run_dir / ".session_id"
    if sid_file.exists():
        return sid_file.read_text(encoding="utf-8").strip()
    return str(run_state.get("session_id") or "")


def _collect_manifest(results_root: Path) -> dict:
    sessions: list[dict] = []
    missing: list[dict] = []
    extra: list[dict] = []

    for combo in expected_combos():
        combo_dir = results_root / combo.task_id / combo.persona_id
        run_dirs = sorted(
            [p for p in combo_dir.glob("*") if (p / "run_state.json").exists()],
            key=lambda p: p.name,
        )
        if len(run_dirs) < REPEATS:
            missing.append(
                {
                    "task_id": combo.task_id,
                    "persona_id": combo.persona_id,
                    "found": len(run_dirs),
                    "expected": REPEATS,
                }
            )
        if len(run_dirs) > REPEATS:
            extra.append(
                {
                    "task_id": combo.task_id,
                    "persona_id": combo.persona_id,
                    "found": len(run_dirs),
                    "using_latest": REPEATS,
                }
            )
        selected = run_dirs[-REPEATS:]
        for repeat_index, run_dir in enumerate(selected):
            run_state = _load_json(run_dir / "run_state.json")
            session_id = _result_session_id(run_dir, run_state)
            sessions.append(
                {
                    "task_id": combo.task_id,
                    "category": combo.category,
                    "difficulty": combo.difficulty,
                    "persona_id": combo.persona_id,
                    "repeat_index": repeat_index,
                    "session_id": session_id,
                    "short_session_id": session_id[:12],
                    "result_dir_abs": str(run_dir),
                    "run_dir_name": run_dir.name,
                    "session_status": run_state.get("session_status"),
                    "termination_reason": run_state.get("termination_reason"),
                    "conversation_turns": len(run_state.get("conversation") or []),
                    "tool_log_count": len(run_state.get("tool_logs") or []),
                    "step_count": run_state.get("step_count"),
                    "manifest_ok": bool(
                        session_id
                        and run_state.get("session_status") == "completed"
                        and len(run_state.get("conversation") or []) >= 2
                    ),
                }
            )

    return {
        "version": "scoring_validation_manifest_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "results_root": str(results_root),
        "expected_sessions": expected_session_count(),
        "counts": {
            "sessions": len(sessions),
            "manifest_ok": sum(1 for s in sessions if s["manifest_ok"]),
            "missing_combos": len(missing),
            "extra_combos": len(extra),
        },
        "missing": missing,
        "extra": extra,
        "sessions": sessions,
    }


def _load_index(result_dir: Path) -> dict:
    path = result_dir / "evaluations" / "index.json"
    if not path.exists():
        return {"scores": []}
    return _load_json(path)


def _matching_tutor_score(result_dir: Path, eval_model: str) -> dict | None:
    index = _load_index(result_dir)
    matches = [
        entry
        for entry in index.get("scores", [])
        if entry.get("eval_mode") == "tutor"
        and entry.get("eval_model") == eval_model
        and str(entry.get("status", "")).startswith("completed")
    ]
    return matches[-1] if matches else None


def _run_eval_one(
    session: dict, *, results_root: Path, eval_model: str, force: bool, dry_run: bool
) -> dict:
    result_dir = Path(session["result_dir_abs"])
    existing = _matching_tutor_score(result_dir, eval_model)
    if existing and not force:
        return {
            "session_id": session["session_id"],
            "task_id": session["task_id"],
            "persona_id": session["persona_id"],
            "repeat_index": session["repeat_index"],
            "status": "skipped_existing",
            "score_id": existing.get("score_id"),
        }

    cmd = [
        sys.executable,
        "-m",
        "server.scripts.eval_single",
        "--session",
        session["session_id"],
        "--mode",
        "tutor",
        "--eval-model",
        eval_model,
        "--results-root",
        str(results_root),
    ]
    if dry_run:
        return {**session, "status": "dry_run", "command": cmd}

    start = time.time()
    proc = subprocess.run(
        cmd,
        cwd=BENCH_ROOT,
        env=os.environ.copy(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    record = {
        "session_id": session["session_id"],
        "task_id": session["task_id"],
        "persona_id": session["persona_id"],
        "repeat_index": session["repeat_index"],
        "status": "completed" if proc.returncode == 0 else "failed",
        "returncode": proc.returncode,
        "duration_seconds": round(time.time() - start, 3),
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }
    payload = _extract_terminal_json(proc.stdout)
    if payload:
        record["score_id"] = payload.get("score_id")
        record["score_status"] = payload.get("score_status") or payload.get("status")
    elif proc.returncode == 0:
        record["warning"] = (
            "eval_single returned success but stdout did not end with a JSON object"
        )
    return record


def _chunks(items: list[dict], size: int) -> list[list[dict]]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _eval_tutor(args: argparse.Namespace, paths: dict[str, Path]) -> int:
    results_root = _resolve(args.results_root)
    manifest = _collect_manifest(results_root)
    _atomic_write_json(paths["manifest"], manifest)
    sessions = [s for s in manifest["sessions"] if s["manifest_ok"]]
    if args.limit:
        sessions = sessions[: args.limit]

    records: list[dict] = []
    for batch_index, batch in enumerate(_chunks(sessions, args.batch_size), start=1):
        print(f"Batch {batch_index}: {len(batch)} sessions")
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [
                pool.submit(
                    _run_eval_one,
                    session,
                    results_root=results_root,
                    eval_model=args.eval_model,
                    force=args.force,
                    dry_run=args.dry_run,
                )
                for session in batch
            ]
            for fut in as_completed(futures):
                record = fut.result()
                records.append(record)
                print(
                    f"  {record.get('status')}: {record.get('short_session_id') or record.get('session_id', '')[:12]}"
                )

    payload = {
        "version": "tutor_eval_batch_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eval_model": args.eval_model,
        "dry_run": args.dry_run,
        "counts": {
            "records": len(records),
            "completed": sum(1 for r in records if r.get("status") == "completed"),
            "failed": sum(1 for r in records if r.get("status") == "failed"),
            "skipped_existing": sum(
                1 for r in records if r.get("status") == "skipped_existing"
            ),
            "dry_run": sum(1 for r in records if r.get("status") == "dry_run"),
        },
        "records": records,
    }
    _atomic_write_json(paths["eval_runs"], payload)
    print(
        json.dumps(
            {
                "manifest": str(paths["manifest"]),
                "eval_runs": str(paths["eval_runs"]),
                "counts": payload["counts"],
            },
            indent=2,
        )
    )
    return 1 if payload["counts"]["failed"] else 0


def _raw_score(detail: dict, dim: str) -> int | None:
    per_run = detail.get("_per_run_scores", {}).get(dim, {})
    for model_runs in per_run.values():
        for run_data in (model_runs or {}).values():
            raw = run_data.get("raw_int")
            if isinstance(raw, int):
                return raw
    norm = detail.get(dim, {}).get("score")
    if isinstance(norm, (int, float)):
        return int(round(norm * 4 + 1))
    return None


def _aggregate_scores(
    paths: dict[str, Path], eval_model: str, results_root: Path
) -> dict:
    manifest = _collect_manifest(results_root)
    _atomic_write_json(paths["manifest"], manifest)
    rows: list[dict] = []
    missing: list[dict] = []
    for session in manifest["sessions"]:
        if not session["manifest_ok"]:
            continue
        result_dir = Path(session["result_dir_abs"])
        match = _matching_tutor_score(result_dir, eval_model)
        if not match:
            missing.append({**session, "reason": "no_matching_tutor_score"})
            continue
        score = _load_json(
            result_dir / "evaluations" / match["score_id"] / "score.json"
        )
        tutor = score.get("tutor") or {}
        detail = tutor.get("detail") or {}
        weights = detail.get("_weights_used") or {}
        for dim in TUTOR_DIMENSIONS:
            dim_short = dim[:2]
            active = bool(weights.get(dim_short))
            dim_detail = detail.get(dim) or {}
            rows.append(
                {
                    **session,
                    "score_id": match["score_id"],
                    "eval_model": eval_model,
                    "tutor_score": tutor.get("score"),
                    "dimension": dim,
                    "dimension_short": dim_short,
                    "active": active,
                    "status": (
                        dim_detail.get("status")
                        if active
                        else "skipped_category_weight"
                    ),
                    "score_norm": dim_detail.get("score") if active else None,
                    "score_raw": _raw_score(detail, dim) if active else None,
                    "reason": dim_detail.get("reason", "") if active else "",
                    "evidence": dim_detail.get("evidence", []) if active else [],
                }
            )
    payload = {
        "version": "tutor_score_rows_v2",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "eval_model": eval_model,
        "counts": {
            "rows": len(rows),
            "active_rows": sum(1 for r in rows if r["active"]),
            "sessions_with_scores": len({r["session_id"] for r in rows}),
            "missing_sessions": len(missing),
        },
        "missing": missing,
        "rows": rows,
    }
    _atomic_write_json(paths["scores"], payload)
    return payload


def _load_task_and_persona(task_id: str, persona_id: str):
    from server.eval.core.coordinator import load_persona_by_id, load_task_by_id

    return load_task_by_id(BENCH_ROOT, task_id), load_persona_by_id(
        BENCH_ROOT, persona_id
    )


def _active_dims(category: str, requires_code: bool) -> list[str]:
    from server.eval.judges.tutor_6d import get_dimension_weight

    return [
        dim
        for dim in TUTOR_DIMENSIONS
        if get_dimension_weight(category, dim, requires_code=requires_code) > 0
    ]


def _dimension_contexts(
    session: dict, score_lookup: dict[tuple[str, str], dict]
) -> list[dict]:
    from server.eval.core.coordinator import coerce_tool_logs
    from server.eval.inputs.context_builder import build_tutor_context
    from server.eval.inputs.enrichment import enrich_conversation_with_tools
    from server.eval.inputs.rubric_builder import (
        build_rubric_text,
        get_max_score,
        load_6d_rubric,
    )

    run_dir = Path(session["result_dir_abs"])
    run_state = _load_json(run_dir / "run_state.json")
    task, persona = _load_task_and_persona(session["task_id"], session["persona_id"])
    category = session["category"]
    requires_code = bool(getattr(task, "requires_code", False))
    conversation = run_state.get("conversation") or []
    tool_logs = coerce_tool_logs(run_state.get("tool_logs") or [])
    enriched = enrich_conversation_with_tools(conversation, tool_logs, mode="full")

    rubric = load_6d_rubric()
    role = rubric.get("role", "")
    rules = rubric.get("rules", [])
    context_cache: dict = {}

    contexts = []
    for dim in _active_dims(category, requires_code):
        score_row = score_lookup.get((session["session_id"], dim), {})
        contexts.append(
            {
                "dimension": dim,
                "dimension_short": dim[:2],
                "role": role,
                "rules": rules,
                "criteria": build_rubric_text(
                    rubric, dim, session["persona_id"], category
                ),
                "max_score": get_max_score(rubric, dim),
                "context": build_tutor_context(
                    conversation=conversation,
                    enriched_conversation=enriched,
                    dimension_name=dim,
                    _cache=context_cache,
                ),
                "llm_judge": {
                    "score_raw": score_row.get("score_raw"),
                    "score_norm": score_row.get("score_norm"),
                    "reason": score_row.get("reason"),
                    "evidence": score_row.get("evidence"),
                },
            }
        )
    return contexts


def _pick_omniscient_sample(sessions: list[dict], size: int, d6_min: int) -> list[dict]:
    valid = [s for s in sessions if s["manifest_ok"]]
    valid.sort(key=lambda s: (s["task_id"], s["persona_id"], s["repeat_index"]))
    chosen: list[dict] = []
    seen: set[str] = set()

    def add(session: dict) -> None:
        if len(chosen) >= size or session["session_id"] in seen:
            return
        chosen.append(session)
        seen.add(session["session_id"])

    # Broad coverage: one repeat-0 per task.
    seen_tasks: set[str] = set()
    for session in valid:
        if session["repeat_index"] == 0 and session["task_id"] not in seen_tasks:
            add(session)
            seen_tasks.add(session["task_id"])

    # D6 targeted oversampling.
    d6_categories = {"adversarial", "strategy", "backtest", "end_to_end"}
    while sum(1 for s in chosen if s["category"] in d6_categories) < d6_min:
        before = len(chosen)
        for session in valid:
            if session["category"] in d6_categories:
                add(session)
                if sum(1 for s in chosen if s["category"] in d6_categories) >= d6_min:
                    break
        if len(chosen) == before:
            break

    for session in valid:
        add(session)
    return chosen[:size]


def _export_sample(args: argparse.Namespace, paths: dict[str, Path]) -> int:
    scores = _aggregate_scores(paths, args.eval_model, _resolve(args.results_root))
    score_lookup = {
        (row["session_id"], row["dimension"]): row
        for row in scores["rows"]
        if row.get("active")
    }
    manifest = _load_json(paths["manifest"])
    sample = _pick_omniscient_sample(
        manifest["sessions"], args.sample_size, args.d6_min
    )

    items = []
    labels = []
    for idx, session in enumerate(sample, start=1):
        sample_id = f"sv_{idx:03d}"
        contexts = _dimension_contexts(session, score_lookup)
        items.append({**session, "sample_id": sample_id, "dimensions": contexts})
        for ctx in contexts:
            labels.append(
                {
                    "sample_id": sample_id,
                    "session_id": session["session_id"],
                    "task_id": session["task_id"],
                    "category": session["category"],
                    "difficulty": session["difficulty"],
                    "persona_id": session["persona_id"],
                    "repeat_index": session["repeat_index"],
                    "dimension": ctx["dimension"],
                    "score_raw": "",
                    "confidence": "",
                    "reason": "",
                    "evidence": [],
                    "rubric_notes": "",
                    "d6": (
                        {
                            "trigger_present": None,
                            "variant": None,
                            "boundary_result": None,
                        }
                        if ctx["dimension"] == "D6_safety_boundaries"
                        else None
                    ),
                }
            )

    context_payload = {
        "version": "omniscient_contexts_v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "description": "Sampled Tutor judge-equivalent contexts for Codex omniscient calibration.",
        "counts": {
            "samples": len(items),
            "dimension_contexts": sum(len(i["dimensions"]) for i in items),
        },
        "items": items,
    }
    label_payload = {
        **OMNISCIENT_LABEL_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "labels": labels,
    }
    _atomic_write_json(paths["contexts"], context_payload)
    _atomic_write_json(paths["labels"], label_payload)
    print(
        json.dumps(
            {
                "contexts": str(paths["contexts"]),
                "labels": str(paths["labels"]),
                "counts": context_payload["counts"],
            },
            indent=2,
        )
    )
    return 0


def _report(args: argparse.Namespace, paths: dict[str, Path]) -> int:
    from experiments.scoring_validation.report import generate_html_report

    scores = _aggregate_scores(paths, args.eval_model, _resolve(args.results_root))
    labels_path = Path(args.labels) if args.labels else paths["labels"]
    labels = []
    if labels_path.exists():
        label_payload = _load_json(labels_path)
        labels = label_payload.get("labels", [])
    stats = generate_html_report(
        score_rows=scores["rows"],
        labels=labels,
        output_path=paths["report"],
        stats_path=paths["stats"],
    )
    print(
        json.dumps(
            {
                "report": str(paths["report"]),
                "stats": str(paths["stats"]),
                "agreement_n": stats["omniscient_agreement"]["n"],
            },
            indent=2,
        )
    )
    return 0


def _dry_run(args: argparse.Namespace) -> None:
    print("=== Tutor Scoring Validation ===")
    print(f"  Expected sessions: {expected_session_count()}")
    print(f"  Task/persona combos: {len(expected_combos())}")
    print(f"  Repeats: {REPEATS}")
    print(f"  Eval model: {TUTOR_EVAL_MODEL}")
    print(f"  Batch size: {BATCH_SIZE}")
    print(f"  Workers: {MAX_WORKERS}")
    print(f"  Omniscient sample size: {OMNISCIENT_SAMPLE_SIZE}")
    print(f"  D6 minimum in sample: {OMNISCIENT_D6_MIN}")


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Tutor scoring validation")
    parser.add_argument("--output-dir", default=str(_resolve(OUTPUT_DIR)))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("dry-run")

    eval_p = sub.add_parser(
        "eval-tutor", help="Batch-run Tutor eval for issue #48 sessions"
    )
    eval_p.add_argument("--results-root", default=str(_resolve(RESULTS_ROOT)))
    eval_p.add_argument("--eval-model", default=TUTOR_EVAL_MODEL)
    eval_p.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    eval_p.add_argument("-w", "--workers", type=int, default=MAX_WORKERS)
    eval_p.add_argument("-n", "--limit", type=int, default=None)
    eval_p.add_argument("--force", action="store_true")
    eval_p.add_argument("--dry-run", action="store_true")

    export_p = sub.add_parser(
        "export-sample", help="Export sampled omniscient review contexts"
    )
    export_p.add_argument("--results-root", default=str(_resolve(RESULTS_ROOT)))
    export_p.add_argument("--eval-model", default=TUTOR_EVAL_MODEL)
    export_p.add_argument("--sample-size", type=int, default=OMNISCIENT_SAMPLE_SIZE)
    export_p.add_argument("--d6-min", type=int, default=OMNISCIENT_D6_MIN)

    report_p = sub.add_parser("report", help="Generate HTML validation report")
    report_p.add_argument("--results-root", default=str(_resolve(RESULTS_ROOT)))
    report_p.add_argument("--eval-model", default=TUTOR_EVAL_MODEL)
    report_p.add_argument("--labels", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _make_parser()
    args = parser.parse_args(argv)
    paths = _paths(args.output_dir)

    if args.command == "dry-run":
        _dry_run(args)
        return 0
    if args.command == "eval-tutor":
        return _eval_tutor(args, paths)
    if args.command == "export-sample":
        return _export_sample(args, paths)
    if args.command == "report":
        return _report(args, paths)
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
