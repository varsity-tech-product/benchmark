"""Validate student simulator stability experiment artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.config import (  # noqa: E402
    EXPERIMENT_TASKS,
    FIXED_TURNS,
    OUTPUT_DIR,
    REPEATS,
    STUDENT_MODELS,
    TASK_PERSONA_MAP,
    TUTOR_TEMPERATURES,
    compute_trial_count,
)


@dataclass
class Check:
    name: str
    ok: bool
    message: str
    required: bool = True


def default_results_dir() -> Path:
    return BENCH_ROOT / OUTPUT_DIR


def _count_files(path: Path, pattern: str = "*.json") -> int:
    return len(list(path.glob(pattern))) if path.exists() else 0


def _expected_counts() -> dict[str, int]:
    combos = sum(len(v) for v in TASK_PERSONA_MAP.values())
    n_models = len(STUDENT_MODELS)
    n_temps = len(TUTOR_TEMPERATURES)
    control = len(EXPERIMENT_TASKS) * n_models
    live = combos * n_models * REPEATS * n_temps
    return {
        "conversations": live + control,
        "live_conversations": live,
        "control_conversations": control,
        "d1_sample": combos * n_models * FIXED_TURNS,
        "d1_full": (live + control) * FIXED_TURNS,
        "d2": combos * n_models * n_temps,
        "d3": combos * REPEATS * n_temps,
        "d4": live + control,
        "control": control,
        "aggregate_d4": live,
    }


def _dimension_counts(path: Path) -> dict[str, int]:
    return {
        "D1": _count_files(path, "D1__*.json"),
        "D2": _count_files(path, "D2__*.json"),
        "D3": _count_files(path, "D3__*.json"),
        "D4": _count_files(path, "D4__*.json"),
        "control": _count_files(path, "control__*.json"),
    }


def validate(results_dir: Path) -> tuple[list[Check], dict]:
    expected = _expected_counts()
    counts = compute_trial_count()
    checks: list[Check] = []

    conv_dir = results_dir / "conversations"
    input_dir = results_dir / "judge_inputs"
    output_dir = results_dir / "judge_outputs"
    eval_path = results_dir / "evaluations" / "all_evaluations.json"
    report_path = results_dir / "report" / "stability_report.html"

    conversations = _count_files(conv_dir)
    checks.append(
        Check(
            "conversation_count",
            conversations == expected["conversations"],
            f"{conversations}/{expected['conversations']} conversation files",
        )
    )

    input_counts = _dimension_counts(input_dir)
    d1_input_ok = input_counts["D1"] in {0, expected["d1_sample"], expected["d1_full"]}
    checks.append(
        Check(
            "judge_input_d1_policy",
            d1_input_ok,
            (
                f"D1 inputs={input_counts['D1']} "
                f"(expected sample {expected['d1_sample']} or full {expected['d1_full']})"
            ),
            required=False,
        )
    )
    for dim, key in [("D2", "d2"), ("D3", "d3"), ("D4", "d4")]:
        checks.append(
            Check(
                f"judge_input_{dim.lower()}",
                input_counts[dim] in {0, expected[key]},
                f"{dim} inputs={input_counts[dim]}/{expected[key]}",
                required=False,
            )
        )
    checks.append(
        Check(
            "judge_input_control",
            input_counts["control"] in {0, expected["control"]},
            f"control inputs={input_counts['control']}/{expected['control']} (optional)",
            required=False,
        )
    )

    output_counts = _dimension_counts(output_dir)
    checks.append(
        Check(
            "judge_output_d1_sample",
            output_counts["D1"] in {0, expected["d1_sample"]},
            f"D1 outputs={output_counts['D1']}/{expected['d1_sample']}",
            required=False,
        )
    )
    for dim, key in [("D2", "d2"), ("D3", "d3"), ("D4", "d4")]:
        checks.append(
            Check(
                f"judge_output_{dim.lower()}",
                output_counts[dim] in {0, expected[key]},
                f"{dim} outputs={output_counts[dim]}/{expected[key]}",
                required=False,
            )
        )
    checks.append(
        Check(
            "judge_output_control",
            output_counts["control"] in {0, expected["control"]},
            f"control outputs={output_counts['control']}/{expected['control']} (optional)",
            required=False,
        )
    )

    aggregate_counts: dict[str, int] = {}
    if eval_path.exists():
        with open(eval_path, encoding="utf-8") as fh:
            aggregate = json.load(fh)
        aggregate_counts = {
            key: len(aggregate.get(key, []))
            for key in ["D1", "D2", "D3", "D4", "control"]
        }
        expected_aggregate = {
            "D1": expected["d1_sample"],
            "D2": expected["d2"],
            "D3": expected["d3"],
            "D4": expected["aggregate_d4"],
            "control": expected["control"],
        }
        for key, expected_count in expected_aggregate.items():
            checks.append(
                Check(
                    f"aggregate_{key.lower()}",
                    aggregate_counts[key] == expected_count,
                    f"{key} aggregate={aggregate_counts[key]}/{expected_count}",
                )
            )
    else:
        checks.append(
            Check("aggregate_file", False, f"missing {eval_path}", required=False)
        )

    checks.append(
        Check(
            "report_html",
            report_path.exists(),
            f"{report_path} {'exists' if report_path.exists() else 'missing'}",
            required=False,
        )
    )

    return checks, {
        "expected": expected,
        "compute_trial_count": counts,
        "judge_inputs": input_counts,
        "judge_outputs": output_counts,
        "aggregate": aggregate_counts,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument(
        "--strict", action="store_true", help="Treat optional warnings as failures"
    )
    parser.add_argument(
        "--json", action="store_true", help="Print machine-readable summary"
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    results_dir = Path(args.results_dir) if args.results_dir else default_results_dir()
    checks, summary = validate(results_dir)
    failures = [c for c in checks if not c.ok and (c.required or args.strict)]

    if args.json:
        print(
            json.dumps(
                {
                    "ok": not failures,
                    "checks": [c.__dict__ for c in checks],
                    "summary": summary,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        for check in checks:
            status = "OK" if check.ok else ("FAIL" if check.required else "WARN")
            print(f"[{status}] {check.name}: {check.message}")

    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
