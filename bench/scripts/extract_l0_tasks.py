#!/usr/bin/env python3
"""
Extract L0 task data directly from structured raw data.

Replaces ``bench/scripts/synthesize_tasks.py`` for v3 L0 generation.
No LLM calls — pure extraction + filter + sample.

Plan B (per docs/benchmark_v7_plan.md): 3 categories, 100 tasks total.

| Category               | Source             | Count | Domain filter |
|------------------------|--------------------|-------|---------------|
| multi_step_reasoning   | finqa              | 25    | bypass        |
| multi_step_reasoning   | convfinqa          | 10    | bypass        |
| data_interpretation    | tatqa              | 30    | bypass        |
| conceptual_qa          | fiqa               | 25    | strict        |
| conceptual_qa          | stack_exchange     | 5     | strict        |
| conceptual_qa          | authoritative_docs | 5     | strict        |

Output: bench/tasks/L0/{category}/*.json (v3 single-turn task schema).
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
SOURCE_DIR = PROJECT_ROOT / "data" / "01_structured" / "classified"
OUTPUT_DIR = PROJECT_ROOT / "bench" / "tasks" / "L0"
SEED = 20260504

# ── Manual review blacklist ───────────────────────────────────────────
# Source IDs rejected during human review (see review notes 2026-05-04).
# Skipped during selection so the pipeline pulls the next-best candidate
# from the deterministic shuffle.
EXCLUDE_SOURCE_IDS = {
    # fiqa: shallow / off-domain / non-answer responses
    "fiqa_7501_65134",  # 2-sentence fragment
    "fiqa_7750_145383",  # "fix the roof" personal advice, not quant
    "fiqa_9179_442179",  # "go to a library and ask for WSJ from 1992" joke
    "fiqa_9316_182462",  # answer doesn't address the question
    "fiqa_6793_41577",  # answer is just a bogleheads link
    "fiqa_6958_569530",  # answer is just a NYSE calendar link
    # money.stackexchange: shallow data-quirk Q&A
    "72825",  # Home Depot 1988 Google Finance bug
    # tatqa: not a financial reasoning question
    "tatqa_train_594_3",  # average age of executive officers
    # finqa / convfinqa: noisy labels in the upstream dataset
    "finqa_train_1087",  # market cap conflated holders with shares
    "finqa_train_1700",  # "lease length" should be "license length"
    "finqa_train_3018",  # reasoning string (avg) inconsistent with answer (delta)
    "convfinqa_train_776",  # answer field is the 2009 value, not the % change
    "convfinqa_train_1228",  # answer 10 but reasoning gives 11 (19.8 - 8.8)
}

# ── Domain filters ────────────────────────────────────────────────────

QUANT_PATTERNS = re.compile(
    r"\b(?:"
    r"sharpe|sortino|alpha|beta|vol(?:atility)?|drawdown|backtest|"
    r"options?|futures?|derivatives?|hedge|hedging|arbitrage|"
    r"portfolio|asset[- ]allocation|risk[- ]management|var|cvar|"
    r"technical[- ]analysis|moving[- ]average|momentum|mean[- ]reversion|"
    r"quant(?:itative)?|algorithmic|trading|stock|bond|equity|equities|"
    r"dividend|earnings|p/e|valuation|cash[- ]flow|balance[- ]sheet|"
    r"income[- ]statement|10-?k|10-?q|annual[- ]report|quarterly[- ]report|"
    r"returns?|yield|interest[- ]rate|libor|treasury|bond[- ]market|"
    r"market[- ]cap|capital[- ]gains|short[- ]selling|leverage|margin|"
    r"efficient[- ]market|capm|black[- ]scholes|monte[- ]carlo|cointegration|"
    r"correlation|regression|factor|alpha101|fama[- ]french"
    r")\b",
    re.IGNORECASE,
)

OFF_DOMAIN_PATTERNS = re.compile(
    r"\b(?:"
    r"mortgage|escrow|401k|ira|retirement[- ]account|roth|"
    r"credit[- ]card|fico|credit[- ]score|debt[- ]consolidat|loan[- ]forgiveness|"
    r"salary|wage|employ(?:ment|er|ee)|paycheck|w-?2|w-?4|1099|"
    r"rent|landlord|tenant|lease|housing[- ]market|home[- ]buyer|first[- ]home|"
    r"student[- ]loan|tuition|college|fafsa|"
    r"tax(?:es|able|ation)?|irs|deduct|filing|exemption|"
    r"insurance|premium|copay|deductible|life[- ]insurance|health[- ]insurance|"
    r"budget|saving[- ]account|savings[- ]rate|debit|atm|"
    r"social[- ]security|medicare|medicaid|food[- ]stamp|"
    r"divorce|inheritance|estate[- ]planning|will|trust[- ]fund|"
    # Commerce / payment platforms
    r"ebay|paypal|dwolla|amazon|etsy|shopify|venmo|zelle|"
    # Consumer loans / refinancing
    r"refinanc|car[- ]loan|auto[- ]loan|personal[- ]loan|"
    # "Make X/month" income generation patterns
    r"\$\d+[/\\](?:month|day|week|year)|per[- ]month[- ]every|"
    r"generate[- ]\$|monthly[- ]income[- ]from|make[- ]money[- ]from|"
    # Personal banking / utilities (not quant)
    r"checking[- ]account|savings[- ]account|bank[- ]account|"
    r"online[- ]bank(?:ing)?|transferring[- ]money|wire[- ]transfer|"
    r"energy[- ]provider|energy[- ]supplier|electricity[- ]bill|gas[- ]bill|"
    r"utility[- ]bill|water[- ]bill|phone[- ]bill"
    r")\b",
    re.IGNORECASE,
)


# ── Filters ───────────────────────────────────────────────────────────

MIN_Q_LEN, MAX_Q_LEN = 30, 1500
MIN_A_LEN, MAX_A_LEN = 50, 4000

REJECTION_PHRASES = (
    "i don't know",
    "not sure",
    "unclear",
    "i'm not sure",
    "i can't tell",
    "no idea",
    "[deleted]",
    "[removed]",
)


def passes_quality(record: dict) -> bool:
    q = (record.get("question_body") or "").strip()
    a = (record.get("answer_body") or "").strip()
    if not q or not a:
        return False
    if not (MIN_Q_LEN <= len(q) <= MAX_Q_LEN):
        return False
    if not (MIN_A_LEN <= len(a) <= MAX_A_LEN):
        return False
    a_lower = a.lower()
    if any(a_lower.startswith(p) for p in REJECTION_PHRASES):
        return False
    return True


def passes_domain(record: dict) -> bool:
    """Strict quant filter for fiqa / stack_exchange / authoritative_docs.

    Requires quant keyword in **title or question_body** (not just answer).
    This rejects records where the question is off-domain but the answer
    happens to mention a quant term tangentially.

    Rejects if any off-domain keyword appears anywhere in title + question + answer.
    """
    title = record.get("title") or ""
    question = record.get("question_body") or ""
    answer = record.get("answer_body") or ""

    question_side = (title + " " + question).lower()
    full_text = (title + " " + question + " " + answer).lower()

    has_quant_in_question = bool(QUANT_PATTERNS.search(question_side))
    has_off = bool(OFF_DOMAIN_PATTERNS.search(full_text))
    return has_quant_in_question and not has_off


# ── Plan B distribution ───────────────────────────────────────────────

PLAN: list[dict] = [
    {
        "source": "finqa",
        "category": "multi_step_reasoning",
        "count": 25,
        "domain_filter": False,
    },
    {
        "source": "convfinqa",
        "category": "multi_step_reasoning",
        "count": 10,
        "domain_filter": False,
    },
    {
        "source": "tatqa",
        "category": "data_interpretation",
        "count": 30,
        "domain_filter": False,
    },
    {"source": "fiqa", "category": "conceptual_qa", "count": 25, "domain_filter": True},
    {
        "source": "stack_exchange",
        "category": "conceptual_qa",
        "count": 5,
        "domain_filter": True,
    },
    {
        "source": "authoritative_docs",
        "category": "conceptual_qa",
        "count": 5,
        "domain_filter": True,
    },
]


# ── Schema conversion ────────────────────────────────────────────────


def to_l0_task(record: dict, category: str) -> dict:
    """Convert structured record to v3 L0 task JSON."""
    source_id = record.get("source_id", "unknown")
    source_dataset = record.get("source_dataset", "unknown")

    # Stable task_id derived from source_id; replace problematic characters.
    safe_id = re.sub(r"[^A-Za-z0-9_\-]", "_", str(source_id))
    task_id = f"L0_{source_dataset}_{safe_id}"

    task: dict = {
        "task_id": task_id,
        "version": "3.0",
        "layer": "L0",
        "category": category,
        "task_type": "single_turn",
        "requires_tool": False,
        "question": (record.get("question_body") or "").strip(),
        "reference_answer": (record.get("answer_body") or "").strip(),
        "source_dataset": source_dataset,
        "source_id": source_id,
        "tags": list(record.get("tags") or []),
    }

    # Optional fields
    title = record.get("title")
    if title:
        task["title"] = title.strip()

    context = record.get("context")
    if context and context.strip():
        task["context"] = context.strip()

    conv = record.get("conversation_history")
    if conv:
        # convfinqa stores prior turns; preserve verbatim for downstream use.
        task["conversation_history"] = conv

    return task


# ── Extraction core ──────────────────────────────────────────────────


def load_records(source: str) -> list[dict]:
    path = SOURCE_DIR / f"{source}.jsonl"
    if not path.exists():
        raise FileNotFoundError(f"Source file missing: {path}")
    out = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def extract_for_entry(entry: dict, rng: random.Random) -> list[dict]:
    source = entry["source"]
    category = entry["category"]
    target = entry["count"]
    use_domain = entry["domain_filter"]

    records = load_records(source)
    initial = len(records)

    # Apply quality filter first
    quality = [r for r in records if passes_quality(r)]
    after_quality = len(quality)

    # Apply domain filter for sources where keyword precision is needed
    if use_domain:
        candidates = [r for r in quality if passes_domain(r)]
    else:
        candidates = quality
    after_domain = len(candidates)

    if len(candidates) < target:
        print(
            f"  WARN {source}: only {len(candidates)} candidates after filters, "
            f"target {target}",
            file=sys.stderr,
        )

    # Stable sample. Walk the shuffled list, skip blacklisted source_ids so
    # rejections during human review pull the next-best candidate without
    # disturbing the order of the keepers.
    rng.shuffle(candidates)
    selected = []
    skipped = 0
    for r in candidates:
        if str(r.get("source_id")) in EXCLUDE_SOURCE_IDS:
            skipped += 1
            continue
        selected.append(r)
        if len(selected) >= target:
            break

    print(
        f"  {source:20s} → {category:24s}  "
        f"raw={initial:6d}  quality={after_quality:6d}  "
        f"domain={after_domain:6d}  skipped={skipped:2d}  selected={len(selected):3d}"
    )
    return [to_l0_task(r, category) for r in selected]


def main() -> None:
    rng = random.Random(SEED)

    if not SOURCE_DIR.exists():
        print(f"ERROR: source dir not found: {SOURCE_DIR}", file=sys.stderr)
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Output dir: {OUTPUT_DIR}")
    print("Extraction plan:")
    print(f"{'source':20s} {'category':24s}  raw     quality domain  selected")

    all_tasks: list[dict] = []
    for entry in PLAN:
        all_tasks.extend(extract_for_entry(entry, rng))

    # Group by category for directory layout
    by_category: dict[str, list[dict]] = {}
    for t in all_tasks:
        by_category.setdefault(t["category"], []).append(t)

    # Write files
    for cat, tasks in by_category.items():
        cat_dir = OUTPUT_DIR / cat
        cat_dir.mkdir(parents=True, exist_ok=True)
        # Clean stale files in this dir to avoid mixing old/new (idempotent regen).
        for stale in cat_dir.glob("*.json"):
            stale.unlink()
        for t in tasks:
            out_path = cat_dir / f"{t['task_id']}.json"
            out_path.write_text(json.dumps(t, indent=2, ensure_ascii=False))

    # Summary
    print()
    print(f"Total tasks written: {len(all_tasks)}")
    for cat, tasks in sorted(by_category.items()):
        print(f"  {cat:24s}  {len(tasks):3d}")
    print(f"Output root: {OUTPUT_DIR.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
