"""Aggregate stability judge outputs into the report input JSON."""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Iterable

BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.config import OUTPUT_DIR  # noqa: E402


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


def _input_metadata(input_dir: Path, output_file: Path) -> dict:
    input_path = input_dir / output_file.name
    if not input_path.exists():
        raise FileNotFoundError(f"Missing judge input metadata for {output_file.name}")
    return _load_json(input_path).get("metadata", {})


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


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


def _normalize_d4_scores(scores: dict) -> dict:
    normalized = dict(scores)
    normalized["per_turn_fidelity"] = _per_turn_fidelity(scores)
    return normalized


def _resolve_label(label_text: object, label_to_model: dict) -> str:
    text = "" if label_text is None else str(label_text)
    labels = re.findall(r"System [A-Z]", text)
    if labels:
        return label_to_model.get(labels[0], labels[0])
    return label_to_model.get(text, text)


def _control_record_from_d4(eval_id: str, scores: dict, metadata: dict) -> dict:
    normalized = _normalize_d4_scores(scores)
    if "distinctiveness" not in normalized:
        normalized["distinctiveness"] = _mean(normalized.get("per_turn_fidelity", []))
    return {"eval_id": eval_id, "scores": normalized, "metadata": metadata}


def aggregate(
    judge_input_dir: Path,
    judge_output_dir: Path,
    output_path: Path,
    strict: bool = False,
) -> dict:
    records: dict[str, list[dict]] = {
        "D1": [],
        "D2": [],
        "D3": [],
        "control": [],
        "D4": [],
    }

    control_outputs = sorted(judge_output_dir.glob("control__*.json"))

    for output_file in sorted(judge_output_dir.glob("*.json")):
        output = _load_json(output_file)
        eval_id = output.get("eval_id") or output_file.stem
        dimension = output.get("dimension") or eval_id.split("__", 1)[0]
        scores = output.get("scores", {})
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
                if not control_outputs:
                    records["control"].append(
                        _control_record_from_d4(eval_id, normalized, metadata)
                    )
            else:
                records["D4"].append(record)
            continue

        if dimension == "control":
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
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
