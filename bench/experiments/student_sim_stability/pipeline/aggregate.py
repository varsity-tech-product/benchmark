"""Aggregate stability judge outputs into the report input JSON."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Iterable

from experiments.student_sim_stability.core.io_utils import (
    atomic_write_json,
    load_json,
    safe_model_dir,
)
from experiments.student_sim_stability.core.paths import BENCH_ROOT, default_results_dir

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.core.config import (  # noqa: E402
    JUDGE_LABELS,
    PANEL_JUDGES,
    expected_artifact_counts,
)
from experiments.student_sim_stability.core.rubrics import (
    DIMENSION_TO_FILE,
    numeric_score_fields,
    required_score_keys,
)


def _dimension_counts(path: Path) -> dict[str, int]:
    counts = {dim: 0 for dim in DIMENSION_TO_FILE}
    if not path.exists():
        return counts
    for entry in path.iterdir():
        if entry.suffix != ".json":
            continue
        prefix = entry.name.split("__", 1)[0]
        if prefix in counts:
            counts[prefix] += 1
    return counts


def _validate_strict_input_output_sets(
    judge_input_dir: Path,
    judge_output_dir: Path,
    profile: str,
) -> None:
    input_names = {path.name for path in judge_input_dir.glob("*.json")}
    output_names = {path.name for path in judge_output_dir.glob("*.json")}
    missing = sorted(input_names - output_names)
    extra = sorted(output_names - input_names)
    if missing:
        raise ValueError(f"Missing judge outputs for inputs: {missing[:5]}")
    if extra:
        raise ValueError(f"Judge outputs without matching inputs: {extra[:5]}")
    if profile == "full":
        counts = _dimension_counts(judge_input_dir)
        base = expected_artifact_counts("full")
        errors = []
        # S1 is allowed to be either the sampled subset or the full set.
        if counts["S1"] not in {base["S1_sample"], base["S1_full"]}:
            errors.append(
                f"S1 inputs={counts['S1']}/"
                f"{base['S1_sample']} sample or {base['S1_full']} full"
            )
        for dim in ("S3", "S2", "S6", "S5", "S4"):
            if counts[dim] != base[dim]:
                errors.append(f"{dim} inputs={counts[dim]}/{base[dim]}")
        if errors:
            raise ValueError(
                "Strict full aggregate has incomplete inputs: " + "; ".join(errors)
            )


def _build_input_metadata_index(input_dir: Path) -> dict[str, dict]:
    """Pre-load every judge input's metadata block keyed by filename.

    The aggregate stage reads each input file at most once instead of doing a
    fresh ``exists()`` + ``load_json`` per output file.
    """
    if not input_dir.exists():
        return {}
    return {
        path.name: (load_json(path).get("metadata") or {})
        for path in sorted(input_dir.glob("*.json"))
    }


def _per_turn_field(scores: dict, field: str) -> list[float]:
    existing = scores.get(f"per_turn_{field}")
    if isinstance(existing, list):
        return existing
    per_turn = scores.get("per_turn", [])
    if not isinstance(per_turn, list):
        return []
    return [turn.get(field, 0) for turn in per_turn if isinstance(turn, dict)]


def _normalize_d3_scores(scores: dict) -> dict:
    normalized = dict(scores)
    normalized["per_turn_fidelity"] = _per_turn_field(scores, "persona_fidelity")
    normalized["per_turn_knowledge_leak"] = _per_turn_field(scores, "knowledge_leak")
    normalized["per_turn_co_teacher_drift"] = _per_turn_field(
        scores, "co_teacher_drift"
    )
    return normalized


def _validate_score_schema(dimension: str, scores: object, eval_id: str) -> None:
    if not isinstance(scores, dict):
        raise ValueError(f"{eval_id}: scores must be a JSON object")
    required = required_score_keys(dimension)
    missing = sorted(required - set(scores))
    if missing:
        raise ValueError(
            f"{eval_id}: missing required score keys for {dimension}: {missing}"
        )


def _merge_panel_3_scores(
    scores_by_judge: dict[str, dict],
    dimension: str,
) -> dict:
    """Synthesize panel_3 score record from three judges.

    Numeric fields (per :func:`numeric_score_fields`) become element-wise
    means across whichever judges supplied a numeric value. Categorical /
    list / text fields are preserved from the primary panel judge for backward
    compatibility, and a per-judge breakdown for S4's ``identified_persona``
    is added so the report can compute per-judge accuracy.
    """
    panel_scores = {
        judge_label: scores_by_judge.get(judge_label) or {}
        for judge_label in JUDGE_LABELS
    }
    primary_scores = panel_scores[JUDGE_LABELS[0]]
    out = dict(primary_scores)  # start from primary, overwrite numerics
    try:
        numeric_fields = numeric_score_fields(dimension)
    except KeyError:
        numeric_fields = ()
    for field in numeric_fields:
        vals = [
            float(v)
            for scores in panel_scores.values()
            for v in (scores.get(field),)
            if isinstance(v, (int, float))
        ]
        if vals:
            out[field] = round(sum(vals) / len(vals), 4)
    if dimension == "S4":
        out["identified_persona_by_judge"] = {
            judge_label: str(scores.get("identified_persona", ""))
            for judge_label, scores in panel_scores.items()
        }
    # Failure types: union (preserves any signal any judge raised)
    has_failure_types = any(
        isinstance(s.get("failure_types"), list) for s in panel_scores.values()
    )
    if has_failure_types:
        union: set[str] = set()
        for s in panel_scores.values():
            ft = s.get("failure_types") or []
            if isinstance(ft, str):
                ft = [ft]
            union.update(str(x) for x in ft if x)
        out["failure_types"] = sorted(union)
    # Reasoning / failure_evidence: keep all attributed for transparency
    out["reasoning_by_judge"] = {
        judge_label: scores.get("reasoning", "")
        for judge_label, scores in panel_scores.items()
    }
    if any("failure_evidence" in s for s in panel_scores.values()):
        out["failure_evidence_by_judge"] = {
            judge_label: scores.get("failure_evidence", "")
            for judge_label, scores in panel_scores.items()
        }
    return out


def aggregate(
    judge_input_dir: Path,
    judge_output_dir: Path,
    output_path: Path,
    strict: bool = False,
    profile: str = "full",
    judge_view: str = "panel_3",
    secondary_judge_output_dir: Path | None = None,
    tertiary_judge_output_dir: Path | None = None,
) -> dict:
    """Aggregate judge outputs into ``all_evaluations.json``.

    Args:
        judge_view: ``"panel_3"`` (default, mean of Sonnet + GPT-5.4 + Gemini)
            or ``"primary"`` (Sonnet only — legacy single-judge behavior).
        secondary_judge_output_dir: explicit GPT-5.4 dir for panel_3 mode;
            defaults to the configured secondary panel judge output dir.
        tertiary_judge_output_dir: explicit Gemini dir for panel_3 mode;
            defaults to the configured tertiary panel judge output dir.
    """
    if judge_view not in ("panel_3", "primary"):
        raise ValueError(
            f"Unknown judge_view {judge_view!r}; use 'panel_3' or 'primary'"
        )
    panel_3 = judge_view == "panel_3"
    if panel_3:
        secondary_model_id, secondary_label = PANEL_JUDGES[1]
        tertiary_model_id, tertiary_label = PANEL_JUDGES[2]
        results_root = judge_output_dir.parent
        if secondary_judge_output_dir is None:
            secondary_judge_output_dir = (
                results_root
                / "judge_outputs_by_model"
                / safe_model_dir(secondary_model_id)
            )
        if tertiary_judge_output_dir is None:
            tertiary_judge_output_dir = (
                results_root
                / "judge_outputs_by_model"
                / safe_model_dir(tertiary_model_id)
            )
        for label, path in (
            (secondary_label, secondary_judge_output_dir),
            (tertiary_label, tertiary_judge_output_dir),
        ):
            if not path.exists():
                raise FileNotFoundError(
                    f"panel_3 view requires {label} outputs at {path}; "
                    "either run the full judge panel or pass judge_view='primary'"
                )
    records: dict[str, list[dict]] = {dim: [] for dim in DIMENSION_TO_FILE}

    if strict:
        _validate_strict_input_output_sets(judge_input_dir, judge_output_dir, profile)

    metadata_by_name = _build_input_metadata_index(judge_input_dir)
    d3_records_seen: list[dict] = []

    for output_file in sorted(judge_output_dir.glob("*.json")):
        output = load_json(output_file)
        eval_id = output.get("eval_id") or output_file.stem
        dimension = output.get("dimension") or eval_id.split("__", 1)[0]
        scores = output.get("scores", {})

        # Panel-3 averaging: blend all configured judges before downstream
        # aggregation. Falls back to whichever judges produced an output for
        # this eval_id when one is absent (e.g., partial run, credit exhaustion).
        if panel_3:
            secondary_path = secondary_judge_output_dir / output_file.name
            tertiary_path = tertiary_judge_output_dir / output_file.name
            secondary_missing = not secondary_path.exists()
            tertiary_missing = not tertiary_path.exists()
            scores_by_judge = {
                JUDGE_LABELS[0]: scores,
                secondary_label: (
                    load_json(secondary_path).get("scores", {})
                    if not secondary_missing
                    else {}
                ),
                tertiary_label: (
                    load_json(tertiary_path).get("scores", {})
                    if not tertiary_missing
                    else {}
                ),
            }
            scores = _merge_panel_3_scores(scores_by_judge, dimension)
            if secondary_missing or tertiary_missing:
                scores = dict(scores)
                scores["panel_3_partial"] = {
                    "secondary_missing": secondary_missing,
                    "tertiary_missing": tertiary_missing,
                }

        if strict:
            _validate_score_schema(dimension, scores, eval_id)
        if output_file.name in metadata_by_name:
            metadata = metadata_by_name[output_file.name]
        elif strict:
            raise FileNotFoundError(
                f"Missing judge input metadata for {output_file.name}"
            )
        else:
            metadata = {}

        if dimension == "S2":
            metadata = dict(metadata)
            normalized = _normalize_d3_scores(scores)
            metadata["drift_onset_turn"] = normalized.get("drift_onset_turn")
            record = {"eval_id": eval_id, "scores": normalized, "metadata": metadata}
            d3_records_seen.append(record)
            if metadata.get("phase") != "control":
                records["S2"].append(record)
            continue

        if dimension == "S6":
            if strict and "distinctiveness" not in scores:
                raise ValueError(f"S6 output missing distinctiveness: {eval_id}")
            records["S6"].append(
                {"eval_id": eval_id, "scores": scores, "metadata": metadata}
            )
            continue

        if dimension not in records:
            if strict:
                raise ValueError(f"Unsupported judge output dimension: {dimension}")
            continue

        records[dimension].append(
            {"eval_id": eval_id, "scores": scores, "metadata": metadata}
        )

    d3_control_records = [
        record
        for record in d3_records_seen
        if record.get("metadata", {}).get("phase") == "control"
    ]
    if strict and d3_control_records and not records["S6"]:
        raise ValueError(
            "S2 control-conversation outputs were present but no real S6 judge "
            "outputs were found. S6-from-S2 fallback is disabled."
        )

    atomic_write_json(output_path, records)
    return {
        "output_path": str(output_path),
        "counts": {key: len(value) for key, value in records.items()},
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument("--judge-input-dir", default=None)
    parser.add_argument("--judge-output-dir", default=None)
    parser.add_argument("--output", default=None)
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--profile", choices=["full", "pilot"], default="full")
    args = parser.parse_args(list(argv) if argv is not None else None)

    results_dir = Path(args.results_dir) if args.results_dir else default_results_dir()
    judge_input_dir = (
        Path(args.judge_input_dir)
        if args.judge_input_dir
        else results_dir / "judge_inputs"
    )
    judge_output_dir = (
        Path(args.judge_output_dir)
        if args.judge_output_dir
        else results_dir / "judge_outputs"
    )
    output_path = (
        Path(args.output)
        if args.output
        else results_dir / "evaluations" / "all_evaluations.json"
    )
    summary = aggregate(
        judge_input_dir=judge_input_dir,
        judge_output_dir=judge_output_dir,
        output_path=output_path,
        strict=args.strict,
        profile=args.profile,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
