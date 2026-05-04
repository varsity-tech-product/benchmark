"""Compute agreement across configured judge-model outputs."""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

from experiments.user_sim_stability.core.numerics import safe_mean, safe_std
from experiments.user_sim_stability.core.paths import (
    BENCH_ROOT,
    default_results_dir,
)

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.user_sim_stability.core.config import (  # noqa: E402
    JUDGE_MODEL,
    JUDGE_MODELS,
    JUDGE_TEMPERATURE,
)
from experiments.user_sim_stability.core.io_utils import (  # noqa: E402
    load_json,
    safe_model_dir,
)
from experiments.user_sim_stability.core.rubrics import (  # noqa: E402
    primary_score_field,
)


def compute_judge_agreement(
    results_dir: Path | None = None,
    models: list[str] | None = None,
) -> dict:
    out = results_dir or default_results_dir()
    if not out.is_absolute():
        out = BENCH_ROOT / out
    selected_models = list(models or JUDGE_MODELS)
    by_model_dir = out / "judge_outputs_by_model"
    input_dir = out / "judge_inputs"
    report_dir = out / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    expected_eval_ids = (
        {path.stem for path in input_dir.glob("*.json")}
        if input_dir.exists()
        else set()
    )

    outputs: dict[str, dict[str, dict]] = {}
    missing_model_dirs: list[str] = []
    for model in selected_models:
        model_dir = by_model_dir / safe_model_dir(model)
        if not model_dir.exists():
            missing_model_dirs.append(model)
            outputs[model] = {}
            continue
        outputs[model] = {
            path.stem: load_json(path) for path in model_dir.glob("*.json")
        }

    output_eval_ids = set().union(*(set(items) for items in outputs.values()))
    eval_ids = sorted(expected_eval_ids or output_eval_ids)
    extra_outputs = (
        sorted(output_eval_ids - expected_eval_ids) if expected_eval_ids else []
    )
    complete_items = []
    missing_outputs = []
    invalid_scores = []
    for eval_id in eval_ids:
        present = [model for model in selected_models if eval_id in outputs[model]]
        if len(present) != len(selected_models):
            missing_outputs.append(
                {
                    "eval_id": eval_id,
                    "present_models": present,
                    "missing_models": [
                        model for model in selected_models if model not in present
                    ],
                }
            )
            continue
        dimension = outputs[selected_models[0]][eval_id].get("dimension")
        try:
            score_field = primary_score_field(dimension)
        except KeyError:
            continue
        values = []
        for model in selected_models:
            value = outputs[model][eval_id].get("scores", {}).get(score_field)
            if not isinstance(value, (int, float)):
                invalid_scores.append(
                    {
                        "eval_id": eval_id,
                        "model": model,
                        "dimension": dimension,
                        "score_field": score_field,
                    }
                )
                break
            values.append(float(value))
        if len(values) != len(selected_models):
            continue
        complete_items.append(
            {
                "eval_id": eval_id,
                "dimension": dimension,
                "score_field": score_field,
                "scores_by_model": dict(zip(selected_models, values)),
                "range": max(values) - min(values),
                "std": safe_std(values),
            }
        )

    by_dimension: dict[str, list[dict]] = defaultdict(list)
    for item in complete_items:
        by_dimension[item["dimension"]].append(item)

    metrics_by_dimension = {}
    for dimension, items in sorted(by_dimension.items()):
        ranges = [item["range"] for item in items]
        stds = [item["std"] for item in items if item["std"] is not None]
        metrics_by_dimension[dimension] = {
            "n": len(items),
            "mean_score_range": safe_mean(ranges),
            "mean_score_std": safe_mean(stds),
            "within_one_point_rate": safe_mean(
                [1.0 if r <= 1.0 else 0.0 for r in ranges]
            ),
        }

    all_ranges = [item["range"] for item in complete_items]
    any_outputs = any(bool(items) for items in outputs.values())
    if missing_model_dirs or missing_outputs or invalid_scores or extra_outputs:
        status = "incomplete" if any_outputs or complete_items else "not_run"
    else:
        status = "computed" if complete_items else "not_run"
    report = {
        "primary_judge": JUDGE_MODEL,
        "judge_models": selected_models,
        "temperature": JUDGE_TEMPERATURE,
        "multi_judge_status": status,
        "missing_model_dirs": missing_model_dirs,
        "expected_input_count": len(expected_eval_ids),
        "missing_outputs_count": len(missing_outputs),
        "missing_outputs_sample": missing_outputs[:20],
        "extra_outputs_count": len(extra_outputs),
        "extra_outputs_sample": extra_outputs[:20],
        "invalid_score_count": len(invalid_scores),
        "invalid_score_sample": invalid_scores[:20],
        "agreement_metrics": (
            {
                "n_complete_items": len(complete_items),
                "mean_score_range": safe_mean(all_ranges),
                "within_one_point_rate": safe_mean(
                    [1.0 if value <= 1.0 else 0.0 for value in all_ranges]
                ),
                "by_dimension": metrics_by_dimension,
            }
            if complete_items
            else None
        ),
    }
    with open(report_dir / "judge_agreement.json", "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return report


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    args = parser.parse_args(list(argv) if argv is not None else None)
    report = compute_judge_agreement(
        Path(args.results_dir) if args.results_dir else None
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
