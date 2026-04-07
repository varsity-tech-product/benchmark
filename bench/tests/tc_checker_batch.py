"""
Batch TC checker simulation for S04 traces.

Tests the new 4-item TC against all available S04 execution traces,
using Sonnet-4.6 as the checker model (new TC_CHECKER_MODEL setting).

Usage:
    python -m bench.tests.tc_checker_batch
"""

import asyncio
import json
import os
import re
from pathlib import Path

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# ── New 4-item TC ───────────────────────────────────────────────────────
TC_TEXT = (
    "The tutor has completed the following research steps with computational "
    "evidence (not just verbal discussion): "
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
    "statistical fragility (e.g., out-of-sample failure), or capacity constraints. "
    "Once all four steps have been computationally demonstrated, the research "
    "session is complete."
)

CHECKER_MODEL = "anthropic/claude-sonnet-4-6"
OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")


def parse_tc_items(tc_text: str) -> list[str]:
    items = re.findall(r"\(\d+\)\s*(.+?)(?=\s*\(\d+\)|Once all|$)", tc_text, re.DOTALL)
    return [item.strip().rstrip(".") for item in items if item.strip()]


TC_ITEMS = parse_tc_items(TC_TEXT)


async def call_llm(prompt: str) -> str:
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


def build_checker_prompt(covered: list[bool], user_msg: str, asst_msg: str) -> str:
    lines = []
    for i, tc in enumerate(TC_ITEMS):
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


def parse_response(text: str, covered: list[bool]) -> list[int]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    data = json.loads(match.group())
    indices = data.get("newly_covered", [])
    return [
        i - 1
        for i in indices
        if isinstance(i, int) and 1 <= i <= len(TC_ITEMS) and not covered[i - 1]
    ]


async def simulate_one_trace(trace_path: str, label: str) -> dict:
    """Run checker simulation on one trace, return summary."""
    state = json.loads(Path(trace_path).read_text())
    conv = state["conversation"]

    # Build exchanges
    exchanges = []
    i = 0
    while i < len(conv) - 1:
        if conv[i]["role"] == "user" and conv[i + 1]["role"] == "assistant":
            exchanges.append((conv[i]["content"], conv[i + 1]["content"]))
            i += 2
        else:
            i += 1

    if len(exchanges) < 2:
        return {
            "label": label,
            "exchanges": len(exchanges),
            "result": "TOO_SHORT",
            "first_cover": {},
            "termination_exchange": None,
        }

    covered = [False] * len(TC_ITEMS)
    min_check = max(2, len(TC_ITEMS) // 2)
    first_cover = {}  # TC item index → exchange number
    term_exchange = None

    for ex_idx, (user_msg, asst_msg) in enumerate(exchanges):
        ex_num = ex_idx + 1
        if ex_num < min_check:
            continue
        if all(covered):
            break

        prompt = build_checker_prompt(covered, user_msg, asst_msg)
        try:
            response = await call_llm(prompt)
            newly = parse_response(response, covered)
            for idx in newly:
                covered[idx] = True
                if idx not in first_cover:
                    first_cover[idx] = ex_num
            if all(covered) and term_exchange is None:
                term_exchange = ex_num
        except Exception:
            pass  # Skip errors, continue

    return {
        "label": label,
        "exchanges": len(exchanges),
        "duration_s": round(state.get("duration_seconds", 0)),
        "result": "TERMINATED" if all(covered) else "NEVER",
        "termination_exchange": term_exchange,
        "first_cover": {i + 1: first_cover[i] for i in first_cover},
        "uncovered": [i + 1 for i, c in enumerate(covered) if not c],
        "bitmap_final": "".join("1" if c else "0" for c in covered),
    }


async def main():
    # Find all S04 traces (exclude strategy_old)
    trace_files = []
    for p in sorted(
        Path("bench/results").rglob("S04_volume_microstructure_alpha/*/run_state.json")
    ):
        if "strategy_old" in str(p):
            continue
        # Skip run-group haiku traces
        if "run-group" in str(p) and "haiku" in str(p):
            continue
        parts = str(p).split("/")
        persona = parts[-2]
        agent_model = f"{parts[-6]}/{parts[-5]}"
        run_type = parts[-7]
        category = parts[-4]

        # Skip traces with 0 exchanges
        state = json.loads(p.read_text())
        n_ex = len([m for m in state["conversation"] if m["role"] == "user"])
        if n_ex < 2:
            continue

        label = f"{run_type}/{agent_model}/{category}/{persona}"
        trace_files.append((str(p), label))

    print(f"Testing {len(trace_files)} S04 traces with checker={CHECKER_MODEL}")
    print(f"TC items: {len(TC_ITEMS)}")
    for i, tc in enumerate(TC_ITEMS):
        print(f"  ({i+1}) {tc[:100]}...")
    print()

    # Run all traces (sequentially to avoid rate limits)
    results = []
    for path, label in trace_files:
        print(f"  Testing: {label}...", end=" ", flush=True)
        r = await simulate_one_trace(path, label)
        status = (
            f"TERM@Ex{r['termination_exchange']}"
            if r["result"] == "TERMINATED"
            else f"NEVER(uncov={r['uncovered']})"
        )
        print(status)
        results.append(r)

    # Summary report
    print(f"\n{'='*90}")
    print("SUMMARY REPORT: S04 TC Checker Stability (checker = sonnet-4.6)")
    print(f"{'='*90}\n")

    print(
        f"{'Trace':<65s} {'Ex':>3s} {'Dur':>5s} {'Result':>8s} {'Term@':>6s} {'TC1':>4s} {'TC2':>4s} {'TC3':>4s} {'TC4':>4s}"
    )
    print("-" * 110)
    for r in results:
        fc = r.get("first_cover", {})
        tc_cols = [f"Ex{fc.get(i, '-')}" if i in fc else "  -" for i in range(1, 5)]
        term = f"Ex{r['termination_exchange']}" if r["termination_exchange"] else "  -"
        dur = f"{r.get('duration_s', 0)}s"
        print(
            f"{r['label']:<65s} {r['exchanges']:3d} {dur:>5s} {r['result']:>8s} {term:>6s} {'  '.join(tc_cols)}"
        )

    # Aggregate stats
    terminated = [r for r in results if r["result"] == "TERMINATED"]
    never = [r for r in results if r["result"] == "NEVER"]
    print(f"\nTerminated: {len(terminated)}/{len(results)}")
    if terminated:
        avg_term = sum(r["termination_exchange"] for r in terminated) / len(terminated)
        print(f"Avg termination exchange: {avg_term:.1f}")
    if never:
        print(f"Never terminated: {len(never)}")
        for r in never:
            print(f"  {r['label']}: uncovered={r['uncovered']}")

    # Per-TC coverage rate
    print("\nPer-TC coverage rate:")
    for tc_idx in range(1, 5):
        covered_count = sum(1 for r in results if tc_idx in r.get("first_cover", {}))
        print(
            f"  TC{tc_idx}: {covered_count}/{len(results)} ({covered_count/len(results)*100:.0f}%)"
        )


if __name__ == "__main__":
    asyncio.run(main())
