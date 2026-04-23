"""Targeted persona probe generation for issue #83."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

from experiments.student_sim_stability.core.paths import BENCH_ROOT

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.core.config import (  # noqa: E402
    MAX_WORKERS,
    OUTPUT_DIR,
    STUDENT_MODEL_SOURCE,
    STUDENT_MODELS,
    TEMPERATURE,
)
from experiments.student_sim_stability.core.contracts import (  # noqa: E402
    build_contract_user_description,
    list_persona_contracts,
    load_student_persona,
)
from experiments.student_sim_stability.pipeline.runner import _make_client  # noqa: E402
from server.core.student_sim import StudentSimulator  # noqa: E402

PROBES = [
    {
        "probe_id": "finance_boundary",
        "facet": "finance_knowledge_boundary",
        "tutor_message": "Before we start, can you explain Sharpe ratio and look-ahead bias in your own words?",
    },
    {
        "probe_id": "coding_boundary",
        "facet": "coding_knowledge_boundary",
        "tutor_message": "Could you sketch the Python or pandas code you would use for this analysis?",
    },
    {
        "probe_id": "emotional_style",
        "facet": "emotional_profile",
        "tutor_message": "This task involves returns, pandas, and a formula. How are you feeling about tackling it?",
    },
    {
        "probe_id": "confusion_style",
        "facet": "confusion_style",
        "tutor_message": "Suppose my explanation suddenly used vectorized returns and annualized volatility. What would you ask next?",
    },
    {
        "probe_id": "recovery_style",
        "facet": "recovery_style",
        "tutor_message": "If I gave you one concrete example and one analogy, what would help you recover from confusion fastest?",
    },
]


def default_results_dir() -> Path:
    return BENCH_ROOT / OUTPUT_DIR


def run_probes(
    results_dir: Path | None = None,
    model: str | None = None,
    limit: int | None = None,
    workers: int | None = MAX_WORKERS,
) -> dict:
    out = results_dir or default_results_dir()
    if not out.is_absolute():
        out = BENCH_ROOT / out
    response_dir = out / "probes" / "responses"
    response_dir.mkdir(parents=True, exist_ok=True)

    selected_models = [model] if model else list(STUDENT_MODELS)
    jobs = []
    for selected_model in selected_models:
        for contract in list_persona_contracts():
            for probe in PROBES:
                jobs.append((selected_model, contract["persona_id"], probe))
    if limit is not None:
        jobs = jobs[:limit]

    def run_one(selected_model: str, persona_id: str, probe: dict) -> Path:
        persona = load_student_persona(persona_id)
        sim = StudentSimulator(
            scenario=(
                "Targeted persona validation probe. Respond as the student, "
                "not as a tutor or evaluator."
            ),
            user_description=build_contract_user_description(persona.persona_id),
            model=_make_client(selected_model, temperature=TEMPERATURE),
        )
        conversation = [
            {
                "role": "assistant",
                "content": probe["tutor_message"],
                "source": "probe_tutor_script",
            }
        ]
        message = sim.generate_message(conversation)
        output = {
            "probe_id": probe["probe_id"],
            "facet": probe["facet"],
            "persona_id": persona_id,
            "student_model": selected_model,
            "turn": {
                "role": "user",
                "content": message,
                "source": STUDENT_MODEL_SOURCE,
                "model": selected_model,
            },
            "conversation": conversation
            + [
                {
                    "role": "user",
                    "content": message,
                    "source": STUDENT_MODEL_SOURCE,
                    "model": selected_model,
                }
            ],
        }
        model_short = selected_model.split("/")[-1]
        path = response_dir / f"{persona_id}__{probe['probe_id']}__{model_short}.json"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(output, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        return path

    written = 0
    worker_count = max(1, workers or MAX_WORKERS)
    with ThreadPoolExecutor(max_workers=worker_count) as pool:
        futures = [pool.submit(run_one, *job) for job in jobs]
        for future in as_completed(futures):
            future.result()
            written += 1

    manifest = {
        "models": selected_models,
        "probe_count": len(PROBES),
        "workers": worker_count,
        "written": written,
        "expected_full_count": len(STUDENT_MODELS)
        * len(list_persona_contracts())
        * len(PROBES),
        "responses_dir": str(response_dir),
    }
    with open(out / "probes" / "manifest.json", "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    return manifest


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--results-dir", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    args = parser.parse_args(list(argv) if argv is not None else None)
    manifest = run_probes(
        Path(args.results_dir) if args.results_dir else None,
        model=args.model,
        limit=args.limit,
        workers=args.workers,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
