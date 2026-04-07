"""
Simulate the incremental TC checker against a real S04 beginner trace.

Tests both GPT-5.2 and Sonnet-4.6 to see when each model would trigger
termination for the new 4-item TC.

Usage:
    python -m tests.tc_checker_simulation
"""

import asyncio
import json
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

# ── Config ──────────────────────────────────────────────────────────────
TRACE_PATH = Path(
    "bench/results/run-single/anthropic/claude-sonnet-4-6/strategy/"
    "S04_volume_microstructure_alpha/beginner_no_finance/run_state.json"
)

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

MODELS = {
    "gpt-5.2": "openai/gpt-5.2",
    "sonnet-4.6": "anthropic/claude-sonnet-4-6",
}


# ── Parse TC items (same logic as simulation.py) ────────────────────────
def parse_tc_items(tc_text: str) -> list[str]:
    items = re.findall(r"\(\d+\)\s*(.+?)(?=\s*\(\d+\)|Once all|$)", tc_text, re.DOTALL)
    return [item.strip().rstrip(".") for item in items if item.strip()]


# ── Build checker prompt (same as _incremental_check) ───────────────────
def build_checker_prompt(
    tc_items: list[str],
    covered: list[bool],
    user_msg: str,
    assistant_msg: str,
) -> str:
    uncovered_lines = []
    covered_lines = []
    for i, tc in enumerate(tc_items):
        if covered[i]:
            covered_lines.append(f"  {i + 1}. [COVERED] {tc}")
        else:
            uncovered_lines.append(f"  {i + 1}. [NOT COVERED] {tc}")

    recent_json = json.dumps(
        [
            {"role": "user", "content": user_msg[:1500]},
            {"role": "assistant", "content": assistant_msg[:1500]},
        ],
        ensure_ascii=False,
    )

    return (
        "You are tracking a tutoring session's progress against "
        "specific learning objectives.\n\n"
        "Current status:\n" + "\n".join(covered_lines + uncovered_lines) + "\n\n"
        "Latest exchange:\n" + recent_json + "\n\n"
        "Which NOT-YET-COVERED items (if any) were demonstrated with "
        "computational evidence (actual numbers, code execution, or "
        "concrete analysis) in this exchange? "
        'Return ONLY a JSON object: {"newly_covered": [1, 3]} '
        'or {"newly_covered": []} if none were covered.'
    )


# ── LLM call via OpenRouter ────────────────────────────────────────────
import httpx

OPENROUTER_KEY = os.environ.get("OPENROUTER_API_KEY", "")


async def call_llm(model: str, prompt: str) -> str:
    async with httpx.AsyncClient(timeout=30) as client:
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
                "max_tokens": 100,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"]


def parse_response(text: str, tc_items: list[str], covered: list[bool]) -> list[int]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return []
    data = json.loads(match.group())
    indices = data.get("newly_covered", [])
    return [
        i - 1
        for i in indices
        if isinstance(i, int) and 1 <= i <= len(tc_items) and not covered[i - 1]
    ]


