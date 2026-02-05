#!/usr/bin/env python3
"""
Create a dataset card with statistics for the benchmark dataset.

Generates a DATASET_CARD.md file with:
- Total records and source distribution
- Teacher model distribution
- Average text lengths
- Sample records
"""

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Optional

from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.schemas import FinalBenchmarkRecord

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
PACKAGED_DIR = PROJECT_ROOT / "data" / "03_packaged"


def compute_statistics(input_path: Path) -> dict:
    """
    Compute statistics from the dataset.

    Args:
        input_path: Path to JSONL file

    Returns:
        Dictionary of statistics
    """
    stats = {
        "total_records": 0,
        "source_distribution": Counter(),
        "teacher_model_distribution": Counter(),
        "knowledge_level_distribution": Counter(),
        "tag_distribution": Counter(),
        "text_lengths": {
            "question": [],
            "reference_answer": [],
            "synthetic_response": [],
        },
        "with_context": 0,
        "with_conversation_history": 0,
        "sample_records": [],
    }

    with open(input_path) as f:
        for i, line in enumerate(tqdm(f, desc="Computing statistics")):
            record = json.loads(line)
            stats["total_records"] += 1

            # Source distribution
            stats["source_distribution"][record["source_dataset"]] += 1

            # Teacher model distribution
            stats["teacher_model_distribution"][record["teacher_model"]] += 1

            # Knowledge level
            if "learner_profile" in record:
                stats["knowledge_level_distribution"][
                    record["learner_profile"]["knowledge_level"]
                ] += 1

            # Tags
            for tag in record.get("tags", []):
                stats["tag_distribution"][tag] += 1

            # Text lengths (word count)
            stats["text_lengths"]["question"].append(
                len(record["question"].split())
            )
            stats["text_lengths"]["reference_answer"].append(
                len(record["reference_answer"].split())
            )
            stats["text_lengths"]["synthetic_response"].append(
                len(record["synthetic_response"].split())
            )

            # Context presence
            if record.get("context"):
                stats["with_context"] += 1
            if record.get("conversation_history"):
                stats["with_conversation_history"] += 1

            # Sample records (first 3)
            if len(stats["sample_records"]) < 3:
                stats["sample_records"].append(record)

    # Compute averages
    for key in stats["text_lengths"]:
        lengths = stats["text_lengths"][key]
        if lengths:
            stats["text_lengths"][key] = {
                "mean": sum(lengths) / len(lengths),
                "min": min(lengths),
                "max": max(lengths),
            }

    return stats


def format_counter(counter: Counter, top_n: int = 10) -> str:
    """Format a counter as a markdown table."""
    lines = ["| Item | Count | Percentage |", "|------|-------|------------|"]
    total = sum(counter.values())

    for item, count in counter.most_common(top_n):
        pct = 100 * count / total if total > 0 else 0
        lines.append(f"| {item} | {count:,} | {pct:.1f}% |")

    return "\n".join(lines)


