"""
TC checker stability test for S04/S05/S06 GPT-5.2 traces + all Sonnet S04 traces.
Compares: actual execution end vs Sonnet checker termination point.
"""

import asyncio
import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

CHECKER_MODEL = "anthropic/claude-sonnet-4-6"
OPENROUTER_KEY = os.environ["OPENROUTER_API_KEY"]

# ── TC texts per task ───────────────────────────────────────────────────
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


def parse_tc_items(tc_text: str) -> list[str]:
    items = re.findall(r"\(\d+\)\s*(.+?)(?=\s*\(\d+\)|$)", tc_text, re.DOTALL)
    return [item.strip().rstrip(".") for item in items if item.strip()]


SEM = asyncio.Semaphore(3)  # rate limit


async def call_llm(prompt: str) -> str:
    async with SEM:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENROUTER_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": CHECKER_MODEL,
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
            {"role": "user", "content": user_msg[:1500]},
            {"role": "assistant", "content": asst_msg[:1500]},
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


async def simulate_trace(trace_path: str, tc_items: list[str]) -> dict:
    state = json.loads(Path(trace_path).read_text())
    conv = state["conversation"]
    dur = state.get("duration_seconds", 0)

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
            "n_ex": n_ex,
            "dur": dur,
            "term_ex": None,
            "first": {},
            "uncov": list(range(1, len(tc_items) + 1)),
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
            resp = await call_llm(build_prompt(tc_items, covered, u, a))
            newly = parse_resp(resp, tc_items, covered)
            for idx in newly:
                covered[idx] = True
                if idx not in first:
                    first[idx] = ex
            if all(covered) and term_ex is None:
                term_ex = ex
        except Exception:
            pass

    return {
        "n_ex": n_ex,
        "dur": dur,
        "term_ex": term_ex,
        "first": {i + 1: first[i] for i in first},
        "uncov": [i + 1 for i, c in enumerate(covered) if not c],
    }


async def main():
    # Collect traces
    base = Path("bench/results")
    traces = []

    # GPT-5.2 run-group S04/05/06
    for p in sorted(
        base.glob("run-group/openai/gpt-5.2/strategy/S0[456]_*/*/run_state.json")
    ):
        task_dir = p.parent.parent.name
        task_id = task_dir[:3]  # S04, S05, S06
        persona = p.parent.name
        traces.append((str(p), task_id, f"gpt5.2/{persona}", persona))

    # Sonnet S04/05/06 (run-single, strategy + strategy_newest)
    for p in sorted(
        base.glob(
            "run-single/anthropic/claude-sonnet-4-6/strategy*/S0[456]_*/*/run_state.json"
        )
    ):
        if "strategy_old" in str(p):
            continue
        task_dir = p.parent.parent.name
        task_id = task_dir[:3]
        persona = p.parent.name
        cat = p.parent.parent.parent.name
        label = f"sonnet/{cat}/{persona}"
        traces.append((str(p), task_id, label, persona))

    print(f"Testing {len(traces)} traces with checker={CHECKER_MODEL}\n")

    # Run all
    results = []
    for path, task_id, label, persona in traces:
        tc_items = parse_tc_items(TC_TEXTS[task_id])
        print(f"  {task_id} {label}...", end=" ", flush=True)
        r = await simulate_trace(path, tc_items)
        r["task_id"] = task_id
        r["label"] = label
        r["persona"] = persona
        results.append(r)
        status = (
            f"TERM@Ex{r['term_ex']}" if r["term_ex"] else f"NEVER(uncov={r['uncov']})"
        )
        print(status)

    # ── Report ──────────────────────────────────────────────────────────
    print(f"\n{'='*120}")
    print("COMPARISON: Actual Execution vs Checker Termination")
    print(f"{'='*120}\n")

    header = f"{'Task':4s} {'Label':<40s} {'ActualEx':>8s} {'ActualDur':>9s} {'ChkTerm':>7s} {'EstDur':>9s} {'Saving':>7s} {'TC1':>4s} {'TC2':>4s} {'TC3':>4s} {'TC4':>4s} {'Uncov'}"
    print(header)
    print("-" * 130)

    for task_id in ["S04", "S05", "S06"]:
        task_results = [r for r in results if r["task_id"] == task_id]
        for r in task_results:
            actual_ex = r["n_ex"]
            actual_dur = r["dur"]
            term_ex = r["term_ex"]

            if term_ex and actual_ex > 0:
                est_dur = actual_dur * term_ex / actual_ex
                saving = f"{(1 - term_ex/actual_ex)*100:.0f}%"
            else:
                est_dur = actual_dur
                saving = "  -"

            fc = r["first"]
            tc_strs = []
            for i in range(1, 5):
                if i in fc:
                    tc_strs.append(f"Ex{fc[i]:>2d}")
                else:
                    tc_strs.append("   -")

            uncov_str = str(r["uncov"]) if r["uncov"] else ""
            term_str = f"Ex{term_ex}" if term_ex else "  -"

            print(
                f"{task_id:4s} {r['label']:<40s} {actual_ex:>8d} {actual_dur:>8.0f}s {term_str:>7s} {est_dur:>8.0f}s {saving:>7s} {'  '.join(tc_strs):>20s} {uncov_str}"
            )
        print()

    # Per-task summary
    print(f"{'='*120}")
    print("PER-TASK TC COVERAGE SUMMARY")
    print(f"{'='*120}\n")

    for task_id in ["S04", "S05", "S06"]:
        task_results = [r for r in results if r["task_id"] == task_id]
        n = len(task_results)
        termed = sum(1 for r in task_results if r["term_ex"])
        tc_items = parse_tc_items(TC_TEXTS[task_id])

        print(f"{task_id}: {termed}/{n} terminated")
        for tc_idx in range(1, len(tc_items) + 1):
            cov = sum(1 for r in task_results if tc_idx in r["first"])
            print(
                f"  TC{tc_idx}: {cov}/{n} ({cov/n*100:.0f}%) — {tc_items[tc_idx-1][:80]}..."
            )

        # Per-persona
        for persona in [
            "beginner_no_finance",
            "intermediate_developer",
            "advanced_quant",
        ]:
            pr = [r for r in task_results if r["persona"] == persona]
            if pr:
                termed_p = sum(1 for r in pr if r["term_ex"])
                print(f"  [{persona}]: {termed_p}/{len(pr)} terminated")
        print()


if __name__ == "__main__":
    asyncio.run(main())
