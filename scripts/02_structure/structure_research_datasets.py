#!/usr/bin/env python3
"""
Structure research datasets into unified format.

Handles:
- FiQA: Financial QA pairs
- FinQA: Numerical reasoning with financial tables
- ConvFinQA: Conversational financial QA
- TAT-QA: Tabular and textual QA
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Iterator

from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.schemas import StructuredQA

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw"
OUTPUT_DIR = PROJECT_ROOT / "data" / "01_structured"


def parse_fiqa(sample_limit: int = None) -> Iterator[StructuredQA]:
    """
    Parse FiQA dataset.

    FiQA contains question-answer pairs from financial forums.
    Uses queries, corpus, and qrels files to match Q&A pairs.
    """
    print("\nParsing FiQA dataset...")

    fiqa_dir = RAW_DATA_DIR / "fiqa"
    corpus_path = fiqa_dir / "corpus.jsonl"
    queries_path = fiqa_dir / "queries.jsonl"
    qrels_dir = fiqa_dir / "qrels"

    if not corpus_path.exists():
        print(f"  Warning: FiQA corpus not found at {corpus_path}")
        return

    # Load corpus (documents/answers)
    corpus = {}
    with open(corpus_path) as f:
        for line in f:
            doc = json.loads(line)
            corpus[doc["_id"]] = doc

    # Load queries
    queries = {}
    if queries_path.exists():
        with open(queries_path) as f:
            for line in f:
                query = json.loads(line)
                queries[query["_id"]] = query

    # Load qrels (query-to-document relevance mappings)
    qrels = []
    for qrel_file in ["train.tsv", "dev.tsv", "test.tsv"]:
        qrel_path = qrels_dir / qrel_file
        if qrel_path.exists():
            with open(qrel_path) as f:
                next(f)  # Skip header
                for line in f:
                    parts = line.strip().split("\t")
                    if len(parts) >= 2:
                        qrels.append((parts[0], parts[1]))

    # Match queries to corpus documents using qrels
    count = 0
    for query_id, corpus_id in tqdm(qrels, desc="Processing FiQA"):
        if query_id not in queries or corpus_id not in corpus:
            continue

        query = queries[query_id]
        doc = corpus[corpus_id]

        question = query.get("text", "").strip()
        answer = doc.get("text", "").strip()

        if not question or not answer:
            continue

        record = StructuredQA(
            source_id=f"fiqa_{query_id}_{corpus_id}",
            source_dataset="fiqa",
            title=None,
            question_body=question,
            answer_body=answer,
            tags=["financial-qa"],
        )
        yield record
        count += 1

        if sample_limit and count >= sample_limit:
            break

    print(f"  Parsed {count} FiQA records")


def parse_finqa(sample_limit: int = None) -> Iterator[StructuredQA]:
    """
    Parse FinQA dataset.

    FinQA contains questions requiring numerical reasoning over
    financial reports with tables and text context.
    """
    print("\nParsing FinQA dataset...")

    finqa_dir = RAW_DATA_DIR / "finqa"

    files = ["train.json", "dev.json", "test.json"]
    count = 0

    for filename in files:
        filepath = finqa_dir / filename
        if not filepath.exists():
            print(f"  Warning: {filename} not found")
            continue

        with open(filepath) as f:
            data = json.load(f)

        split = filename.replace(".json", "")

        for idx, item in enumerate(tqdm(data, desc=f"Processing {filename}")):
            # Extract context (tables and pre/post text)
            pre_text = item.get("pre_text", [])
            post_text = item.get("post_text", [])
            table = item.get("table", [])

            # Format context
            context_parts = []
            if pre_text:
                context_parts.append("**Context:**\n" + "\n".join(pre_text))
            if table:
                # Convert table to markdown
                table_md = format_table_markdown(table)
                context_parts.append("**Table:**\n" + table_md)
            if post_text:
                context_parts.append("\n".join(post_text))

            context = "\n\n".join(context_parts)

            # Extract Q&A from nested 'qa' field
            qa_data = item.get("qa", {})
            question = qa_data.get("question", "") if isinstance(qa_data, dict) else ""
            answer = qa_data.get("answer", "") if isinstance(qa_data, dict) else ""
            program = qa_data.get("program", "") if isinstance(qa_data, dict) else ""

            # Skip if no question
            if not question:
                continue

            # Format answer with explanation
            answer_parts = [f"**Answer:** {answer}"]
            if program:
                answer_parts.append(f"**Reasoning:** {program}")

            record = StructuredQA(
                source_id=f"finqa_{split}_{idx}",
                source_dataset="finqa",
                question_body=question,
                answer_body="\n\n".join(answer_parts),
                context=context,
                tags=["numerical-reasoning", "financial-reports"],
            )
            yield record
            count += 1

            if sample_limit and count >= sample_limit:
                return

    print(f"  Parsed {count} FinQA records")


def parse_convfinqa(sample_limit: int = None) -> Iterator[StructuredQA]:
    """
    Parse ConvFinQA dataset.

    ConvFinQA extends FinQA with conversational context,
    preserving the full conversation chain.
    """
    print("\nParsing ConvFinQA dataset...")

    convfinqa_dir = RAW_DATA_DIR / "convfinqa"

    files = ["train.json", "dev.json", "test.json"]
    count = 0

    for filename in files:
        filepath = convfinqa_dir / filename
        if not filepath.exists():
            print(f"  Warning: {filename} not found")
            continue

        with open(filepath) as f:
            data = json.load(f)

        split = filename.replace(".json", "")

        for doc_idx, item in enumerate(tqdm(data, desc=f"Processing {filename}")):
            # Extract document context
            pre_text = item.get("pre_text", [])
            post_text = item.get("post_text", [])
            table = item.get("table", [])

            # Format context
            context_parts = []
            if pre_text:
                context_parts.append("**Context:**\n" + "\n".join(pre_text))
            if table:
                table_md = format_table_markdown(table)
                context_parts.append("**Table:**\n" + table_md)
            if post_text:
                context_parts.append("\n".join(post_text))

            context = "\n\n".join(context_parts)

            # ConvFinQA has the main Q&A in the 'qa' field
            # dialogue_break contains prior turn questions as strings
            qa_data = item.get("qa", {})
            if not qa_data or not isinstance(qa_data, dict):
                continue

            question = qa_data.get("question", "")
            answer = qa_data.get("answer", "")
            program = qa_data.get("program", "")

            if not question:
                continue

            # Build conversation history from dialogue_break (prior questions)
            annotation = item.get("annotation", {})
            dialogue_break = annotation.get("dialogue_break", [])
            conversation_history = []
            if dialogue_break and isinstance(dialogue_break, list):
                for prior_q in dialogue_break:
                    if isinstance(prior_q, str):
                        conversation_history.append({"question": prior_q, "answer": ""})

            # Format answer
            answer_parts = [f"**Answer:** {answer}"]
            if program:
                answer_parts.append(f"**Reasoning:** {program}")

            answer_text = "\n\n".join(answer_parts)

            # Create record with conversation history
            record = StructuredQA(
                source_id=f"convfinqa_{split}_{doc_idx}",
                source_dataset="convfinqa",
                question_body=question,
                answer_body=answer_text,
                context=context,
                conversation_history=(
                    conversation_history if conversation_history else None
                ),
                tags=["conversational", "numerical-reasoning", "financial-reports"],
            )
            yield record
            count += 1

            if sample_limit and count >= sample_limit:
                return

    print(f"  Parsed {count} ConvFinQA records")


def parse_tatqa(sample_limit: int = None) -> Iterator[StructuredQA]:
    """
    Parse TAT-QA dataset.

    TAT-QA requires reasoning over both tables and text
    in financial documents.
    """
    print("\nParsing TAT-QA dataset...")

    tatqa_dir = RAW_DATA_DIR / "tatqa"

    files = [
        "tatqa_dataset_train.json",
        "tatqa_dataset_dev.json",
        "tatqa_dataset_test.json",
    ]
    count = 0

    for filename in files:
        filepath = tatqa_dir / filename
        if not filepath.exists():
            print(f"  Warning: {filename} not found")
            continue

        with open(filepath) as f:
            data = json.load(f)

        split = filename.replace("tatqa_dataset_", "").replace(".json", "")

        for doc_idx, item in enumerate(tqdm(data, desc=f"Processing {filename}")):
            # Extract context
            table = item.get("table", {})
            paragraphs = item.get("paragraphs", [])

            # Format context
            context_parts = []

            # Format table if present
            if table:
                table_data = table.get("table", [])
                if table_data:
                    table_md = format_table_markdown(table_data)
                    context_parts.append("**Table:**\n" + table_md)

            # Add paragraphs
            for para in paragraphs:
                para_text = para.get("text", "")
                if para_text:
                    context_parts.append(para_text)

            context = "\n\n".join(context_parts) if context_parts else None

            # Process questions
            questions = item.get("questions", [])

            for q_idx, qa in enumerate(questions):
                question = qa.get("question", "")
                answer = qa.get("answer", "")
                answer_type = qa.get("answer_type", "")
                derivation = qa.get("derivation", "")

                # Format answer
                if isinstance(answer, list):
                    answer_text = ", ".join(str(a) for a in answer)
                else:
                    answer_text = str(answer)

                answer_parts = [f"**Answer:** {answer_text}"]
                if answer_type:
                    answer_parts.append(f"**Type:** {answer_type}")
                if derivation:
                    answer_parts.append(f"**Derivation:** {derivation}")

                record = StructuredQA(
                    source_id=f"tatqa_{split}_{doc_idx}_{q_idx}",
                    source_dataset="tatqa",
                    question_body=question,
                    answer_body="\n\n".join(answer_parts),
                    context=context,
                    tags=["tabular-reasoning", "financial-documents"],
                )
                yield record
                count += 1

                if sample_limit and count >= sample_limit:
                    return

    print(f"  Parsed {count} TAT-QA records")


def format_table_markdown(table: list) -> str:
    """
    Convert a table (list of lists) to markdown format.

    Args:
        table: List of rows, each row is a list of cell values

    Returns:
        Markdown table string
    """
    if not table:
        return ""

    # Handle different table formats
    if isinstance(table[0], dict):
        # Table is list of dicts
        headers = list(table[0].keys())
        rows = [[str(row.get(h, "")) for h in headers] for row in table]
        table = [headers] + rows

    lines = []

    for i, row in enumerate(table):
        # Convert all cells to strings
        cells = [str(cell).strip() for cell in row]
        line = "| " + " | ".join(cells) + " |"
        lines.append(line)

        # Add header separator after first row
        if i == 0:
            separator = "| " + " | ".join(["---"] * len(cells)) + " |"
            lines.append(separator)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Structure research datasets into unified format"
    )
    parser.add_argument(
        "--dataset",
        choices=["fiqa", "finqa", "convfinqa", "tatqa", "all"],
        default="all",
        help="Which dataset to process (default: all)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Process only N records per dataset (for testing)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "research_datasets.jsonl",
        help="Output file path",
    )

    args = parser.parse_args()

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Collect parsers to run
    parsers = []
    if args.dataset in ["fiqa", "all"]:
        parsers.append(("FiQA", parse_fiqa))
    if args.dataset in ["finqa", "all"]:
        parsers.append(("FinQA", parse_finqa))
    if args.dataset in ["convfinqa", "all"]:
        parsers.append(("ConvFinQA", parse_convfinqa))
    if args.dataset in ["tatqa", "all"]:
        parsers.append(("TAT-QA", parse_tatqa))

    # Process and write output
    total_count = 0
    with open(args.output, "w") as f:
        for name, parser_fn in parsers:
            dataset_count = 0
            for record in parser_fn(sample_limit=args.sample):
                f.write(record.model_dump_json() + "\n")
                dataset_count += 1
                total_count += 1

            print(f"  {name}: {dataset_count} records")

    print("\n" + "=" * 60)
    print("Research datasets structuring complete!")
    print("=" * 60)
    print(f"Output: {args.output}")
    print(f"Total records: {total_count}")


if __name__ == "__main__":
    main()
