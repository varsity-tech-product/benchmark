#!/usr/bin/env python3
"""
General-purpose structured data quality inspection script.

Works with any JSONL file following the StructuredQA schema.

Usage:
  python inspect_quality.py --file data/01_structured/stack_exchange.jsonl
  python inspect_quality.py  # defaults to all JSONL files in 01_structured/
"""

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
STRUCTURED_DIR = PROJECT_ROOT / "data" / "01_structured"

# ── Quality thresholds (for flagging, not filtering) ───────────────────
SHORT_ANSWER_WORDS = 30
SHORT_QUESTION_WORDS = 10
LINK_HEAVY_RATIO = 0.3
BOILERPLATE_PHRASES = [
    "i am a bot",
    "this is not financial advice",
    "not a financial advisor",
    "tldr",
    "edit: thanks for the gold",
    "[removed]",
    "[deleted]",
]

# ── Helpers ────────────────────────────────────────────────────────────


def word_count(text: str) -> int:
    return len(text.split())


def url_ratio(text: str) -> float:
    urls = re.findall(r"https?://\S+", text)
    url_chars = sum(len(u) for u in urls)
    return url_chars / max(len(text), 1)


def has_boilerplate(text: str) -> bool:
    lower = text.lower()
    return any(phrase in lower for phrase in BOILERPLATE_PHRASES)


def percentile(sorted_list: list, p: float) -> float:
    if not sorted_list:
        return 0
    idx = min(int(len(sorted_list) * p), len(sorted_list) - 1)
    return sorted_list[idx]


