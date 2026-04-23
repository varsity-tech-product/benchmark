"""Scripted multi-turn dialogue generation for issue #83."""

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

SCRIPTS = [
    {
        "script_id": "code_pressure",
        "pressure": "coding",
        "tutor_turns": [
            "Let's write a pandas function to compute daily returns.",
            "Now vectorize it and avoid a loop.",
            "Can you debug why the first row becomes NaN?",
            "How would you test this function?",
        ],
    },
    {
        "script_id": "finance_pressure",
        "pressure": "finance",
        "tutor_turns": [
            "Let's reason about Sharpe ratio before coding.",
            "How would transaction costs change your interpretation?",
            "Where could look-ahead bias sneak into this strategy?",
            "What would make this backtest untrustworthy?",
        ],
    },
]


def default_results_dir() -> Path:
    return BENCH_ROOT / OUTPUT_DIR


def run_scripted_dialogues(
    results_dir: Path | None = None,
    model: str | None = None,
    limit: int | None = None,
    workers: int | None = MAX_WORKERS,
) -> dict:
    out = results_dir or default_results_dir()
    if not out.is_absolute():
        out = BENCH_ROOT / out
    dialogue_dir = out / "scripted" / "conversations"
    dialogue_dir.mkdir(parents=True, exist_ok=True)

    selected_models = [model] if model else list(STUDENT_MODELS)
    jobs = []
    for selected_model in selected_models:
        for contract in list_persona_contracts():
            for script in SCRIPTS:
                jobs.append((selected_model, contract["persona_id"], script))
    if limit is not None:
        jobs = jobs[:limit]

    def run_one(selected_model: str, persona_id: str, script: dict) -> Path:
        persona = load_student_persona(persona_id)
        sim = StudentSimulator(
            scenario=(
                "Scripted persona validation dialogue. Tutor messages are fixed; "
                "respond naturally as the student."
            ),
            user_description=build_contract_user_description(persona.persona_id),
            model=_make_client(selected_model, temperature=TEMPERATURE),
        )
        conversation: list[dict] = []
        for tutor_turn in script["tutor_turns"]:
            conversation.append(
                {
                    "role": "assistant",
                    "content": tutor_turn,
                    "source": "scripted_tutor",
                }
            )
            student_message = sim.generate_message(conversation)
            conversation.append(
                {
                    "role": "user",
                    "content": student_message,
                    "source": STUDENT_MODEL_SOURCE,
                    "model": selected_model,
                }
            )
        output = {
            "script_id": script["script_id"],
            "pressure": script["pressure"],
            "persona_id": persona_id,
            "student_model": selected_model,
            "conversation": conversation,
        }
        model_short = selected_model.split("/")[-1]
        path = dialogue_dir / f"{persona_id}__{script['script_id']}__{model_short}.json"
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
        "script_count": len(SCRIPTS),
        "workers": worker_count,
        "written": written,
        "expected_full_count": len(STUDENT_MODELS)
        * len(list_persona_contracts())
        * len(SCRIPTS),
        "conversations_dir": str(dialogue_dir),
    }
    with open(out / "scripted" / "manifest.json", "w", encoding="utf-8") as fh:
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
    manifest = run_scripted_dialogues(
        Path(args.results_dir) if args.results_dir else None,
        model=args.model,
        limit=args.limit,
        workers=args.workers,
    )
    print(json.dumps(manifest, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