# ── Main simulation ────────────────────────────────────────────────────
async def simulate_checker(
    model_name: str, model_id: str, conversation: list, tc_items: list[str]
):
    """Run the TC checker for each exchange and report when each TC item gets covered."""
    covered = [False] * len(tc_items)
    min_check_exchange = max(2, len(tc_items) // 2)  # = 2 for 4 items

    results = []

    # Build exchanges: pair up user/assistant messages
    exchanges = []
    i = 0
    while i < len(conversation) - 1:
        if (
            conversation[i]["role"] == "user"
            and conversation[i + 1]["role"] == "assistant"
        ):
            exchanges.append(
                (conversation[i]["content"], conversation[i + 1]["content"])
            )
            i += 2
        else:
            i += 1

    print(f"\n{'='*70}")
    print(f"Model: {model_name} ({model_id})")
    print(f"TC items: {len(tc_items)}")
    print(f"Exchanges: {len(exchanges)}")
    print(f"Min check exchange: {min_check_exchange}")
    print(f"{'='*70}")

    for ex_idx, (user_msg, asst_msg) in enumerate(exchanges):
        ex_num = ex_idx + 1

        # Gate 1: skip early exchanges
        if ex_num < min_check_exchange:
            print(f"  Exchange {ex_num:2d}: SKIPPED (< min_check={min_check_exchange})")
            results.append(
                {"exchange": ex_num, "action": "skip", "covered": list(covered)}
            )
            continue

        # Gate 2: skip-turn optimization
        coverage_ratio = sum(covered) / len(covered)
        interval = 1 if coverage_ratio >= 0.5 else 2
        # For simulation, we check every exchange (no skip-turn) to get full picture
        # But log what the real checker would do
        would_skip = False
        if ex_num > min_check_exchange:
            # Check if interval would skip this
            last_check = (
                results[-1]["exchange"]
                if results and results[-1]["action"] == "check"
                else 0
            )
            if ex_num - last_check < interval:
                would_skip = True

        prompt = build_checker_prompt(tc_items, covered, user_msg, asst_msg)

        try:
            response = await call_llm(model_id, prompt)
            newly = parse_response(response, tc_items, covered)
            for idx in newly:
                covered[idx] = True

            status = f"newly_covered={[i+1 for i in newly]}" if newly else "none"
            skip_note = " (would be SKIPPED by interval)" if would_skip else ""
            all_covered = all(covered)
            print(
                f"  Exchange {ex_num:2d}: {status:30s} "
                f"bitmap={''.join('■' if c else '□' for c in covered)} "
                f"{'→ TERMINATE!' if all_covered else ''}{skip_note}"
            )
            results.append(
                {
                    "exchange": ex_num,
                    "action": "check",
                    "newly_covered": [i + 1 for i in newly],
                    "covered": list(covered),
                    "all_covered": all_covered,
                    "would_skip": would_skip,
                    "user_preview": user_msg[:100],
                    "response": response,
                }
            )

            if all_covered:
                print(
                    f"\n  ★ TERMINATION at exchange {ex_num} (message pair {ex_num*2-1}-{ex_num*2})"
                )
                break
        except Exception as e:
            print(f"  Exchange {ex_num:2d}: ERROR - {e}")
            results.append({"exchange": ex_num, "action": "error", "error": str(e)})

    if not all(covered):
        uncov = [i + 1 for i, c in enumerate(covered) if not c]
        print(f"\n  ✗ NEVER TERMINATED — uncovered items: {uncov}")

    return results


async def main():
    # Load conversation
    state = json.loads(TRACE_PATH.read_text())
    conversation = state["conversation"]

    # Parse TC
    tc_items = parse_tc_items(TC_TEXT)
    print("Parsed TC items:")
    for i, item in enumerate(tc_items):
        print(f"  ({i+1}) {item[:120]}...")
    print()

    # Run both models
    all_results = {}
    for model_name, model_id in MODELS.items():
        results = await simulate_checker(model_name, model_id, conversation, tc_items)
        all_results[model_name] = results

    # Summary comparison
    print(f"\n{'='*70}")
    print("SUMMARY COMPARISON")
    print(f"{'='*70}")
    for model_name, results in all_results.items():
        term_ex = None
        for r in results:
            if r.get("all_covered"):
                term_ex = r["exchange"]
                break
        coverage_history = []
        for r in results:
            if r["action"] == "check":
                coverage_history.append(
                    f"Ex{r['exchange']}:{''.join('■' if c else '□' for c in r['covered'])}"
                )
        print(f"\n{model_name}:")
        print(f"  Termination: {'Exchange ' + str(term_ex) if term_ex else 'NEVER'}")
        print(f"  Coverage flow: {' → '.join(coverage_history)}")


if __name__ == "__main__":
    asyncio.run(main())
