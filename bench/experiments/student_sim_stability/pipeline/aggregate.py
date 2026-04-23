"""Aggregate stability judge outputs into the report input JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable

from experiments.student_sim_stability.core.paths import BENCH_ROOT

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.core.config import (  # noqa: E402
    GENERATED_STUDENT_TURNS,
    OUTPUT_DIR,
    REPEATS,
    STUDENT_MODELS,
    TASK_PERSONA_MAP,
    TUTOR_TEMPERATURES,
)
from experiments.student_sim_stability.core.rubrics import (
    required_score_keys,
)
from experiments.student_sim_stability.pipeline.probes import PROBES  # noqa: E402

# noqa: E402
from experiments.student_sim_stability.pipeline.scripted_dialogues import (
    SCRIPTS,
)


def default_results_dir() -> Path:
    return BENCH_ROOT / OUTPUT_DIR


def atomic_write_json(path: Path, data: dict) -> None:
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
        tmp_path = Path(fh.name)
    tmp_path.replace(path)


def _load_json(path: Path) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dimension_counts(path: Path) -> dict[str, int]:
    return {
        "D1": len(list(path.glob("D1__*.json"))) if path.exists() else 0,
        "D2": len(list(path.glob("D2__*.json"))) if path.exists() else 0,
        "D3": len(list(path.glob("D3__*.json"))) if path.exists() else 0,
        "D4": len(list(path.glob("D4__*.json"))) if path.exists() else 0,
        "control": len(list(path.glob("control__*.json"))) if path.exists() else 0,
        "P1": len(list(path.glob("P1__*.json"))) if path.exists() else 0,
        "B1": len(list(path.glob("B1__*.json"))) if path.exists() else 0,
    }


def _expected_full_input_counts() -> dict[str, int]:
    combos = sum(len(v) for v in TASK_PERSONA_MAP.values())
    n_models = len(STUDENT_MODELS)
    n_temps = len(TUTOR_TEMPERATURES)
    n_personas = len({pid for ids in TASK_PERSONA_MAP.values() for pid in ids})
    live = combos * n_models * REPEATS * n_temps
    control = combos * n_models
    return {
        "D1": combos * n_models * GENERATED_STUDENT_TURNS,
        "D1_full": (live + control) * GENERATED_STUDENT_TURNS,
        "D2": combos * n_models * n_temps,
        "D3": combos * REPEATS * n_temps,
        "D4": live + control,
        "control": control,
        "P1": n_personas * len(PROBES) * n_models,
        "B1": n_personas * len(SCRIPTS) * n_models,
    }


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
        expected = _expected_full_input_counts()
        errors = []
        for dim, expected_count in expected.items():
            if dim == "D1_full":
                continue
            if dim == "D1":
                if counts[dim] not in {expected["D1"], expected["D1_full"]}:
                    errors.append(
                        f"D1 inputs={counts[dim]}/"
                        f"{expected['D1']} sample or {expected['D1_full']} full"
                    )
                continue
            if counts[dim] != expected_count:
                errors.append(f"{dim} inputs={counts[dim]}/{expected_count}")
        if errors:
            raise ValueError(
                "Strict full aggregate has incomplete inputs: " + "; ".join(errors)
            )


def _input_metadata(input_dir: Path, output_file: Path) -> dict:
    input_path = input_dir / output_file.name
    if not input_path.exists():
        raise FileNotFoundError(f"Missing judge input metadata for {output_file.name}")
    return _load_json(input_path).get("metadata", {})


def _per_turn_fidelity(scores: dict) -> list[float]:
    existing = scores.get("per_turn_fidelity")
    if isinstance(existing, list):
        return existing
    per_turn = scores.get("per_turn", [])
    if not isinstance(per_turn, list):
        return []
    return [
        turn.get("persona_fidelity", 0) for turn in per_turn if isinstance(turn, dict)
    ]


def _per_turn_field(scores: dict, field: str) -> list[float]:
    existing = scores.get(f"per_turn_{field}")
    if isinstance(existing, list):
        return existing
    per_turn = scores.get("per_turn", [])
    if not isinstance(per_turn, list):
        return []
    return [turn.get(field, 0) for turn in per_turn if isinstance(turn, dict)]


def _normalize_d4_scores(scores: dict) -> dict:
    normalized = dict(scores)
    normalized["per_turn_fidelity"] = _per_turn_fidelity(scores)
    normalized["per_turn_knowledge_leak"] = _per_turn_field(scores, "knowledge_leak")
    normalized["per_turn_co_teacher_drift"] = _per_turn_field(
        scores, "co_teacher_drift"
    )
    return normalized


def _resolve_label(label_text: object, label_to_model: dict) -> str:
    text = "" if label_text is None else str(label_text)
    labels = re.findall(r"System [A-Z]", text)
    if labels:
        return label_to_model.get(labels[0], labels[0])
    return label_to_model.get(text, text)


def _validate_score_schema(dimension: str, scores: object, eval_id: str) -> None:
    if not isinstance(scores, dict):
        raise ValueError(f"{eval_id}: scores must be a JSON object")
    required = required_score_keys(dimension)
    missing = sorted(required - set(scores))
    if missing:
        raise ValueError(
            f"{eval_id}: missing required score keys for {dimension}: {missing}"
        )


def aggregate(
    judge_input_dir: Path,
    judge_output_dir: Path,
    output_path: Path,
    strict: bool = False,
    profile: str = "full",
) -> dict:
    records: dict[str, list[dict]] = {
        "D1": [],
        "D2": [],
        "D3": [],
        "control": [],
        "D4": [],
        "P1": [],
        "B1": [],
    }

    if strict:
        _validate_strict_input_output_sets(judge_input_dir, judge_output_dir, profile)

    d4_control_outputs = 0

    for output_file in sorted(judge_output_dir.glob("*.json")):
        output = _load_json(output_file)
        eval_id = output.get("eval_id") or output_file.stem
        dimension = output.get("dimension") or eval_id.split("__", 1)[0]
        scores = output.get("scores", {})
        if strict:
            _validate_score_schema(dimension, scores, eval_id)
        try:
            metadata = _input_metadata(judge_input_dir, output_file)
        except FileNotFoundError:
            if strict:
                raise
            metadata = {}

        if dimension == "D3":
            metadata = dict(metadata)
            label_to_model = metadata.get("label_to_model", {})
            metadata["best_model"] = _resolve_label(
                scores.get("best_set"), label_to_model
            )
            metadata["worst_model"] = _resolve_label(
                scores.get("worst_set"), label_to_model
            )

        if dimension == "D4":
            metadata = dict(metadata)
            normalized = _normalize_d4_scores(scores)
            metadata["drift_onset_turn"] = normalized.get("drift_onset_turn")
            record = {"eval_id": eval_id, "scores": normalized, "metadata": metadata}
            if metadata.get("phase") == "control":
                d4_control_outputs += 1
            else:
                records["D4"].append(record)
            continue

        if dimension == "control":
            if strict and "distinctiveness" not in scores:
                raise ValueError(f"Control output missing distinctiveness: {eval_id}")
            records["control"].append(
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

    if strict and d4_control_outputs and not records["control"]:
        raise ValueError(
            "D4 control outputs were present but no real control__ judge outputs "
            "were found. Control-from-D4 fallback is disabled."
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