def generate_dataset_card(stats: dict, output_path: Path):
    """Generate the dataset card markdown file."""
    card = f"""# Quant Tutor Benchmark Dataset

## Overview

This dataset contains financial Q&A pairs augmented with synthesized tutoring components:
- **Learner Profiles**: Inferred knowledge level, background, and learning goals
- **Tutoring Strategies**: Pedagogical approaches and teaching plans
- **Synthetic Responses**: AI-generated tutoring responses

**Generated**: {datetime.now().strftime("%Y-%m-%d %H:%M:%S UTC")}

## Statistics

### Dataset Size

| Metric | Value |
|--------|-------|
| Total Records | {stats['total_records']:,} |
| With Context | {stats['with_context']:,} ({100*stats['with_context']/max(stats['total_records'],1):.1f}%) |
| With Conversation History | {stats['with_conversation_history']:,} ({100*stats['with_conversation_history']/max(stats['total_records'],1):.1f}%) |

### Source Distribution

{format_counter(stats['source_distribution'])}

### Teacher Model Distribution

{format_counter(stats['teacher_model_distribution'])}

### Knowledge Level Distribution

{format_counter(stats['knowledge_level_distribution'])}

### Top Tags

{format_counter(stats['tag_distribution'], top_n=15)}

### Text Length Statistics (words)

| Field | Mean | Min | Max |
|-------|------|-----|-----|
| Question | {stats['text_lengths']['question']['mean']:.0f} | {stats['text_lengths']['question']['min']} | {stats['text_lengths']['question']['max']} |
| Reference Answer | {stats['text_lengths']['reference_answer']['mean']:.0f} | {stats['text_lengths']['reference_answer']['min']} | {stats['text_lengths']['reference_answer']['max']} |
| Synthetic Response | {stats['text_lengths']['synthetic_response']['mean']:.0f} | {stats['text_lengths']['synthetic_response']['min']} | {stats['text_lengths']['synthetic_response']['max']} |

## Schema

Each record contains:

```json
{{
  "id": "unique_record_id",
  "source_id": "original_source_id",
  "source_dataset": "dataset_name",
  "title": "optional_title",
  "question": "original_question_text",
  "reference_answer": "original_answer_text",
  "tags": ["tag1", "tag2"],
  "source_url": "https://...",
  "context": "optional_context_tables_etc",
  "conversation_history": [optional_prior_turns],
  "learner_profile": {{
    "knowledge_level": "beginner|intermediate|advanced",
    "financial_background": "description",
    "learning_goals": ["goal1", "goal2"],
    "potential_misconceptions": ["misconception1"],
    "emotional_context": "description"
  }},
  "tutoring_strategy": {{
    "approach": "Socratic|direct_instruction|...",
    "steps": ["step1", "step2", "step3"],
    "key_concepts": ["concept1", "concept2"],
    "analogies_or_examples": ["analogy1"],
    "follow_up_questions": ["question1"]
  }},
  "synthetic_response": "AI_generated_tutoring_response",
  "teacher_model": "model_used_for_synthesis",
  "synthesis_timestamp": "ISO_timestamp"
}}
```

## Sample Records

"""

    # Add sample records
    for i, record in enumerate(stats["sample_records"], 1):
        card += f"""### Sample {i}

**Source**: {record['source_dataset']}
**Question**: {record['question'][:300]}{'...' if len(record['question']) > 300 else ''}

**Learner Profile**:
- Knowledge Level: {record['learner_profile']['knowledge_level']}
- Background: {record['learner_profile']['financial_background']}

**Tutoring Strategy**:
- Approach: {record['tutoring_strategy']['approach']}
- Key Concepts: {', '.join(record['tutoring_strategy']['key_concepts'][:3])}

**Synthetic Response** (truncated):
{record['synthetic_response'][:500]}{'...' if len(record['synthetic_response']) > 500 else ''}

---

"""

    card += """## Usage

```python
import json

# Load dataset
with open('quant_tutor_benchmark.jsonl') as f:
    records = [json.loads(line) for line in f]

# Example: filter by knowledge level
beginner_records = [
    r for r in records
    if r['learner_profile']['knowledge_level'] == 'beginner'
]
```

## Citation

If you use this dataset, please cite:

```bibtex
@dataset{quant_tutor_benchmark,
  title={Quant Tutor Benchmark Dataset},
  year={2024},
  description={Financial QA dataset with synthesized tutoring components}
}
```

## License

This dataset is derived from multiple sources with varying licenses:
- Stack Exchange data: CC BY-SA 4.0
- Research datasets: See original dataset licenses
- Government sources (SEC, CFPB, FINRA): Public domain

Please verify compliance with original source licenses before use.
"""

    # Write the card
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write(card)

    print(f"Dataset card written to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Create dataset card with statistics"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=PACKAGED_DIR / "quant_tutor_benchmark.jsonl",
        help="Input validated JSONL file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PACKAGED_DIR / "DATASET_CARD.md",
        help="Output markdown file",
    )

    args = parser.parse_args()

    # Check input exists
    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}")
        print("Run validate_schema.py first")
        sys.exit(1)

    print(f"Reading: {args.input}")

    stats = compute_statistics(args.input)
    generate_dataset_card(stats, args.output)

    print("\n" + "=" * 60)
    print("Dataset Card Summary")
    print("=" * 60)
    print(f"Total records: {stats['total_records']:,}")
    print(f"Sources: {len(stats['source_distribution'])}")
    print(f"Teacher models: {len(stats['teacher_model_distribution'])}")


if __name__ == "__main__":
    main()
