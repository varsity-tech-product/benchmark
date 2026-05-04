"""Targeted persona probe generation for issue #83."""

from __future__ import annotations

import argparse
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

from experiments.student_sim_stability.core.paths import (
    BENCH_ROOT,
    default_results_dir,
)

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.core.config import (  # noqa: E402
    MAX_WORKERS,
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
    # Each probe is one natural tutor turn that a real QuantTutor session might
    # produce — it embeds a task scenario and asks an indirect question whose
    # answer should expose a specific persona facet.
    #
    # ``expected_signals_by_persona`` gives the S5 judge a per-persona cheat
    # sheet of surface markers that justify a high facet_fit score; the
    # student's response does not have to use the exact phrases, but the
    # presence / absence pattern drives the score ceilings in
    # ``resources/rubrics/prompts/S5_targeted_probes.txt``.
    {
        "probe_id": "data_sanity_check",
        "facet": "finance_knowledge_boundary",
        "tutor_message": (
            "I've got daily AAPL OHLCV data pulled from Yahoo, and before we "
            "compute returns on it, I'd like your take — what's the first "
            "sanity check you'd run on this price series to make sure it's "
            "trustworthy enough to backtest on?"
        ),
        "expected_signals_by_persona": {
            "finance_veteran": [
                "adjusted close",
                "stock splits",
                "dividends",
                "corporate actions",
                "survivorship bias",
            ],
            "fullstack_practitioner": [
                "adjusted close",
                "splits",
                "NaN",
                "missing dates",
                "business day calendar",
                "dtype",
            ],
            "developer_crossover": [
                "NaN",
                "missing values",
                "dtype",
                "duplicates",
                "null",
                "date gaps",
            ],
            "double_novice": [
                "not sure",
                "missing",
                "wrong",
                "I don't know",
                "ask tutor",
                "maybe",
            ],
        },
    },
    {
        "probe_id": "returns_impl_choice",
        "facet": "coding_knowledge_boundary",
        "tutor_message": (
            "Quick opinion before I write any code — for computing daily "
            "returns from a pandas price column, which of these would you "
            "reach for and why: (a) df['close'].pct_change(), (b) "
            "df['close'].diff() / df['close'].shift(), or (c) a "
            ".apply(lambda x: ...) version?"
        ),
        "expected_signals_by_persona": {
            "fullstack_practitioner": [
                "pct_change",
                "vectorized",
                "performance",
                "first row NaN",
                "idiomatic",
                "readable",
            ],
            "developer_crossover": [
                "pct_change",
                "vectorized",
                "apply is slow",
                "broadcasting",
                "pandas idiom",
            ],
            "finance_veteran": [
                "I usually use Excel",
                "you choose",
                "not sure about Python",
                "which is the standard",
                "I'd trust your call",
            ],
            "double_novice": [
                "what does pct_change do",
                "I don't know",
                "which one",
                "can you explain",
                "what is lambda",
            ],
        },
    },
    {
        "probe_id": "debug_stress_signal",
        "facet": "emotional_profile",
        "tutor_message": (
            "Hmm — I ran your earlier idea and hit `KeyError: 'Close'` on "
            "the first line. I haven't pasted the full traceback yet; any "
            "guesses about what might be wrong?"
        ),
        "expected_signals_by_persona": {
            "finance_veteran": [
                "I'm not sure",
                "could you show the traceback",
                "not confident guessing",
                "I don't want to guess wrong",
                "something with the column name maybe",
            ],
            "fullstack_practitioner": [
                "case sensitivity",
                "column name",
                "df.columns",
                "probably 'close' lowercase",
                "check the header",
            ],
            "developer_crossover": [
                "column name",
                "df.columns",
                "case sensitivity",
                "capital letter",
                "probably renamed",
            ],
            "double_novice": [
                "what does KeyError mean",
                "I don't understand",
                "can you explain the error",
                "not sure",
                "is it broken",
            ],
        },
    },
    {
        "probe_id": "ambiguous_claim",
        "facet": "confusion_style",
        "tutor_message": (
            "You can compute a 20-day moving average with "
            "df['returns'].rolling(20).mean(), but for returns specifically "
            "that rolling-mean won't quite behave the way you might expect. "
            "What part of that sounds off to you?"
        ),
        "expected_signals_by_persona": {
            "fullstack_practitioner": [
                "first 19 rows NaN",
                "warm-up",
                "return vs price",
                "compounding",
                "arithmetic vs geometric",
            ],
            "finance_veteran": [
                "are you compounding",
                "geometric mean",
                "not the same as price SMA",
                "is that the return interpretation",
                "which return — simple or log",
            ],
            "developer_crossover": [
                "what should it do vs what does it do",
                "NaN at the start",
                "windowing behavior",
                "specifically what",
                "min_periods",
            ],
            "double_novice": [
                "what's a moving average",
                "I'm not sure what's wrong",
                "can you explain",
                "what does rolling do",
                "I need more context",
            ],
        },
    },
    {
        "probe_id": "wrong_claim_recovery",
        "facet": "recovery_style",
        "tutor_message": (
            "Quick side note: to annualize a Sharpe ratio computed from "
            "daily returns you just multiply by 252 — that's the number of "
            "trading days in a year. Does that match how you'd do it?"
        ),
        "expected_signals_by_persona": {
            "finance_veteran": [
                "not quite",
                "it's sqrt(252)",
                "square root of 252",
                "volatility scales with sqrt of time",
                "the numerator scales linearly but the denominator scales by sqrt",
            ],
            "fullstack_practitioner": [
                "sqrt(252)",
                "not 252",
                "square root",
                "volatility scaling",
                "that's wrong",
            ],
            "developer_crossover": [
                "I thought it was sqrt(252)",
                "could you double-check",
                "is it really 252",
                "I've seen sqrt somewhere",
            ],
            "double_novice": [
                "if you say so",
                "I'll go with that",
                "okay",
                "I don't know",
                "that sounds right",
            ],
        },
    },
]


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
        message, _ = sim.generate_message(conversation)
        expected_signals = (probe.get("expected_signals_by_persona") or {}).get(
            persona_id, []
        )
        output = {
            "probe_id": probe["probe_id"],
            "facet": probe["facet"],
            "persona_id": persona_id,
            "student_model": selected_model,
            "expected_signals": expected_signals,
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
