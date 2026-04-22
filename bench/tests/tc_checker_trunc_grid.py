"""
Truncation × Model grid test for TC checker.
Tests Opus 4.6 and GPT 5.4 across truncation values [1500, 2000, 2500, 3000, 4000].
All cases run in parallel for speed.
"""

import asyncio
import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]

MODELS = {
    "opus-4.6": "anthropic/claude-opus-4-6",
    "gpt-5.4": "openai/gpt-5.4",
}

TRUNC_VALUES = [1500, 2000, 2500, 3000, 4000]

TC_TEXTS = {
    "S04": (
        "(1) Explained the economic meaning of at least one non-price data field "
        "such as taker buy volume or trade count, and engineered at least one "
        "derived feature from raw microstructure data (e.g., a volume ratio, flow "
        "imbalance, or taker intensity measure) with a clear mathematical definition. "
        "(2) Evaluated the signal quantitatively using at least two metrics such as "
        "IC, quantile return spread, or hit rate. "
        "(3) Analyzed signal robustness by examining either decay across multiple "
        "time horizons or stability across different subperiods or regimes. "
        "(4) Identified at least one factor limiting practical deployment of the "
        "signal, supported by evidence: execution costs, signal decay speed, "
        "statistical fragility (e.g., out-of-sample failure), or capacity constraints."
    ),
    "S05": (
        "(1) Analyzed the statistical relationship between BTC and ETH using at least "
        "one method such as rolling correlation, cointegration, or lead-lag analysis, "
        "and constructed a specific cross-asset trading signal such as a spread z-score, "
        "relative-value measure, or lead-lag predictor, with a clear mathematical definition. "
        "(2) Evaluated the signal on a dollar-neutral or hedged basis, presenting specific "
        "performance metrics such as IC, Sharpe ratio, or cumulative return for the "
        "long-short portfolio. "
        "(3) Examined signal robustness by testing across at least two lookback windows, "
        "frequencies, or subperiods, and reporting how performance varies. "
        "(4) Identified at least one risk specific to cross-asset strategies, supported by "
        "evidence: relationship breakdown during a specific period, structural regime change, "
        "divergence under stress, or statistical fragility such as out-of-sample degradation."
    ),
    "S06": (
        "(1) Constructed at least two individual signals from distinct alpha sources or "
        "data features, each with a clear mathematical definition. "
        "(2) Combined the signals into a composite using an explicit method such as equal "
        "weighting, rank averaging, or optimized weighting, and analyzed signal relationships "
        "such as pairwise correlation or redundancy. "
        "(3) Evaluated the composite signal and compared its performance against at least one "
        "individual signal, presenting specific metric values such as IC, Sharpe ratio, or "
        "return spread. "
        "(4) Identified at least one condition under which signal diversification fails to "
        "provide benefit, supported by evidence: correlated drawdowns during a specific period, "
        "regime-dependent correlation breakdown, overfitting the combination weights, or "
        "statistical fragility such as out-of-sample degradation."
    ),
}

# Hard cases to test
HARD_CASES = [
    (
        "bench/results/run-group/openai/gpt-5.2/strategy/S04_volume_microstructure_alpha/advanced_quant/run_state.json",
        "S04",
        "gpt5.2/adv",
    ),
    (
        "bench/results/run-single/anthropic/claude-sonnet-4-6/strategy/S04_volume_microstructure_alpha/advanced_quant/run_state.json",
        "S04",
        "sonnet/adv",
    ),
    (
        "bench/results/run-group/openai/gpt-5.2/strategy/S06_multi_signal_combination/beginner_no_finance/run_state.json",
        "S06",
        "gpt5.2/beg",
    ),
    (
        "bench/results/run-group/openai/gpt-5.2/strategy/S06_multi_signal_combination/advanced_quant/run_state.json",
        "S06",
        "gpt5.2/adv",
    ),
    (
        "bench/results/run-group/openai/gpt-5.2/strategy/S06_multi_signal_combination/intermediate_developer/run_state.json",
        "S06",
        "gpt5.2/int",
    ),
    (
        "bench/results/run-group/openai/gpt-5.2/strategy/S05_cross_asset_alpha/beginner_no_finance/run_state.json",
        "S05",
        "gpt5.2/beg",
    ),
]


def parse_tc_items(tc_text: str) -> list[str]:
    items = re.findall(r"\(\d+\)\s*(.+?)(?=\s*\(\d+\)|$)", tc_text, re.DOTALL)
    return [item.strip().rstrip(".") for item in items if item.strip()]


SEM = asyncio.Semaphore(5)  # concurrency limit


async def call_llm(model: str, prompt: str) -> str:
    async with SEM:
        async with httpx.AsyncClient(timeout=90) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.0,
                    "max_tokens": 150,
                },
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]


def build_prompt(tc_items, covered, user_msg, asst_msg):
    lines = []
    for i, tc in enumerate(tc_items):
        status = "COVERED" if covered[i] else "NOT COVERED"
        lines.append(f"  {i + 1}. [{status}] {tc}")
    recent_json = json.dumps(
        [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": asst_msg},
        ],
        ensure_ascii=False,
    )
    return (
        "You are tracking a tutoring session's progress against "
        "specific learning objectives.\n\n"
        "Current status:\n" + "\n".join(lines) + "\n\n"
        "Latest exchange:\n" + recent_json + "\n\n"
        "Which NOT-YET-COVERED items (if any) were demonstrated with "
        "computational evidence (actual numbers, code execution, or "
        "concrete analysis) in this exchange? "
        'Return ONLY a JSON object: {"newly_covered": [1, 3]} '
        'or {"newly_covered": []} if none were covered.'
    )


