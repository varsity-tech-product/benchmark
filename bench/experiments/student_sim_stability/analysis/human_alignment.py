"""Human quant-expert alignment artifact helpers."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from experiments.student_sim_stability.core.paths import BENCH_ROOT

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.core.config import OUTPUT_DIR  # noqa: E402

LABEL_FIELDS = [
    "eval_id",
    "dimension",
    "source_file",
    "persona_id",
    "task_id",
    "model",
    "persona_fidelity",
    "knowledge_boundary_pass",
    "emotional_match",
    "drift_onset_turn",
    "failure_type",
    "human_comment",
]

JUDGE_SCORE_FIELD_BY_DIMENSION = {
    "D1": "overall",
    "D4": "overall_drift_score",
    "P1": "overall_probe_pass",
    "B1": "contract_fit",
}

KNOWLEDGE_FIELD_BY_DIMENSION = {
    "D1": "knowledge_boundary",
    "D3": "knowledge_boundary_preserved",
}

EMOTIONAL_FIELD_BY_DIMENSION = {
    "D1": "emotional_tone",
    "D2": "emotional_consistency",
    "D3": "emotional_profile_preserved",
}


def default_results_dir() -> Path:
    return BENCH_ROOT / OUTPUT_DIR


def _sample_key(payload: dict) -> tuple:
    metadata = payload.get("metadata", {})
    return (
        payload.get("dimension", ""),
        metadata.get("persona_id", ""),
        metadata.get("task_id", ""),
        metadata.get("model", ""),
        payload.get("eval_id", ""),
    )


def _stratified_judge_input_samples(input_dir: Path, sample_limit: int) -> list[dict]:
    payloads = []
    for path in sorted(input_dir.glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        payloads.append((path, payload))

    priority = ["D1", "D4", "P1", "B1", "control", "D2", "D3"]
    buckets: dict[str, list[tuple[Path, dict]]] = defaultdict(list)
    for item in payloads:
        buckets[str(item[1].get("dimension", ""))].append(item)
    for values in buckets.values():
        values.sort(key=lambda item: _sample_key(item[1]))

    ordered = []
    while len(ordered) < sample_limit:
        made_progress = False
        for dimension in priority:
            values = buckets.get(dimension) or []
            if values:
                ordered.append(values.pop(0))
                made_progress = True
                if len(ordered) >= sample_limit:
                    break
        if not made_progress:
            break

    samples = []
    for path, payload in ordered:
        metadata = payload.get("metadata", {})
        samples.append(
            {
                "eval_id": payload.get("eval_id", path.stem),
                "dimension": payload.get("dimension"),
                "source_file": metadata.get("source_file")
                or metadata.get("persona_source_file"),
                "persona_id": metadata.get("persona_id"),
                "task_id": metadata.get("task_id"),
                "model": metadata.get("model"),
                "judge_input_file": path.name,
            }
        )
    return samples


def _write_llm_label_snapshot(out: Path, samples: list[dict]) -> dict:
    output_dir = out / "judge_outputs"
    rows = []
    missing = []
    for sample in samples:
        output_path = output_dir / f"{sample['eval_id']}.json"
        if not output_path.exists():
            missing.append(sample["eval_id"])
            continue
        with open(output_path, encoding="utf-8") as fh:
            output = json.load(fh)
        scores = output.get("scores", {})
        rows.append(
            {
                "eval_id": sample["eval_id"],
                "dimension": sample["dimension"],
                "judge_output_file": output_path.name,
                "judge_model": output.get("judge_model"),
                "rubric_id": output.get("rubric_id"),
                "scores": scores,
                "persona_fidelity_score": scores.get(
                    JUDGE_SCORE_FIELD_BY_DIMENSION.get(sample["dimension"], "")
                ),
                "drift_onset_turn": scores.get("drift_onset_turn"),
                "failure_types": scores.get("failure_types") or [],
                "dominant_failure_type": scores.get("dominant_failure_type"),
            }
        )
    snapshot = {
        "sample_count": len(samples),
        "llm_label_count": len(rows),
        "missing_judge_outputs": missing,
        "labels": rows,
    }
    align_dir = out / "human_alignment"
    with open(align_dir / "llm_judge_labels.json", "w", encoding="utf-8") as fh:
        json.dump(snapshot, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return snapshot


def init_human_alignment(
    results_dir: Path | None = None,
    sample_limit: int = 50,
    overwrite: bool = False,
) -> dict:
    out = results_dir or default_results_dir()
    if not out.is_absolute():
        out = BENCH_ROOT / out
    align_dir = out / "human_alignment"
    align_dir.mkdir(parents=True, exist_ok=True)

    input_dir = out / "judge_inputs"
    samples = _stratified_judge_input_samples(input_dir, sample_limit)
    llm_snapshot = _write_llm_label_snapshot(out, samples)

    status = "sampled" if samples else "not_run"
    manifest = {
        "human_alignment_status": status,
        "sample_limit": sample_limit,
        "sample_count": len(samples),
        "sampling_policy": "deterministic round-robin over D1, D4, P1, B1, control, D2, D3",
        "llm_judge_label_count": llm_snapshot["llm_label_count"],
        "samples": samples,
    }
    with open(align_dir / "sample_manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")

    label_path = align_dir / "human_label_template.csv"
    if overwrite or not label_path.exists():
        with open(
            label_path,
            "w",
            encoding="utf-8",
            newline="",
        ) as fh:
            writer = csv.DictWriter(fh, fieldnames=LABEL_FIELDS)
            writer.writeheader()
            for sample in samples:
                row = {field: "" for field in LABEL_FIELDS}
                row.update(
                    {
                        "eval_id": sample["eval_id"],
                        "dimension": sample["dimension"],
                        "source_file": sample["source_file"],
                        "persona_id": sample["persona_id"],
                        "task_id": sample["task_id"],
                        "model": sample["model"],
                    }
                )
                writer.writerow(row)

    agreement_stub = {
        "human_alignment_status": status,
        "agreement_metrics": None,
        "disagreement_examples": [],
        "notes": "Fill human_label_template.csv and run an agreement implementation before treating this as calibrated.",
    }
    agreement_path = align_dir / "agreement_report.json"
    if overwrite or not agreement_path.exists():
        with open(agreement_path, "w", encoding="utf-8") as fh:
            json.dump(agreement_stub, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
    disagreement_path = align_dir / "disagreement_examples.md"
    if overwrite or not disagreement_path.exists():
        with open(disagreement_path, "w", encoding="utf-8") as fh:
            fh.write(
                "# Human Alignment Disagreement Examples\n\nStatus: not labeled.\n"
            )

    return manifest


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _as_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _numeric_agreement(rows: list[dict], field: str) -> dict | None:
    values = [row[field] for row in rows if row.get(field) is not None]
    if not values:
        return None
    return {
        "n": len(values),
        "mean_absolute_difference": _mean(values),
        "within_one_point_rate": _mean(
            [1.0 if value <= 1.0 else 0.0 for value in values]
        ),
    }


def _failure_type_agreement(rows: list[dict]) -> dict | None:
    values = [row for row in rows if row.get("failure_type_match") is not None]
    if not values:
        return None
    return {
        "n": len(values),
        "exact_or_contained_match_rate": _mean(
            [1.0 if row["failure_type_match"] else 0.0 for row in values]
        ),
    }


def compute_human_agreement(
    results_dir: Path | None = None,
    labels_path: Path | None = None,
) -> dict:
    out = results_dir or default_results_dir()
    if not out.is_absolute():
        out = BENCH_ROOT / out
    align_dir = out / "human_alignment"
    labels = labels_path or align_dir / "human_label_template.csv"
    eval_path = out / "evaluations" / "all_evaluations.json"
    align_dir.mkdir(parents=True, exist_ok=True)

    if not labels.exists() or not eval_path.exists():
        report = {
            "human_alignment_status": (
                "sampled"
                if (align_dir / "sample_manifest.json").exists()
                else "not_run"
            ),
            "agreement_metrics": None,
            "disagreement_examples": [],
            "notes": "Human label file or aggregate evaluation file is missing.",
        }
        with open(align_dir / "agreement_report.json", "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return report

    aggregate = _load_json(eval_path)
    by_eval_id = {
        record.get("eval_id"): record
        for records in aggregate.values()
        if isinstance(records, list)
        for record in records
    }

    compared = []
    knowledge_compared = []
    emotional_compared = []
    drift_compared = []
    failure_type_compared = []
    all_disagreements = []
    with open(labels, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            eval_id = row.get("eval_id")
            record = by_eval_id.get(eval_id)
            if not record:
                continue
            dimension = (
                record.get("metadata", {}).get("dimension")
                or record.get("eval_id", "").split("__", 1)[0]
            )
            scores = record.get("scores", {})

            human_score = _as_float(row.get("persona_fidelity"))
            score_field = JUDGE_SCORE_FIELD_BY_DIMENSION.get(dimension)
            judge_score = _as_float(scores.get(score_field)) if score_field else None
            if human_score is not None and judge_score is not None:
                compared.append(
                    {
                        "eval_id": eval_id,
                        "dimension": dimension,
                        "human_score": human_score,
                        "judge_score": judge_score,
                        "abs_diff": abs(human_score - judge_score),
                        "human_comment": row.get("human_comment", ""),
                    }
                )
                all_disagreements.append(
                    {
                        "category": "persona_fidelity",
                        "eval_id": eval_id,
                        "dimension": dimension,
                        "human_score": human_score,
                        "judge_score": judge_score,
                        "abs_diff": abs(human_score - judge_score),
                        "human_comment": row.get("human_comment", ""),
                    }
                )

            human_knowledge = _as_float(row.get("knowledge_boundary_pass"))
            knowledge_field = KNOWLEDGE_FIELD_BY_DIMENSION.get(dimension)
            judge_knowledge = (
                _as_float(scores.get(knowledge_field)) if knowledge_field else None
            )
            if human_knowledge is not None and judge_knowledge is not None:
                knowledge_compared.append(
                    {
                        "eval_id": eval_id,
                        "abs_diff": abs(human_knowledge - judge_knowledge),
                    }
                )
                all_disagreements.append(
                    {
                        "category": "knowledge_boundary_pass",
                        "eval_id": eval_id,
                        "dimension": dimension,
                        "human_score": human_knowledge,
                        "judge_score": judge_knowledge,
                        "abs_diff": abs(human_knowledge - judge_knowledge),
                        "human_comment": row.get("human_comment", ""),
                    }
                )

            human_emotional = _as_float(row.get("emotional_match"))
            emotional_field = EMOTIONAL_FIELD_BY_DIMENSION.get(dimension)
            judge_emotional = (
                _as_float(scores.get(emotional_field)) if emotional_field else None
            )
            if human_emotional is not None and judge_emotional is not None:
                emotional_compared.append(
                    {
                        "eval_id": eval_id,
                        "abs_diff": abs(human_emotional - judge_emotional),
                    }
                )
                all_disagreements.append(
                    {
                        "category": "emotional_match",
                        "eval_id": eval_id,
                        "dimension": dimension,
                        "human_score": human_emotional,
                        "judge_score": judge_emotional,
                        "abs_diff": abs(human_emotional - judge_emotional),
                        "human_comment": row.get("human_comment", ""),
                    }
                )

            human_drift = _as_float(row.get("drift_onset_turn"))
            judge_drift = _as_float(scores.get("drift_onset_turn"))
            if human_drift is not None and judge_drift is not None:
                drift_compared.append(
                    {
                        "eval_id": eval_id,
                        "abs_diff": abs(human_drift - judge_drift),
                    }
                )
                all_disagreements.append(
                    {
                        "category": "drift_onset_turn",
                        "eval_id": eval_id,
                        "dimension": dimension,
                        "human_score": human_drift,
                        "judge_score": judge_drift,
                        "abs_diff": abs(human_drift - judge_drift),
                        "human_comment": row.get("human_comment", ""),
                    }
                )

            human_failure = str(row.get("failure_type", "")).strip()
            if human_failure:
                judge_failures = scores.get("failure_types") or []
                if isinstance(judge_failures, str):
                    judge_failures = [judge_failures]
                dominant = scores.get("dominant_failure_type")
                judge_failure_set = {str(item) for item in judge_failures if item}
                if dominant:
                    judge_failure_set.add(str(dominant))
                failure_type_compared.append(
                    {
                        "eval_id": eval_id,
                        "failure_type_match": human_failure in judge_failure_set,
                        "human_failure_type": human_failure,
                        "judge_failure_types": sorted(judge_failure_set),
                    }
                )
                if human_failure not in judge_failure_set:
                    all_disagreements.append(
                        {
                            "category": "failure_type",
                            "eval_id": eval_id,
                            "dimension": dimension,
                            "human_score": human_failure,
                            "judge_score": sorted(judge_failure_set),
                            "abs_diff": 1.0,
                            "human_comment": row.get("human_comment", ""),
                        }
                    )

    diffs = [item["abs_diff"] for item in compared]
    disagreements = sorted(
        all_disagreements, key=lambda item: item.get("abs_diff") or 0.0, reverse=True
    )[:10]
    status = (
        "agreement_reported"
        if compared
        or knowledge_compared
        or emotional_compared
        or drift_compared
        or failure_type_compared
        else "labeled"
    )
    report = {
        "human_alignment_status": status,
        "labels_file": str(labels),
        "llm_judge_labels_file": str(align_dir / "llm_judge_labels.json"),
        "agreement_metrics": (
            {
                "persona_fidelity": (
                    {
                        "n": len(compared),
                        "mean_absolute_difference": _mean(diffs),
                        "within_one_point_rate": _mean(
                            [1.0 if diff <= 1.0 else 0.0 for diff in diffs]
                        ),
                    }
                    if compared
                    else None
                ),
                "knowledge_boundary_pass": _numeric_agreement(
                    knowledge_compared, "abs_diff"
                ),
                "emotional_match": _numeric_agreement(emotional_compared, "abs_diff"),
                "drift_onset_turn": _numeric_agreement(drift_compared, "abs_diff"),
                "failure_type": _failure_type_agreement(failure_type_compared),
            }
            if status == "agreement_reported"
            else None
        ),
        "disagreement_examples": disagreements,
    }
    with open(align_dir / "agreement_report.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    with open(align_dir / "disagreement_examples.md", "w", encoding="utf-8") as fh:
        fh.write("# Human Alignment Disagreement Examples\n\n")
        if not disagreements:
            fh.write("No scored human-label disagreements available.\n")
        for item in disagreements:
            fh.write(
                f"- `{item['eval_id']}` ({item.get('category')}): "
                f"human={item.get('human_score')}, judge={item.get('judge_score')}, "
                f"abs_diff={(item.get('abs_diff') or 0):.2f}. "
                f"{item.get('human_comment', '')}\n"
            )
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument("--sample-limit", type=int, default=50)
    parser.add_argument("--compute", action="store_true")
    parser.add_argument("--labels", type=Path, default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    results_dir = Path(args.results_dir) if args.results_dir else None
    if args.compute:
        result = compute_human_agreement(results_dir, labels_path=args.labels)
    else:
        result = init_human_alignment(
            results_dir,
            sample_limit=args.sample_limit,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
