"""Estimate LLM cost for judge-qualification judge inputs."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from experiments.student_sim_stability.core.config import JUDGE_MODEL, JUDGE_MODELS
from experiments.student_sim_stability.core.io_utils import (
    atomic_write_json,
    load_json,
)
from experiments.student_sim_stability.core.paths import resolve_results_dir
from experiments.student_sim_stability.judge_qualification.render import (
    _coerce_gate_dir,
)
from server.config.pricing import resolve_pricing

CHARS_PER_TOKEN = 3.5
DEFAULT_OUTPUT_TOKENS_BY_DIMENSION = {
    "S1": 350,
    "S2": 730,
    "S5": 235,
    "S4": 290,
    "S6": 480,
}


def _estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN))


def _model_cost(model: str, input_tokens: int, output_tokens: int) -> dict[str, Any]:
    pricing = resolve_pricing(model)
    if pricing is None:
        return {
            "model": model,
            "pricing_status": "missing_from_local_pricing_table",
            "estimated_cost_usd": None,
            "input_price_usd_per_million_tokens": None,
            "output_price_usd_per_million_tokens": None,
        }
    input_price, output_price = pricing
    return {
        "model": model,
        "pricing_status": "from_bench_server_config_pricing",
        "estimated_cost_usd": round(
            input_tokens * input_price + output_tokens * output_price,
            6,
        ),
        "input_price_usd_per_million_tokens": input_price * 1_000_000,
        "output_price_usd_per_million_tokens": output_price * 1_000_000,
    }


def estimate_judge_qualification_cost(
    *,
    gate_dir: Path | None = None,
    results_dir: Path | None = None,
    models: list[str] | None = None,
) -> dict[str, Any]:
    gate_dir = _coerce_gate_dir(gate_dir=gate_dir, results_dir=results_dir)
    input_dir = gate_dir / "judge_inputs"
    report_dir = gate_dir / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    selected_models = list(models or [JUDGE_MODEL])
    by_dimension: dict[str, dict[str, int]] = defaultdict(
        lambda: {"calls": 0, "input_tokens": 0, "output_tokens": 0}
    )
    total_input = 0
    total_output = 0
    calls = 0
    for path in sorted(input_dir.glob("*.json")):
        payload = load_json(path)
        dimension = str(payload.get("dimension", "unknown"))
        input_tokens = _estimate_tokens(str(payload.get("prompt", "")))
        output_tokens = DEFAULT_OUTPUT_TOKENS_BY_DIMENSION.get(dimension, 400)
        by_dimension[dimension]["calls"] += 1
        by_dimension[dimension]["input_tokens"] += input_tokens
        by_dimension[dimension]["output_tokens"] += output_tokens
        total_input += input_tokens
        total_output += output_tokens
        calls += 1

    model_rows = [
        _model_cost(model, total_input, total_output) for model in selected_models
    ]
    known_total = sum(
        row["estimated_cost_usd"]
        for row in model_rows
        if isinstance(row["estimated_cost_usd"], (int, float))
    )
    estimate = {
        "version": "judge_qualification_cost_estimate_v1",
        "input_dir": str(input_dir),
        "estimation_method": {
            "chars_per_token": CHARS_PER_TOKEN,
            "output_tokens_by_dimension": DEFAULT_OUTPUT_TOKENS_BY_DIMENSION,
            "pricing_source": "bench/server/config/pricing.py",
        },
        "counts": {
            "calls": calls,
            "input_tokens": total_input,
            "output_tokens": total_output,
        },
        "by_dimension": dict(sorted(by_dimension.items())),
        "models": model_rows,
        "estimated_total_cost_usd": round(known_total, 6),
        "estimated_total_with_25pct_buffer_usd": round(known_total * 1.25, 6),
    }
    atomic_write_json(report_dir / "llm_cost_estimate.json", estimate)
    return estimate


def _parse_models(value: str | None, *, all_models: bool) -> list[str]:
    if all_models:
        return list(JUDGE_MODELS)
    if not value:
        return [JUDGE_MODEL]
    return [item.strip() for item in value.split(",") if item.strip()]


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", required=True)
    parser.add_argument("--models", default=None)
    parser.add_argument("--all-models", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    estimate = estimate_judge_qualification_cost(
        gate_dir=resolve_results_dir(args.results_dir),
        models=_parse_models(args.models, all_models=args.all_models),
    )
    print(json.dumps(estimate, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