def parse_resp(text, tc_items, covered):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    data = json.loads(match.group())
    return [
        i - 1
        for i in data.get("newly_covered", [])
        if isinstance(i, int) and 1 <= i <= len(tc_items) and not covered[i - 1]
    ]


async def test_one_case(
    trace_path: str,
    task_id: str,
    label: str,
    model_name: str,
    model_id: str,
    trunc: int,
) -> dict:
    """Test a single (case, model, truncation) combination."""
    tc_items = parse_tc_items(TC_TEXTS[task_id])
    state = json.loads(Path(trace_path).read_text())
    conv = state["conversation"]

    exchanges = []
    i = 0
    while i < len(conv) - 1:
        if conv[i]["role"] == "user" and conv[i + 1]["role"] == "assistant":
            exchanges.append((conv[i]["content"], conv[i + 1]["content"]))
            i += 2
        else:
            i += 1

    n_ex = len(exchanges)
    if n_ex < 2:
        return {
            "label": label,
            "model": model_name,
            "trunc": trunc,
            "task_id": task_id,
            "n_ex": n_ex,
            "bitmap": "0" * len(tc_items),
            "term_ex": None,
        }

    covered = [False] * len(tc_items)
    min_check = max(2, len(tc_items) // 2)
    first = {}
    term_ex = None

    for ex_idx, (u, a) in enumerate(exchanges):
        ex = ex_idx + 1
        if ex < min_check:
            continue
        if all(covered):
            break
        try:
            # Apply truncation
            u_trunc = u[:trunc] if u else ""
            a_trunc = a[:trunc] if a else ""
            resp = await call_llm(
                model_id, build_prompt(tc_items, covered, u_trunc, a_trunc)
            )
            newly = parse_resp(resp, tc_items, covered)
            for idx in newly:
                covered[idx] = True
                if idx not in first:
                    first[idx] = ex
            if all(covered) and term_ex is None:
                term_ex = ex
        except Exception:
            pass

    bitmap = "".join("1" if c else "0" for c in covered)
    return {
        "label": label,
        "model": model_name,
        "trunc": trunc,
        "task_id": task_id,
        "n_ex": n_ex,
        "bitmap": bitmap,
        "term_ex": term_ex,
        "first": {i + 1: first[i] for i in first},
    }


async def main():
    # Filter to existing traces
    cases = []
    for path, task_id, label in HARD_CASES:
        if Path(path).exists():
            cases.append((path, task_id, label))
        else:
            print(f"  SKIP (not found): {label} — {path}")

    print(
        f"Running {len(cases)} cases × {len(MODELS)} models × {len(TRUNC_VALUES)} truncations = {len(cases)*len(MODELS)*len(TRUNC_VALUES)} tests"
    )
    print(f"Models: {list(MODELS.keys())}")
    print(f"Truncations: {TRUNC_VALUES}")
    print()

    # Launch all tasks in parallel
    tasks = []
    for path, task_id, label in cases:
        for model_name, model_id in MODELS.items():
            for trunc in TRUNC_VALUES:
                tasks.append(
                    test_one_case(path, task_id, label, model_name, model_id, trunc)
                )

    print(f"Launching {len(tasks)} parallel tests...\n")
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect results, handle errors
    valid = []
    for r in results:
        if isinstance(r, Exception):
            print(f"  ERROR: {r}")
        else:
            valid.append(r)

    # Print results grouped by model
    for model_name in MODELS:
        print(f"\n{'='*100}")
        print(f"MODEL: {model_name} ({MODELS[model_name]})")
        print(f"{'='*100}")

        header = f"{'Case':<20s}"
        for trunc in TRUNC_VALUES:
            header += f" | @{trunc:>4d}          "
        print(header)
        print("-" * len(header))

        for path, task_id, label in cases:
            row = f"{task_id} {label:<14s}"
            for trunc in TRUNC_VALUES:
                match = [
                    r
                    for r in valid
                    if r["label"] == label
                    and r["model"] == model_name
                    and r["trunc"] == trunc
                ]
                if match:
                    r = match[0]
                    if r["term_ex"]:
                        cell = f"{r['bitmap']}(Ex{r['term_ex']})"
                    else:
                        cell = r["bitmap"]
                    row += f" | {cell:<15s}"
                else:
                    row += f" | {'ERR':<15s}"
            print(row)

    # Summary comparison with Sonnet results (hardcoded from prior tests)
    print(f"\n\n{'='*100}")
    print("CROSS-MODEL COMPARISON @ trunc=3000 (Sonnet results from prior test)")
    print(f"{'='*100}")

    sonnet_3000 = {
        "S04 gpt5.2/adv": "1111(Ex7)",
        "S04 sonnet/adv": "1111(Ex6)",
        "S06 gpt5.2/beg": "1111(Ex6)",
        "S06 gpt5.2/adv": "1111(Ex6)",
        "S06 gpt5.2/int": "0001",
        "S05 gpt5.2/beg": "1111(Ex13)",
    }

    print(f"{'Case':<20s} | {'Sonnet-4.6':<15s}", end="")
    for model_name in MODELS:
        print(f" | {model_name:<15s}", end="")
    print()
    print("-" * 80)

    for path, task_id, label in cases:
        key = f"{task_id} {label}"
        row = f"{key:<20s} | {sonnet_3000.get(key, '?'):<15s}"
        for model_name in MODELS:
            match = [
                r
                for r in valid
                if r["label"] == label
                and r["model"] == model_name
                and r["trunc"] == 3000
            ]
            if match:
                r = match[0]
                if r["term_ex"]:
                    cell = f"{r['bitmap']}(Ex{r['term_ex']})"
                else:
                    cell = r["bitmap"]
                row += f" | {cell:<15s}"
            else:
                row += f" | {'ERR':<15s}"
        print(row)


if __name__ == "__main__":
    asyncio.run(main())
