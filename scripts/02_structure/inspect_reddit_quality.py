#!/usr/bin/env python3
"""
Reddit structured data quality inspection script.

Performs statistical analysis and random sampling on reddit.jsonl
to help evaluate whether the default thresholds produce acceptable data quality.

Evaluation dimensions:
  1. Basic statistics (record count, field completeness)
  2. Text length distribution (question & answer)
  3. Subreddit distribution
  4. Low-quality signal detection (short answers, boilerplate, link-only)
  5. Random sample output for human review
"""

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

# ── Paths ──────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DATA_PATH = PROJECT_ROOT / "data" / "01_structured" / "reddit_hq.jsonl"

# ── Quality thresholds (for flagging, not filtering) ───────────────────
SHORT_ANSWER_WORDS = 60  # answers below this are flagged as "short"
SHORT_QUESTION_WORDS = 15  # questions below this are flagged as "vague"
LINK_HEAVY_RATIO = 0.3  # if >30% of answer is URLs, flag it
BOILERPLATE_PHRASES = [
    "i am a bot",
    "this is not financial advice",
    "not a financial advisor",
    "tldr",
    "edit: thanks for the gold",
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
    idx = int(len(sorted_list) * p)
    idx = min(idx, len(sorted_list) - 1)
    return sorted_list[idx]


# ── Main ───────────────────────────────────────────────────────────────


def main():
    if not DATA_PATH.exists():
        print(f"Error: {DATA_PATH} not found. Run structure_reddit.py first.")
        sys.exit(1)

    print(f"Loading data from {DATA_PATH} ...")
    records = []
    with open(DATA_PATH) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))

    # Only inspect reddit records
    records = [r for r in records if r.get("source_dataset", "").startswith("reddit.")]
    total = len(records)
    print(f"Total Reddit records: {total}\n")

    if total == 0:
        print("No Reddit records found.")
        sys.exit(0)

    # ── 1. Subreddit distribution ──────────────────────────────────────
    sub_counts = Counter(r["source_dataset"] for r in records)
    print("=" * 60)
    print("1. SUBREDDIT DISTRIBUTION")
    print("=" * 60)
    for sub, cnt in sub_counts.most_common():
        pct = cnt / total * 100
        bar = "█" * int(pct / 2)
        print(f"  {sub:<35s} {cnt:>7,d}  ({pct:5.1f}%) {bar}")
    print()

    # ── 2. Text length statistics ──────────────────────────────────────
    q_words = sorted([word_count(r["question_body"]) for r in records])
    a_words = sorted([word_count(r["answer_body"]) for r in records])

    print("=" * 60)
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
    ]
    for field in fields:
        filled = sum(1 for r in records if r.get(field))
        pct = filled / total * 100
        print(f"  {field:<25s} {filled:>7,d} / {total:>7,d}  ({pct:.1f}%)")
    print()

    # ── 4. Quality flags ───────────────────────────────────────────────
    print("=" * 60)
    print("4. QUALITY FLAGS (potential issues)")
    print("=" * 60)

    short_answers = [
        r for r in records if word_count(r["answer_body"]) < SHORT_ANSWER_WORDS
    ]
    short_questions = [
        r for r in records if word_count(r["question_body"]) < SHORT_QUESTION_WORDS
    ]
    link_heavy = [r for r in records if url_ratio(r["answer_body"]) > LINK_HEAVY_RATIO]
    boilerplate = [r for r in records if has_boilerplate(r["answer_body"])]

    flags = [
        (f"Short answers (<{SHORT_ANSWER_WORDS} words)", short_answers),
        (f"Vague questions (<{SHORT_QUESTION_WORDS} words)", short_questions),
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
    print()

    # ── 5. Random samples ─────────────────────────────────────────────
    SAMPLE_SIZE = 5
    print("=" * 60)
    print(f"5. RANDOM SAMPLES ({SAMPLE_SIZE} records for human review)")
    print("=" * 60)

    samples = random.sample(records, min(SAMPLE_SIZE, total))
    for i, r in enumerate(samples, 1):
        print(f"\n--- Sample {i} [{r['source_dataset']}] ---")
        print(f"  Title:    {r.get('title', 'N/A')[:100]}")
        q_preview = r["question_body"][:200].replace("\n", " ")
        a_preview = r["answer_body"][:300].replace("\n", " ")
        print(
            f"  Question: {q_preview}{'...' if len(r['question_body']) > 200 else ''}"
        )
        print(f"  Answer:   {a_preview}{'...' if len(r['answer_body']) > 300 else ''}")
        print(
            f"  Q words:  {word_count(r['question_body'])}  |  A words: {word_count(r['answer_body'])}"
        )
        print(f"  Tags:     {r.get('tags', [])}")
        print(f"  URL:      {r.get('source_url', 'N/A')}")

    # ── 6. Quality flagged samples ─────────────────────────────────────
    print()
    print("=" * 60)
    print("6. FLAGGED SAMPLE (worst-quality examples)")
    print("=" * 60)

    if short_answers:
        worst = sorted(short_answers, key=lambda r: word_count(r["answer_body"]))[:3]
        print("\n  >> Shortest answers:")
        for r in worst:
            a_preview = r["answer_body"][:200].replace("\n", " ")
            print(f"     [{word_count(r['answer_body'])} words] {a_preview}")

    if link_heavy:
        print("\n  >> Link-heavy answer sample:")
        sample = random.sample(link_heavy, min(2, len(link_heavy)))
        for r in sample:
            a_preview = r["answer_body"][:200].replace("\n", " ")
            print(f"     [url_ratio={url_ratio(r['answer_body']):.1%}] {a_preview}")

    # ── 7. Summary & recommendations ──────────────────────────────────
    print()
    print("=" * 60)
    print("7. OVERALL ASSESSMENT")
    print("=" * 60)

    short_ans_pct = len(short_answers) / total * 100
    short_q_pct = len(short_questions) / total * 100
    link_pct = len(link_heavy) / total * 100
    bot_pct = len(boilerplate) / total * 100
    median_a = percentile(a_words, 0.50)

    score = 100
    issues = []

    if short_ans_pct > 20:
        score -= 25
        issues.append(
            f"HIGH: {short_ans_pct:.1f}% of answers are very short -> raise --min-comment-score or increase min_comment_words"
        )
    elif short_ans_pct > 10:
        score -= 10
        issues.append(
            f"MEDIUM: {short_ans_pct:.1f}% of answers are short -> consider raising --min-comment-score"
        )

    if short_q_pct > 30:
        score -= 15
        issues.append(
            f"HIGH: {short_q_pct:.1f}% of questions are vague -> raise --min-post-score"
        )
    elif short_q_pct > 15:
        score -= 5
        issues.append(
            f"MEDIUM: {short_q_pct:.1f}% of questions are vague -> consider raising --min-post-score"
        )

    if link_pct > 15:
        score -= 15
        issues.append(
            f"HIGH: {link_pct:.1f}% of answers are link-heavy -> add URL filtering in structuring"
        )
    elif link_pct > 5:
        score -= 5
        issues.append(f"MEDIUM: {link_pct:.1f}% link-heavy answers")

    if bot_pct > 5:
        score -= 10
        issues.append(
            f"MEDIUM: {bot_pct:.1f}% answers contain boilerplate phrases -> add boilerplate filter"
        )

    if median_a < 80:
        score -= 10
        issues.append(
            f"LOW: median answer length is only {median_a:.0f} words -> raise quality thresholds"
        )

    score = max(0, score)

    if score >= 80:
        verdict = "GOOD - data quality is acceptable for most use cases"
    elif score >= 60:
        verdict = "FAIR - usable but would benefit from threshold adjustments"
    elif score >= 40:
        verdict = "POOR - significant quality issues, threshold adjustment recommended"
    else:
        verdict = "BAD - data needs major filtering or re-structuring"

    print(f"\n  Quality Score: {score}/100")
    print(f"  Verdict: {verdict}")

    if issues:
        print(f"\n  Issues found ({len(issues)}):")
        for issue in issues:
            print(f"    - {issue}")
    else:
        print("\n  No significant quality issues detected.")

    print()
    print("  Suggested next steps:")
    if score >= 80:
        print("    - Current default thresholds are working well")
        print("    - Review the random samples above for subjective quality")
    else:
        if short_ans_pct > 10:
            print(
                "    - Re-run with higher comment score: --min-comment-score 15 or 20"
            )
        if short_q_pct > 15:
            print("    - Re-run with higher post score: --min-post-score 25 or 50")
        if link_pct > 5:
            print("    - Consider adding URL-ratio filtering in structure_reddit.py")
        if bot_pct > 5:
            print("    - Consider adding boilerplate detection filter")
        print("    - After adjustments, re-run this script to compare")

    print()


if __name__ == "__main__":
    main()
