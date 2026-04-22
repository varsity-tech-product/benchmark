#!/usr/bin/env python3
"""
Classify structured financial data into 7 categories by data source.

Reads all *.jsonl files from data/01_structured/ and splits records into
data/01_structured/classified/<category>.jsonl based on source_dataset.

Does NOT modify original data files.

Categories:
  1. reddit              - Reddit community Q&A (all subreddits)
  2. fiqa                - Financial Industry QA (open-ended expert Q&A)
  3. authoritative_docs  - Official regulatory body content (SEC, FINRA, CFPB)
  4. stack_exchange      - Money Stack Exchange expert Q&A
  5. finqa               - Financial report numerical reasoning
  6. tatqa               - Table-based financial Q&A
  7. convfinqa           - Conversational multi-turn financial reasoning

Usage:
    python classify_data.py
"""

import json
import sys
from collections import Counter
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
INPUT_DIR = PROJECT_ROOT / "data" / "01_structured"
OUTPUT_DIR = INPUT_DIR / "classified"

# ---------------------------------------------------------------------------
# 7 Categories
# ---------------------------------------------------------------------------
CATEGORIES = [
    "reddit",
    "fiqa",
    "authoritative_docs",
    "stack_exchange",
    "finqa",
    "tatqa",
    "convfinqa",
]

# ---------------------------------------------------------------------------
# source_dataset value -> category mapping
# ---------------------------------------------------------------------------
SOURCE_TO_CATEGORY = {
    # Reddit (6 subreddits)
    "reddit.personalfinance": "reddit",
    "reddit.financialindependence": "reddit",
    "reddit.investing": "reddit",
    "reddit.stocks": "reddit",
    "reddit.realestateinvesting": "reddit",
    "reddit.tax": "reddit",
    # Research datasets
    "fiqa": "fiqa",
    "finqa": "finqa",
    "tatqa": "tatqa",
    "convfinqa": "convfinqa",
    # Authoritative docs
    "sec_investor_gov": "authoritative_docs",
    "finra": "authoritative_docs",
    "cfpb": "authoritative_docs",
    # Stack Exchange
    "money.stackexchange": "stack_exchange",
}


def main():
    """Main entry point."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Discover input files (only top-level .jsonl, not from classified/)
    input_files = sorted(p for p in INPUT_DIR.glob("*.jsonl") if p.parent == INPUT_DIR)

    if not input_files:
        print(f"ERROR: No .jsonl files found in {INPUT_DIR}")
        sys.exit(1)

    print(f"Input directory:  {INPUT_DIR}")
    print(f"Output directory: {OUTPUT_DIR}")
    print(f"Input files:      {[f.name for f in input_files]}")
    print()

    # Tracking
    total_records = 0
    category_counts = Counter()
    source_counts = Counter()
    unmapped = Counter()

    # Open output file handles
    output_handles = {}
    for cat in CATEGORIES:
        output_handles[cat] = open(OUTPUT_DIR / f"{cat}.jsonl", "w", encoding="utf-8")

    try:
        for input_file in input_files:
            print(f"Processing {input_file.name} ...")
            file_count = 0

            with open(input_file, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError as e:
                        print(f"  WARNING: Skipping line {line_num} (JSON error: {e})")
                        continue

                    source_dataset = record.get("source_dataset", "")
                    category = SOURCE_TO_CATEGORY.get(source_dataset)

                    if category is None:
                        unmapped[source_dataset] += 1
                        continue

                    output_handles[category].write(
                        json.dumps(record, ensure_ascii=False) + "\n"
                    )

                    total_records += 1
                    file_count += 1
                    category_counts[category] += 1
                    source_counts[source_dataset] += 1

            print(f"  -> {file_count:,} records classified")
    finally:
        for fh in output_handles.values():
            fh.close()

    # ---- Summary ----
    print(f"\n{'=' * 65}")
    print("CLASSIFICATION SUMMARY")
    print(f"{'=' * 65}")
    print(f"Total records: {total_records:,}\n")

    print(f"  {'Category':<25} {'Records':>10} {'Percent':>8}")
    print(f"  {'-' * 25} {'-' * 10} {'-' * 8}")
    for cat in CATEGORIES:
        count = category_counts[cat]
        pct = count / total_records * 100 if total_records else 0
        print(f"  {cat:<25} {count:>10,} {pct:>7.1f}%")

    print(f"\n{'=' * 65}")
    print("SOURCE_DATASET BREAKDOWN")
    print(f"{'=' * 65}")
    for source in sorted(source_counts.keys()):
        cat = SOURCE_TO_CATEGORY[source]
        print(f"  {source:<35} -> {cat:<20} ({source_counts[source]:>7,})")

    if unmapped:
        print("\nWARNING: Unmapped source_dataset values:")
        for src, cnt in unmapped.most_common():
            print(f"  {src}: {cnt:,} records SKIPPED")

    # Output files
    print(f"\n{'=' * 65}")
    print(f"Output: {OUTPUT_DIR}/")
    for cat in CATEGORIES:
        print(f"  {cat}.jsonl  ({category_counts[cat]:,} records)")

    # Verification
    total_output = sum(category_counts.values())
    if total_output == total_records:
        print(f"\nVERIFICATION PASSED: {total_output:,} == {total_records:,}")
    else:
        print(f"\nVERIFICATION FAILED: {total_output:,} != {total_records:,}")
        sys.exit(1)


if __name__ == "__main__":
    main()