def inspect_file(filepath: Path) -> int:
    """Inspect a single JSONL file. Returns quality score (0-100)."""

    filename = filepath.name
    print("\n" + "#" * 70)
    print(f"#  INSPECTING: {filename}")
    print("#" * 70)

    if not filepath.exists():
        print(f"  ERROR: file not found at {filepath}")
        return -1

    records = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    total = len(records)
    print(f"\nTotal records: {total:,d}")

    if total == 0:
        print("  No records found. Skipping.")
        return -1

    # ── 1. Source distribution ─────────────────────────────────────────
    src_counts = Counter(r.get("source_dataset", "unknown") for r in records)
    print("\n" + "=" * 60)
    print("1. SOURCE DISTRIBUTION")
    print("=" * 60)
    for src, cnt in src_counts.most_common():
        pct = cnt / total * 100
        bar = "█" * max(1, int(pct / 2))
        print(f"  {src:<40s} {cnt:>7,d}  ({pct:5.1f}%) {bar}")

    # ── 2. Text length statistics ──────────────────────────────────────
    q_words = sorted([word_count(r.get("question_body", "")) for r in records])
    a_words = sorted([word_count(r.get("answer_body", "")) for r in records])

    print("\n" + "=" * 60)
    print("2. TEXT LENGTH DISTRIBUTION (word count)")
    print("=" * 60)
    for label, data in [("Question", q_words), ("Answer", a_words)]:
        avg = sum(data) / len(data)
        print(f"  {label}:")
        print(f"    Min:    {data[0]:>8,d}")
        print(f"    P25:    {percentile(data, 0.25):>8,.0f}")
        print(f"    Median: {percentile(data, 0.50):>8,.0f}")
        print(f"    P75:    {percentile(data, 0.75):>8,.0f}")
        print(f"    P95:    {percentile(data, 0.95):>8,.0f}")
        print(f"    Max:    {data[-1]:>8,d}")
        print(f"    Mean:   {avg:>8,.1f}")
        print()

    # ── 3. Field completeness ──────────────────────────────────────────
    print("=" * 60)
    print("3. FIELD COMPLETENESS")
    print("=" * 60)
    fields = [
        "title",
        "question_body",
        "answer_body",
        "tags",
        "creation_date",
        "source_url",
        "context",
    ]
    for field in fields:
        filled = sum(1 for r in records if r.get(field))
        pct = filled / total * 100
        print(f"  {field:<25s} {filled:>7,d} / {total:>7,d}  ({pct:.1f}%)")

    # ── 4. Quality flags ───────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("4. QUALITY FLAGS (potential issues)")
    print("=" * 60)

    short_answers = [
        r for r in records if word_count(r.get("answer_body", "")) < SHORT_ANSWER_WORDS
    ]
    short_questions = [
        r
        for r in records
        if word_count(r.get("question_body", "")) < SHORT_QUESTION_WORDS
    ]
    link_heavy = [
        r for r in records if url_ratio(r.get("answer_body", "")) > LINK_HEAVY_RATIO
    ]
    boilerplate = [r for r in records if has_boilerplate(r.get("answer_body", ""))]
    empty_answers = [r for r in records if not r.get("answer_body", "").strip()]
    empty_questions = [r for r in records if not r.get("question_body", "").strip()]

    flags = [
        ("Empty questions", empty_questions),
        ("Empty answers", empty_answers),
        (f"Short answers (<{SHORT_ANSWER_WORDS} words)", short_answers),
        (f"Short questions (<{SHORT_QUESTION_WORDS} words)", short_questions),
        (f"Link-heavy answers (>{LINK_HEAVY_RATIO*100:.0f}% URLs)", link_heavy),
        ("Boilerplate/bot-like answers", boilerplate),
    ]

    issue_total = 0
    for label, items in flags:
        cnt = len(items)
        pct = cnt / total * 100
        issue_total += cnt
        status = "⚠" if pct > 10 else "✓"
        print(f"  {status} {label:<45s} {cnt:>7,d}  ({pct:5.1f}%)")

    print(f"\n  Combined flagged records (may overlap): ~{issue_total:,d}")

    # ── 5. Random samples ─────────────────────────────────────────────
    SAMPLE_SIZE = 3
    print("\n" + "=" * 60)
    print(f"5. RANDOM SAMPLES ({SAMPLE_SIZE} records for human review)")
    print("=" * 60)

    samples = random.sample(records, min(SAMPLE_SIZE, total))
    for i, r in enumerate(samples, 1):
        print(f"\n--- Sample {i} [{r.get('source_dataset', 'unknown')}] ---")
        print(f"  Title:    {(r.get('title') or 'N/A')[:100]}")
        q_preview = (r.get("question_body") or "")[:200].replace("\n", " ")
        a_preview = (r.get("answer_body") or "")[:300].replace("\n", " ")
        print(
            f"  Question: {q_preview}{'...' if len(r.get('question_body', '')) > 200 else ''}"
        )
        print(
            f"  Answer:   {a_preview}{'...' if len(r.get('answer_body', '')) > 300 else ''}"
        )
        print(
            f"  Q words:  {word_count(r.get('question_body', ''))}  |  A words: {word_count(r.get('answer_body', ''))}"
        )
        print(f"  Tags:     {r.get('tags', [])}")

    # ── 6. Flagged samples ─────────────────────────────────────────────
    if short_answers:
        print("\n" + "=" * 60)
        print("6. WORST-QUALITY SAMPLES")
        print("=" * 60)
        worst = sorted(
            short_answers, key=lambda r: word_count(r.get("answer_body", ""))
        )[:3]
        print("\n  >> Shortest answers:")
        for r in worst:
            a_preview = (r.get("answer_body") or "")[:200].replace("\n", " ")
            print(f"     [{word_count(r.get('answer_body', ''))} words] {a_preview}")

    # ── 7. Scoring ─────────────────────────────────────────────────────
    short_ans_pct = len(short_answers) / total * 100
    short_q_pct = len(short_questions) / total * 100
    link_pct = len(link_heavy) / total * 100
    bot_pct = len(boilerplate) / total * 100
    empty_q_pct = len(empty_questions) / total * 100
    empty_a_pct = len(empty_answers) / total * 100
    median_a = percentile(a_words, 0.50)

    score = 100
    issues = []

    if empty_q_pct > 1:
        score -= 20
        issues.append(f"HIGH: {empty_q_pct:.1f}% empty questions")
    if empty_a_pct > 1:
        score -= 20
        issues.append(f"HIGH: {empty_a_pct:.1f}% empty answers")
    if short_ans_pct > 20:
        score -= 25
        issues.append(f"HIGH: {short_ans_pct:.1f}% of answers are very short")
    elif short_ans_pct > 10:
        score -= 10
        issues.append(f"MEDIUM: {short_ans_pct:.1f}% of answers are short")
    if short_q_pct > 30:
        score -= 15
        issues.append(f"HIGH: {short_q_pct:.1f}% of questions are vague/short")
    elif short_q_pct > 15:
        score -= 5
        issues.append(f"MEDIUM: {short_q_pct:.1f}% of questions are vague/short")
    if link_pct > 15:
        score -= 15
        issues.append(f"HIGH: {link_pct:.1f}% of answers are link-heavy")
    elif link_pct > 5:
        score -= 5
        issues.append(f"MEDIUM: {link_pct:.1f}% link-heavy answers")
    if bot_pct > 5:
        score -= 10
        issues.append(f"MEDIUM: {bot_pct:.1f}% boilerplate answers")
    if median_a < 30:
        score -= 15
        issues.append(f"LOW: median answer length is only {median_a:.0f} words")
    elif median_a < 50:
        score -= 5
        issues.append(f"MEDIUM: median answer length is only {median_a:.0f} words")

    score = max(0, score)

    print("\n" + "=" * 60)
    print("7. OVERALL ASSESSMENT")
    print("=" * 60)

    if score >= 80:
        verdict = "GOOD"
    elif score >= 60:
        verdict = "FAIR - would benefit from improvements"
    elif score >= 40:
        verdict = "POOR - significant quality issues"
    else:
        verdict = "BAD - needs major filtering or re-structuring"

    print(f"\n  Quality Score: {score}/100  [{verdict}]")

    if issues:
        print(f"\n  Issues ({len(issues)}):")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("\n  No significant quality issues detected.")

    print()
    return score


# ── Main ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="Inspect StructuredQA data quality")
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Path to a specific JSONL file (default: inspect all files in 01_structured/)",
    )
    args = parser.parse_args()

    if args.file:
        files = [args.file]
    else:
        files = sorted(STRUCTURED_DIR.glob("*.jsonl"))

    if not files:
        print(f"No JSONL files found in {STRUCTURED_DIR}")
        sys.exit(1)

    random.seed(42)
    results = {}
    for f in files:
        score = inspect_file(f)
        if score >= 0:
            results[f.name] = score

    # ── Final summary ──────────────────────────────────────────────────
    if len(results) > 1:
        print("\n" + "#" * 70)
        print("#  SUMMARY ACROSS ALL DATASETS")
        print("#" * 70)
        for name, score in sorted(results.items(), key=lambda x: x[1]):
            if score >= 80:
                badge = "GOOD"
            elif score >= 60:
                badge = "FAIR"
            elif score >= 40:
                badge = "POOR"
            else:
                badge = "BAD "
            bar = "█" * (score // 5)
            print(f"  [{badge}] {score:>3d}/100  {name:<35s} {bar}")
        print()


if __name__ == "__main__":
    main()
