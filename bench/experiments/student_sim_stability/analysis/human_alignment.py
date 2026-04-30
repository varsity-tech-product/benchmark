"""Human quant-expert alignment artifact helpers.

Schema v2 (2026-04-27): expanded to cover S4 ``identified_persona`` (judges
the LLM panel can't be validated on without this), control ``distinctiveness``
and ``persona_set_a`` (placebo-test correctness), S5 ``facet_fit`` +
``expected_signals_hit``, and S3 ``persona_fidelity``.

The agreement report exposes per-judge accuracy (Sonnet alone vs GPT-5.4
alone vs panel-3 consensus) for the categorical fields so the
question "which judge is more reliable" gets a data-backed answer.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from experiments.student_sim_stability.core.paths import (
    BENCH_ROOT,
    default_results_dir,
)

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.core.io_utils import (  # noqa: E402
    atomic_write_json,
    load_json,
)
from experiments.student_sim_stability.core.rubrics import (  # noqa: E402
    DIMENSION_TO_FILE,
    primary_score_field,
)

LABEL_FIELDS = [
    # Identification + provenance
    "eval_id",
    "dimension",
    "source_file",
    "persona_id",
    "task_id",
    "model",
    # Always-applicable scoring
    "persona_fidelity",  # 1-5 numeric, applies to S1/S3/S2/S5/S4
    "knowledge_boundary_pass",  # 1-5, S1
    "emotional_match",  # 1-5, S1/S3
    "drift_onset_turn",  # int, S2
    "failure_type",  # categorical, all dims
    "human_comment",  # free text (Chinese OK)
    # S4-specific
    "human_identified_persona",  # one of: developer_crossover/double_novice/finance_veteran/fullstack_practitioner/uncertain
    "human_b1_confidence",  # 1-5
    # control-specific
    "human_distinctiveness",  # 1-5
    "human_persona_set_a",  # 'true'/'false'/'unknown'
    # S5-specific
    "human_facet_fit",  # 1-5
    "human_facet_signals_hit",  # comma-separated string
]

KNOWLEDGE_FIELD_BY_DIMENSION = {
    "S1": "knowledge_boundary",
}

EMOTIONAL_FIELD_BY_DIMENSION = {
    "S1": "emotional_tone",
    "S3": "emotional_consistency",
}

# Stratified sampling targets (40-sample budget). Persona quotas matter
# because different personas surface different judge weaknesses (e.g.
# fullstack_practitioner is the close-neighbor case for S4 ambiguity).
DEFAULT_SAMPLE_PLAN: dict[str, dict[str, int]] = {
    # dim: { persona_id: count } — all 4 personas covered for S1/S2/S5/S4
    "S1": {
        "developer_crossover": 2,
        "double_novice": 2,
        "finance_veteran": 2,
        "fullstack_practitioner": 2,
    },
    "S3": {
        "developer_crossover": 1,
        "double_novice": 1,
        "finance_veteran": 1,
        "fullstack_practitioner": 1,
    },
    "S2": {
        "developer_crossover": 2,
        "double_novice": 2,
        "finance_veteran": 2,
        "fullstack_practitioner": 2,
    },
    "S6": {
        "developer_crossover": 1,
        "double_novice": 1,
        "finance_veteran": 1,
        "fullstack_practitioner": 1,
    },
    "S5": {
        "developer_crossover": 2,
        "double_novice": 2,
        "finance_veteran": 2,
        "fullstack_practitioner": 2,
    },
    "S4": {
        # fp is overweighted because it is the close-neighbor judge-disagreement
        # case (Sonnet 41% vs GPT-5.4 76% accuracy). Extra fp samples give
        # higher statistical power for the Sonnet-vs-GPT-5.4 question.
        "developer_crossover": 1,
        "double_novice": 1,
        "finance_veteran": 1,
        "fullstack_practitioner": 4,
    },
}

DEFAULT_EXTENSION_KEY_FIELDS = ("dimension", "persona_id", "model")
ALIGNMENT_SAMPLE_FIELDS = frozenset((*LABEL_FIELDS, "judge_input_file"))


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


def _record_numeric(
    *,
    category: str,
    eval_id: str,
    dimension: str,
    bucket: list[dict],
    bucket_extra: dict | None,
    human_score: float | None,
    judge_score: float | None,
    human_comment: str,
    disagreements: list[dict],
) -> None:
    """Append matched human/judge numeric scores to ``bucket`` + ``disagreements``.

    No-op if either score is missing. ``bucket_extra`` is merged into the
    bucket entry (used by ``persona_fidelity`` to also carry ``dimension``).
    """
    if human_score is None or judge_score is None:
        return
    diff = abs(human_score - judge_score)
    entry = {"eval_id": eval_id, "abs_diff": diff}
    if bucket_extra:
        entry.update(bucket_extra)
    bucket.append(entry)
    disagreements.append(
        {
            "category": category,
            "eval_id": eval_id,
            "dimension": dimension,
            "human_score": human_score,
            "judge_score": judge_score,
            "abs_diff": diff,
            "human_comment": human_comment,
        }
    )


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


def _stratified_sample(
    input_dir: Path, plan: dict[str, dict[str, int]], seed: int = 42
) -> list[dict]:
    """Pick samples per (dimension, persona) quota from the rendered judge
    inputs. For S4 specifically, prioritise samples where the two-judge panel
    disagrees most (read identified_persona_by_judge from the corresponding
    primary aggregate if available) — these are the most informative cases.
    """
    rng = random.Random(seed)
    payloads: dict[tuple[str, str], list[tuple[Path, dict]]] = defaultdict(list)
    for path in sorted(input_dir.glob("*.json")):
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        dim = str(payload.get("dimension", ""))
        persona = str((payload.get("metadata") or {}).get("persona_id", ""))
        if dim and persona:
            payloads[(dim, persona)].append((path, payload))

    # For S4: rank candidates so that fp samples where Sonnet and GPT-5.4
    # disagreed are chosen first. Falls back to random if no aggregate yet.
    b1_priority: dict[str, int] = {}
    eval_path = input_dir.parent / "evaluations" / "all_evaluations.json"
    if eval_path.exists():
        try:
            ag = load_json(eval_path)
            for r in ag.get("S4", []):
                by_judge = (r.get("scores") or {}).get(
                    "identified_persona_by_judge"
                ) or {}
                s = str(by_judge.get("sonnet", "")).strip()
                g = str(by_judge.get("gpt54", "")).strip()
                expected = str((r.get("metadata") or {}).get("persona_id", ""))
                # Score 2 if disagree, 1 if agree-but-wrong, 0 otherwise
                if s and g and s != g:
                    b1_priority[r.get("eval_id", "")] = 2
                elif s and s != expected:
                    b1_priority[r.get("eval_id", "")] = 1
        except (json.JSONDecodeError, OSError, KeyError, TypeError):
            # Sampling falls back to random ordering on any read/schema error;
            # specific catch keeps real bugs (e.g. AttributeError) loud.
            pass

    selected: list[tuple[Path, dict]] = []
    for (dim, persona), wanted in (
        ((d, p), n)
        for d, persona_quotas in plan.items()
        for p, n in persona_quotas.items()
    ):
        bucket = payloads.get((dim, persona), [])
        if not bucket:
            continue
        if dim == "S4":
            # Sort by descending priority then deterministic eval_id for ties
            bucket = sorted(
                bucket,
                key=lambda x: (
                    -b1_priority.get(x[1].get("eval_id", ""), 0),
                    x[1].get("eval_id", ""),
                ),
            )
            chosen = bucket[:wanted]
        else:
            shuffled = list(bucket)
            rng.shuffle(shuffled)
            chosen = shuffled[:wanted]
        selected.extend(chosen)

    # Build out the per-sample dicts
    samples = []
    for path, payload in selected:
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


def _valid_dimensions_label() -> str:
    return ", ".join(DIMENSION_TO_FILE)


def parse_alignment_target_spec(
    target_spec: str,
    *,
    dimension: str | None = None,
) -> dict[str, int]:
    """Parse the D2 ``--target`` grammar into ``{dimension: quota}``."""
    valid_dims = set(DIMENSION_TO_FILE)
    chosen_dimension = dimension.strip() if dimension else None
    if chosen_dimension and chosen_dimension not in valid_dims:
        raise ValueError(
            f"unknown --dimension {chosen_dimension!r}; expected one of "
            f"{_valid_dimensions_label()}"
        )

    spec = target_spec.strip()
    if not spec:
        raise ValueError("--target must not be empty")

    def parse_quota(raw: str, context: str) -> int:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(
                f"invalid --target quota for {context}: {raw!r}; expected integer"
            ) from exc
        if value < 0:
            raise ValueError(
                f"invalid --target quota for {context}: {raw!r}; expected >= 0"
            )
        return value

    if "=" not in spec:
        if not chosen_dimension:
            raise ValueError(
                "--target must use DIM=N[,DIM=N...] when --dimension is omitted"
            )
        return {chosen_dimension: parse_quota(spec, chosen_dimension)}

    targets: dict[str, int] = {}
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            raise ValueError("--target contains an empty DIM=N entry")
        if "=" not in part:
            raise ValueError(
                f"invalid --target entry {part!r}; expected DIM=N comma form"
            )
        dim_raw, quota_raw = part.split("=", 1)
        dim = dim_raw.strip()
        if dim in targets:
            raise ValueError(f"duplicate dimension in --target: {dim}")
        if dim not in valid_dims:
            raise ValueError(
                f"unknown dimension in --target: {dim!r}; expected one of "
                f"{_valid_dimensions_label()}"
            )
        targets[dim] = parse_quota(quota_raw.strip(), dim)

    if chosen_dimension and set(targets) != {chosen_dimension}:
        got = ", ".join(targets)
        raise ValueError(
            "--dimension/--target mismatch: "
            f"--dimension {chosen_dimension} requires --target {chosen_dimension}=N; "
            f"got {got}"
        )
    return targets


def parse_alignment_key_fields(key_fields: str | Iterable[str]) -> tuple[str, ...]:
    if isinstance(key_fields, str):
        fields = tuple(
            field.strip() for field in key_fields.split(",") if field.strip()
        )
    else:
        fields = tuple(str(field).strip() for field in key_fields if str(field).strip())
    if not fields:
        raise ValueError("--key-fields must include at least one field")
    if len(fields) > 3:
        raise ValueError("--key-fields accepts at most three comma-separated fields")
    duplicates = sorted({field for field in fields if fields.count(field) > 1})
    if duplicates:
        raise ValueError(f"duplicate field in --key-fields: {', '.join(duplicates)}")
    unknown = [field for field in fields if field not in ALIGNMENT_SAMPLE_FIELDS]
    if unknown:
        allowed = ", ".join(sorted(ALIGNMENT_SAMPLE_FIELDS))
        raise ValueError(
            f"unknown field in --key-fields: {', '.join(unknown)}; "
            f"expected one of {allowed}"
        )
    return fields


def _alignment_sample_from_input(path: Path, payload: dict) -> dict | None:
    metadata = payload.get("metadata") or {}
    dimension = str(payload.get("dimension") or "").strip()
    eval_id = str(payload.get("eval_id") or path.stem).strip()
    if not (dimension and eval_id):
        return None
    return {
        "eval_id": eval_id,
        "dimension": dimension,
        "source_file": metadata.get("source_file")
        or metadata.get("persona_source_file")
        or "",
        "persona_id": metadata.get("persona_id") or "",
        "task_id": metadata.get("task_id") or "",
        "model": metadata.get("model") or "",
        "judge_input_file": path.name,
    }


def _cell_key(sample: dict, key_fields: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(str(sample.get(field) or "") for field in key_fields)


def _cell_payload(key_fields: tuple[str, ...], key: tuple[str, ...]) -> dict[str, str]:
    return dict(zip(key_fields, key))


def _read_human_label_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"human label CSV not found: {path}")
    with open(path, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        header = list(reader.fieldnames or LABEL_FIELDS)
        rows = [dict(row) for row in reader]
    return header, rows


def _write_human_label_csv(
    path: Path,
    *,
    header: list[str],
    existing_rows: list[dict[str, str]],
    new_samples: list[dict],
) -> int:
    existing_csv_eids = {str(row.get("eval_id") or "") for row in existing_rows}
    appended = 0
    rows = list(existing_rows)
    for sample in new_samples:
        if sample["eval_id"] in existing_csv_eids:
            continue
        row = {col: "" for col in header}
        for col in header:
            if col in sample:
                row[col] = sample.get(col) or ""
        rows.append(row)
        existing_csv_eids.add(sample["eval_id"])
        appended += 1

    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)
    return appended


def write_llm_label_snapshot(out: Path, samples: list[dict]) -> dict:
    """Snapshot the LLM judges' labels for the chosen samples — three judges
    if available, plus the panel-3 mean already computed in the aggregate.
    """
    primary_dir = out / "judge_outputs"
    by_model_dir = out / "judge_outputs_by_model"
    aggregate_path = out / "evaluations" / "all_evaluations.json"
    aggregate = load_json(aggregate_path) if aggregate_path.exists() else {}
    aggregate_by_eval = {
        r.get("eval_id"): r
        for records in aggregate.values()
        if isinstance(records, list)
        for r in records
    }

    rows = []
    missing = []
    for sample in samples:
        eval_id = sample["eval_id"]
        primary_path = primary_dir / f"{eval_id}.json"
        if not primary_path.exists():
            missing.append(eval_id)
            continue
        primary = load_json(primary_path)
        per_judge: dict[str, dict] = {}
        for judge_dir_name, label in (
            ("anthropic__claude-sonnet-4-6", "sonnet"),
            ("openai__gpt-5_4", "gpt54"),
            ("google__gemini-3_1-pro-preview", "gemini"),
        ):
            jp = by_model_dir / judge_dir_name / f"{eval_id}.json"
            if jp.exists():
                per_judge[label] = load_json(jp).get("scores") or {}
        agg_record = aggregate_by_eval.get(eval_id) or {}
        rows.append(
            {
                "eval_id": eval_id,
                "dimension": sample["dimension"],
                "judge_input_file": sample["judge_input_file"],
                "rubric_id": primary.get("rubric_id"),
                "panel_3_scores": agg_record.get("scores") or primary.get("scores"),
                "scores_by_judge": per_judge,
            }
        )

    snapshot = {
        "sample_count": len(samples),
        "llm_label_count": len(rows),
        "missing_judge_outputs": missing,
        "labels": rows,
    }
    align_dir = out / "human_alignment"
    atomic_write_json(align_dir / "llm_judge_labels.json", snapshot)
    return snapshot


def extend_alignment_pool(
    results_dir: Path | None = None,
    *,
    target: str,
    key_fields: str | Iterable[str] = DEFAULT_EXTENSION_KEY_FIELDS,
    dimension: str | None = None,
    seed: int = 2026,
    dry_run: bool = False,
) -> dict:
    """Extend human-alignment samples to per-dimension per-cell targets.

    Target parsing follows the locked D2 CLI semantics: ``target`` becomes a
    canonical ``{dimension: per_cell_quota}`` map, while ``key_fields`` only
    controls cell composition inside each dimension.
    """
    out = results_dir or default_results_dir()
    if not out.is_absolute():
        out = BENCH_ROOT / out
    align_dir = out / "human_alignment"
    input_dir = out / "judge_inputs"
    manifest_path = align_dir / "sample_manifest.json"
    csv_path = align_dir / "human_label_template.csv"

    target_per_cell = parse_alignment_target_spec(target, dimension=dimension)
    parsed_key_fields = parse_alignment_key_fields(key_fields)

    if not manifest_path.exists():
        raise FileNotFoundError(f"human alignment manifest not found: {manifest_path}")
    if not input_dir.exists():
        raise FileNotFoundError(f"judge input directory not found: {input_dir}")

    manifest = load_json(manifest_path)
    existing_samples = list(manifest.get("samples") or [])
    existing_ids = {str(sample.get("eval_id") or "") for sample in existing_samples}

    existing_count: dict[tuple[str, tuple[str, ...]], int] = defaultdict(int)
    known_cells: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for sample in existing_samples:
        dim = str(sample.get("dimension") or "").strip()
        if dim not in target_per_cell:
            continue
        key = _cell_key(sample, parsed_key_fields)
        known_cells[dim].add(key)
        existing_count[(dim, key)] += 1

    candidate_pool: dict[tuple[str, tuple[str, ...]], list[dict]] = defaultdict(list)
    for path in sorted(input_dir.glob("*.json")):
        try:
            payload = load_json(path)
        except (OSError, json.JSONDecodeError):
            continue
        sample = _alignment_sample_from_input(path, payload)
        if not sample:
            continue
        dim = sample["dimension"]
        if dim not in target_per_cell:
            continue
        key = _cell_key(sample, parsed_key_fields)
        known_cells[dim].add(key)
        if sample["eval_id"] not in existing_ids:
            candidate_pool[(dim, key)].append(sample)

    rng = random.Random(seed)
    new_samples: list[dict] = []
    cell_reports: list[dict] = []
    warnings: list[str] = []
    for dim, target_n in target_per_cell.items():
        for key in sorted(known_cells.get(dim, set())):
            existing_n = existing_count.get((dim, key), 0)
            needed = max(0, target_n - existing_n)
            pool = candidate_pool.get((dim, key), [])
            selected: list[dict] = []
            status = "ok"
            if needed > 0:
                if not pool:
                    status = "no_candidates"
                    warnings.append(
                        "no candidates for "
                        f"dimension={dim} cell={_cell_payload(parsed_key_fields, key)} "
                        f"(existing={existing_n}, need={needed})"
                    )
                else:
                    shuffled = list(pool)
                    rng.shuffle(shuffled)
                    selected = shuffled[: min(needed, len(shuffled))]
                    new_samples.extend(selected)
                    status = "would_add" if len(selected) == needed else "shortfall"
                    if len(selected) < needed:
                        warnings.append(
                            "pool shortfall for "
                            f"dimension={dim} cell={_cell_payload(parsed_key_fields, key)} "
                            f"(existing={existing_n}, need={needed}, "
                            f"available={len(pool)}, selected={len(selected)})"
                        )
            cell_reports.append(
                {
                    "dimension": dim,
                    "cell": _cell_payload(parsed_key_fields, key),
                    "existing": existing_n,
                    "target": target_n,
                    "needed": needed,
                    "available_candidates": len(pool),
                    "selected": len(selected),
                    "status": status,
                }
            )

    for warning in warnings:
        print(f"WARNING: {warning}", file=sys.stderr)

    combined_samples = existing_samples + new_samples
    by_dimension: dict[str, dict[str, int]] = {}
    for dim in target_per_cell:
        existing_dim = sum(
            1 for sample in existing_samples if sample.get("dimension") == dim
        )
        added_dim = sum(1 for sample in new_samples if sample.get("dimension") == dim)
        by_dimension[dim] = {
            "existing": existing_dim,
            "added": added_dim,
            "final": existing_dim + added_dim,
        }

    report = {
        "human_alignment_status": "extension_dry_run" if dry_run else "extended",
        "schema_version": "human_alignment_v2",
        "results_dir": str(out),
        "dry_run": dry_run,
        "target_per_cell": target_per_cell,
        "key_fields": list(parsed_key_fields),
        "dimension": dimension,
        "seed": seed,
        "existing_sample_count": len(existing_samples),
        "new_sample_count": len(new_samples),
        "final_sample_count": len(combined_samples),
        "by_dimension": by_dimension,
        "warnings": warnings,
        "cells": cell_reports,
        "writes": {
            "sample_manifest": False,
            "human_label_template": False,
            "llm_judge_labels": False,
        },
    }
    if dry_run:
        return report

    header, existing_rows = _read_human_label_csv(csv_path)
    new_manifest = dict(manifest)
    new_manifest.update(
        {
            "human_alignment_status": "sampled",
            "schema_version": "human_alignment_v2",
            "sample_limit": len(combined_samples),
            "sample_count": len(combined_samples),
            "extension_target_per_cell": target_per_cell,
            "extension_key_fields": list(parsed_key_fields),
            "extension_dimension": dimension,
            "extension_seed": seed,
            "extension_added": len(new_samples),
            "samples": combined_samples,
        }
    )
    atomic_write_json(manifest_path, new_manifest)
    appended = _write_human_label_csv(
        csv_path,
        header=header,
        existing_rows=existing_rows,
        new_samples=new_samples,
    )
    snapshot = write_llm_label_snapshot(out, combined_samples)
    report["writes"] = {
        "sample_manifest": str(manifest_path),
        "human_label_template": str(csv_path),
        "llm_judge_labels": str(align_dir / "llm_judge_labels.json"),
    }
    report["csv_rows_appended"] = appended
    report["llm_label_count"] = snapshot["llm_label_count"]
    report["missing_judge_outputs"] = snapshot["missing_judge_outputs"]
    return report


def init_human_alignment(
    results_dir: Path | None = None,
    sample_limit: int = 40,
    overwrite: bool = False,
    sample_plan: dict | None = None,
) -> dict:
    out = results_dir or default_results_dir()
    if not out.is_absolute():
        out = BENCH_ROOT / out
    align_dir = out / "human_alignment"
    align_dir.mkdir(parents=True, exist_ok=True)

    input_dir = out / "judge_inputs"
    plan = sample_plan or DEFAULT_SAMPLE_PLAN
    samples = _stratified_sample(input_dir, plan)
    llm_snapshot = write_llm_label_snapshot(out, samples)

    status = "sampled" if samples else "not_run"
    manifest = {
        "human_alignment_status": status,
        "schema_version": "human_alignment_v2",
        "sample_limit": sample_limit,
        "sample_count": len(samples),
        "sampling_policy": (
            "stratified per (dimension, persona) per DEFAULT_SAMPLE_PLAN; "
            "S4 prioritises judge-disagreement cases when available"
        ),
        "llm_judge_label_count": llm_snapshot["llm_label_count"],
        "samples": samples,
    }
    atomic_write_json(align_dir / "sample_manifest.json", manifest)

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
        "schema_version": "human_alignment_v2",
        "agreement_metrics": None,
        "disagreement_examples": [],
        "notes": "Fill human_label_template.csv and run `cli human-alignment --compute` to produce metrics.",
    }
    agreement_path = align_dir / "agreement_report.json"
    if overwrite or not agreement_path.exists():
        atomic_write_json(agreement_path, agreement_stub)
    disagreement_path = align_dir / "disagreement_examples.md"
    if overwrite or not disagreement_path.exists():
        with open(disagreement_path, "w", encoding="utf-8") as fh:
            fh.write(
                "# Human Alignment Disagreement Examples\n\nStatus: not labeled.\n"
            )

    return manifest


# Mapping from human label CSV column → (judge score field by dimension).
# Used for per-judge alignment on the numeric fields that can be paired
# across (human label, judge raw score). The dimension key set is exactly
# the dimensions where the rubric exposes a comparable scalar.
_PER_JUDGE_ALIGNMENT_SPECS: list[tuple[str, str, dict[str, str]]] = [
    (
        "persona_fidelity",
        "persona_fidelity",
        {
            "S1": "overall",
            "S3": "overall_reproducibility",
            "S2": "overall_drift_score",
            "S5": "overall_probe_pass",
            "S4": "contract_fit",
        },
    ),
    (
        "knowledge_boundary_pass",
        "knowledge_boundary_pass",
        {"S1": "knowledge_boundary"},
    ),
    (
        "emotional_match",
        "emotional_match",
        {"S1": "emotional_tone", "S3": "emotional_consistency"},
    ),
    (
        "p1_facet_fit",
        "human_facet_fit",
        {"S5": "facet_fit"},
    ),
    (
        "control_distinctiveness",
        "human_distinctiveness",
        {"S6": "distinctiveness"},
    ),
]


def _compute_per_judge_alignment(
    labels_path: Path,
    llm_snapshot_path: Path,
) -> dict:
    """Per-judge MAD / within-1pt / n for each (numeric field, dimension).

    The aggregate-level metrics in ``compute_human_agreement`` collapse all
    judges into a single Panel-3 / primary score, which is fine for a
    "do judges roughly track humans" description but useless for the
    "which judge tracks humans best" question. This helper reads each
    judge's raw score from the ``llm_judge_labels.json`` snapshot
    (``scores_by_judge``) and pairs it against the human-labelled CSV row
    on a per-field, per-dimension, per-judge basis.

    Returns ``{field: {dimension: {judge_label: {n, mean_absolute_difference,
    within_one_point_rate}}}}`` — empty when the inputs are missing.
    """
    if not (labels_path.exists() and llm_snapshot_path.exists()):
        return {}
    snapshot = load_json(llm_snapshot_path) or {}
    labels_by_eval = {
        item.get("eval_id"): item for item in snapshot.get("labels", []) or []
    }
    bucket: dict[str, dict[str, dict[str, list[float]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    with open(labels_path, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            eid = str(row.get("eval_id") or "").strip()
            dim = str(row.get("dimension") or "").strip()
            label_data = labels_by_eval.get(eid)
            if not label_data:
                continue
            sbj = label_data.get("scores_by_judge") or {}
            for field_name, csv_col, dim_to_judge_field in _PER_JUDGE_ALIGNMENT_SPECS:
                judge_field = dim_to_judge_field.get(dim)
                if not judge_field:
                    continue
                hv = _as_float(row.get(csv_col))
                if hv is None:
                    continue
                for j_label in ("sonnet", "gpt54", "gemini"):
                    j_scores = sbj.get(j_label) or {}
                    jv = _as_float(j_scores.get(judge_field))
                    if jv is None:
                        continue
                    bucket[field_name][dim][j_label].append(abs(hv - jv))
    out: dict = {}
    for field_name, by_dim in bucket.items():
        out[field_name] = {}
        for dim, by_judge in by_dim.items():
            out[field_name][dim] = {}
            for j_label, diffs in by_judge.items():
                n = len(diffs)
                if n == 0:
                    continue
                out[field_name][dim][j_label] = {
                    "n": n,
                    "mean_absolute_difference": sum(diffs) / n,
                    "within_one_point_rate": sum(1 for d in diffs if d <= 1.0) / n,
                }
    return out


def _per_judge_b1_match(
    eval_id: str,
    expected_human: str,
    aggregate_by_eval: dict,
    by_model_dir: Path,
) -> dict:
    """Compare human's identified_persona to each judge's identified_persona
    on a S4 sample. Returns dict with per-judge match (bool) for the three
    panel members."""
    out: dict[str, bool | None] = {"sonnet": None, "gpt54": None, "gemini": None}
    # Try aggregate first (panel-3 has identified_persona_by_judge)
    rec = aggregate_by_eval.get(eval_id) or {}
    by_judge = (rec.get("scores") or {}).get("identified_persona_by_judge") or {}
    for label in ("sonnet", "gpt54", "gemini"):
        v = str(by_judge.get(label, "")).strip()
        if v:
            out[label] = v == expected_human
    # Fall back to by_model dirs for fields not in aggregate
    for label, dir_name in (
        ("sonnet", "anthropic__claude-sonnet-4-6"),
        ("gpt54", "openai__gpt-5_4"),
        ("gemini", "google__gemini-3_1-pro-preview"),
    ):
        if out[label] is not None:
            continue
        jp = by_model_dir / dir_name / f"{eval_id}.json"
        if jp.exists():
            payload = load_json(jp)
            judged = str(
                (payload.get("scores") or {}).get("identified_persona", "")
            ).strip()
            if judged:
                out[label] = judged == expected_human
    return out


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
    by_model_dir = out / "judge_outputs_by_model"
    align_dir.mkdir(parents=True, exist_ok=True)

    if not labels.exists() or not eval_path.exists():
        report = {
            "human_alignment_status": (
                "sampled"
                if (align_dir / "sample_manifest.json").exists()
                else "not_run"
            ),
            "schema_version": "human_alignment_v2",
            "agreement_metrics": None,
            "disagreement_examples": [],
            "notes": "Human label file or aggregate evaluation file is missing.",
        }
        atomic_write_json(align_dir / "agreement_report.json", report)
        return report

    aggregate = load_json(eval_path)
    by_eval_id = {
        record.get("eval_id"): record
        for records in aggregate.values()
        if isinstance(records, list)
        for record in records
    }

    # Comparison buckets — each list collects per-eval comparisons
    persona_fid: list[dict] = []
    knowledge: list[dict] = []
    emotional: list[dict] = []
    drift: list[dict] = []
    failure_match: list[dict] = []
    # New v2 buckets
    b1_per_judge: list[dict] = (
        []
    )  # {eval_id, persona_id, sonnet_match, gpt54_match, gemini_match}
    control_dist: list[dict] = []  # numeric agreement
    control_set_a: list[dict] = []  # per-judge accuracy
    p1_facet_fit: list[dict] = []  # numeric agreement
    p1_signals_recall: list[dict] = []  # set recall (human hits / judge expected)

    all_disagreements: list[dict] = []

    with open(labels, encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            eval_id = str(row.get("eval_id") or "")
            record = by_eval_id.get(eval_id)
            if not record:
                continue
            dimension = (
                record.get("metadata", {}).get("dimension")
                or record.get("eval_id", "").split("__", 1)[0]
            )
            scores = record.get("scores", {})

            comment = row.get("human_comment", "")

            # --- persona_fidelity (numeric, multiple dims) ---
            try:
                primary_field = primary_score_field(dimension)
            except KeyError:
                primary_field = None
            _record_numeric(
                category="persona_fidelity",
                eval_id=eval_id,
                dimension=dimension,
                bucket=persona_fid,
                bucket_extra={"dimension": dimension},
                human_score=_as_float(row.get("persona_fidelity")),
                judge_score=(
                    _as_float(scores.get(primary_field)) if primary_field else None
                ),
                human_comment=comment,
                disagreements=all_disagreements,
            )

            # --- knowledge_boundary_pass (S1 only) ---
            kf = KNOWLEDGE_FIELD_BY_DIMENSION.get(dimension)
            _record_numeric(
                category="knowledge_boundary_pass",
                eval_id=eval_id,
                dimension=dimension,
                bucket=knowledge,
                bucket_extra=None,
                human_score=_as_float(row.get("knowledge_boundary_pass")),
                judge_score=_as_float(scores.get(kf)) if kf else None,
                human_comment=comment,
                disagreements=all_disagreements,
            )

            # --- emotional_match (S1/S3) ---
            ef = EMOTIONAL_FIELD_BY_DIMENSION.get(dimension)
            _record_numeric(
                category="emotional_match",
                eval_id=eval_id,
                dimension=dimension,
                bucket=emotional,
                bucket_extra=None,
                human_score=_as_float(row.get("emotional_match")),
                judge_score=_as_float(scores.get(ef)) if ef else None,
                human_comment=comment,
                disagreements=all_disagreements,
            )

            # --- drift_onset_turn (S2) ---
            _record_numeric(
                category="drift_onset_turn",
                eval_id=eval_id,
                dimension=dimension,
                bucket=drift,
                bucket_extra=None,
                human_score=_as_float(row.get("drift_onset_turn")),
                judge_score=_as_float(scores.get("drift_onset_turn")),
                human_comment=comment,
                disagreements=all_disagreements,
            )

            # --- failure_type ---
            human_f = str(row.get("failure_type", "")).strip()
            if human_f:
                judge_failures = scores.get("failure_types") or []
                if isinstance(judge_failures, str):
                    judge_failures = [judge_failures]
                dominant = scores.get("dominant_failure_type")
                judge_set = {str(item) for item in judge_failures if item}
                if dominant:
                    judge_set.add(str(dominant))
                match = human_f in judge_set
                failure_match.append(
                    {
                        "eval_id": eval_id,
                        "failure_type_match": match,
                        "human_failure_type": human_f,
                        "judge_failure_types": sorted(judge_set),
                    }
                )
                if not match:
                    all_disagreements.append(
                        {
                            "category": "failure_type",
                            "eval_id": eval_id,
                            "dimension": dimension,
                            "human_score": human_f,
                            "judge_score": sorted(judge_set),
                            "abs_diff": 1.0,
                            "human_comment": row.get("human_comment", ""),
                        }
                    )

            # --- S4 identified_persona (per-judge accuracy) ---
            if dimension == "S4":
                human_id = str(row.get("human_identified_persona", "")).strip()
                if human_id and human_id != "uncertain":
                    matches = _per_judge_b1_match(
                        eval_id,
                        human_id,
                        by_eval_id,
                        by_model_dir,
                    )
                    b1_per_judge.append(
                        {
                            "eval_id": eval_id,
                            "expected_human": human_id,
                            "true_persona_id": record.get("metadata", {}).get(
                                "persona_id"
                            ),
                            **matches,
                        }
                    )

            # --- control distinctiveness (numeric) + persona_set_a (categorical) ---
            if dimension == "S6":
                _record_numeric(
                    category="control_distinctiveness",
                    eval_id=eval_id,
                    dimension=dimension,
                    bucket=control_dist,
                    bucket_extra=None,
                    human_score=_as_float(row.get("human_distinctiveness")),
                    judge_score=_as_float(scores.get("distinctiveness")),
                    human_comment=comment,
                    disagreements=all_disagreements,
                )
                # persona_set_a check (was the human right about which set was conditioned?)
                human_a = str(row.get("human_persona_set_a", "")).strip().lower()
                if human_a in ("true", "false"):
                    human_bool = human_a == "true"
                    truth = bool(record.get("metadata", {}).get("persona_is_set_a"))
                    control_set_a.append(
                        {
                            "eval_id": eval_id,
                            "human_correct": (human_bool == truth),
                        }
                    )

            # --- S5 facet_fit + expected_signals recall ---
            if dimension == "S5":
                _record_numeric(
                    category="p1_facet_fit",
                    eval_id=eval_id,
                    dimension=dimension,
                    bucket=p1_facet_fit,
                    bucket_extra=None,
                    human_score=_as_float(row.get("human_facet_fit")),
                    judge_score=_as_float(scores.get("facet_fit")),
                    human_comment=comment,
                    disagreements=all_disagreements,
                )
                human_signals = [
                    s.strip()
                    for s in row.get("human_facet_signals_hit", "").split(",")
                    if s.strip()
                ]
                expected = (record.get("metadata") or {}).get("expected_signals") or []
                if human_signals and expected:
                    expected_set = {s.strip().lower() for s in expected if s}
                    human_set = {s.lower() for s in human_signals}
                    recall = (
                        len(human_set & expected_set) / len(expected_set)
                        if expected_set
                        else 0.0
                    )
                    p1_signals_recall.append({"eval_id": eval_id, "recall": recall})

    # ------------------------------------------------------------------
    # Aggregate metrics
    # ------------------------------------------------------------------
    def _b1_judge_accuracy(view: str) -> dict | None:
        vals = [r[view] for r in b1_per_judge if r.get(view) is not None]
        if not vals:
            return None
        return {
            "n": len(vals),
            "accuracy_vs_human": _mean([1.0 if v else 0.0 for v in vals]),
        }

    metrics = {
        "persona_fidelity": _numeric_agreement(persona_fid, "abs_diff"),
        "knowledge_boundary_pass": _numeric_agreement(knowledge, "abs_diff"),
        "emotional_match": _numeric_agreement(emotional, "abs_diff"),
        "drift_onset_turn": _numeric_agreement(drift, "abs_diff"),
        "failure_type": _failure_type_agreement(failure_match),
        # Per-judge S4 vs human (the load-bearing question)
        "b1_identification": {
            "sonnet_vs_human": _b1_judge_accuracy("sonnet"),
            "gpt54_vs_human": _b1_judge_accuracy("gpt54"),
            "gemini_vs_human": _b1_judge_accuracy("gemini"),
            "n": len(b1_per_judge),
        },
        "control_distinctiveness": _numeric_agreement(control_dist, "abs_diff"),
        "control_persona_set_a_accuracy": (
            {
                "n": len(control_set_a),
                "accuracy": _mean(
                    [1.0 if r["human_correct"] else 0.0 for r in control_set_a]
                ),
            }
            if control_set_a
            else None
        ),
        "p1_facet_fit": _numeric_agreement(p1_facet_fit, "abs_diff"),
        "p1_expected_signals_recall": (
            {
                "n": len(p1_signals_recall),
                "mean_recall": _mean([r["recall"] for r in p1_signals_recall]),
            }
            if p1_signals_recall
            else None
        ),
        "per_judge_alignment": _compute_per_judge_alignment(
            labels, align_dir / "llm_judge_labels.json"
        ),
    }

    has_data = any(
        v
        for v in (
            persona_fid,
            knowledge,
            emotional,
            drift,
            failure_match,
            b1_per_judge,
            control_dist,
            control_set_a,
            p1_facet_fit,
            p1_signals_recall,
        )
    )

    disagreements = sorted(
        all_disagreements,
        key=lambda item: item.get("abs_diff") or 0.0,
        reverse=True,
    )[:15]

    report = {
        "human_alignment_status": "agreement_reported" if has_data else "labeled",
        "schema_version": "human_alignment_v2",
        "labels_file": str(labels),
        "llm_judge_labels_file": str(align_dir / "llm_judge_labels.json"),
        "agreement_metrics": metrics if has_data else None,
        "b1_breakdown_by_persona": _b1_breakdown_by_persona(b1_per_judge),
        "disagreement_examples": disagreements,
    }
    atomic_write_json(align_dir / "agreement_report.json", report)
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


def _b1_breakdown_by_persona(rows: list[dict]) -> dict:
    """Per-persona S4 per-judge accuracy (the breakdown that exposes
    persona-specific judge weakness)."""
    by_persona: dict[str, dict] = defaultdict(
        lambda: {
            "n": 0,
            "sonnet_correct": 0,
            "gpt54_correct": 0,
            "gemini_correct": 0,
        }
    )
    for r in rows:
        p = r.get("expected_human") or r.get("true_persona_id") or "unknown"
        by_persona[p]["n"] += 1
        if r.get("sonnet"):
            by_persona[p]["sonnet_correct"] += 1
        if r.get("gpt54"):
            by_persona[p]["gpt54_correct"] += 1
        if r.get("gemini"):
            by_persona[p]["gemini_correct"] += 1
    return {
        p: {
            "n": d["n"],
            "sonnet_accuracy": d["sonnet_correct"] / d["n"] if d["n"] else None,
            "gpt54_accuracy": d["gpt54_correct"] / d["n"] if d["n"] else None,
            "gemini_accuracy": d["gemini_correct"] / d["n"] if d["n"] else None,
        }
        for p, d in by_persona.items()
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument("--sample-limit", type=int, default=40)
    parser.add_argument("--compute", action="store_true")
    parser.add_argument("--labels", type=Path, default=None)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    results_dir = Path(args.results_dir) if args.results_dir else None
    if args.compute:
        result = compute_human_agreement(results_dir, labels_path=args.labels)
    else:
        result = init_human_alignment(
            results_dir,
            sample_limit=args.sample_limit,
            overwrite=args.overwrite,
        )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
