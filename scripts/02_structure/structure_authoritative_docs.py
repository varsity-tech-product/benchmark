#!/usr/bin/env python3
"""
Structure authoritative financial education documents.

Parses scraped HTML from SEC, CFPB, and FINRA.
Extracts Q&A pairs using heuristics and optionally LLM assistance.
"""

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Iterator

from bs4 import BeautifulSoup
from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.schemas import StructuredQA

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw"
OUTPUT_DIR = PROJECT_ROOT / "data" / "01_structured"

# Minimum word count for content
MIN_WORD_COUNT = 200


def extract_text_content(soup: BeautifulSoup) -> str:
    """
    Extract main text content from HTML.

    Removes navigation, headers, footers, etc.
    """
    # Remove script and style elements
    for element in soup(["script", "style", "nav", "header", "footer", "aside"]):
        element.decompose()

    # Try to find main content area
    main_content = (
        soup.find("main")
        or soup.find("article")
        or soup.find("div", class_=re.compile(r"content|main|article"))
    )

    if main_content:
        return main_content.get_text(separator="\n", strip=True)
    else:
        return soup.get_text(separator="\n", strip=True)


def extract_qa_heuristic(soup: BeautifulSoup, url: str) -> list[dict]:
    """
    Extract Q&A pairs using heuristics.

    Strategies:
    1. H2/H3 as questions, following paragraphs as answers
    2. Definition lists (dt/dd)
    3. FAQ-style sections
    4. Question mark detection in headings

    Returns:
        List of {question, answer} dicts
    """
    qa_pairs = []

    # Remove nav elements
    for nav in soup.find_all(["nav", "header", "footer", "aside"]):
        nav.decompose()

    # Strategy 1: H2/H3 followed by paragraphs
    for heading in soup.find_all(["h2", "h3"]):
        heading_text = heading.get_text(strip=True)

        # Skip navigation-like headings
        if len(heading_text) < 10 or heading_text.lower() in [
            "menu",
            "navigation",
            "search",
            "share",
        ]:
            continue

        # Collect following siblings until next heading
        answer_parts = []
        for sibling in heading.find_next_siblings():
            if sibling.name in ["h1", "h2", "h3"]:
                break
            if sibling.name in ["p", "ul", "ol", "div"]:
                text = sibling.get_text(strip=True)
                if text:
                    answer_parts.append(text)

        if answer_parts:
            answer = "\n\n".join(answer_parts)
            if len(answer.split()) >= 50:  # Minimum answer length
                qa_pairs.append(
                    {
                        "question": heading_text,
                        "answer": answer,
                    }
                )

    # Strategy 2: Definition lists
    for dl in soup.find_all("dl"):
        dts = dl.find_all("dt")
        dds = dl.find_all("dd")

        for dt, dd in zip(dts, dds):
            question = dt.get_text(strip=True)
            answer = dd.get_text(strip=True)

            if question and answer and len(answer.split()) >= 30:
                qa_pairs.append(
                    {
                        "question": question,
                        "answer": answer,
                    }
                )

    # Strategy 3: FAQ sections
    faq_sections = soup.find_all(class_=re.compile(r"faq|accordion|question", re.I))
    for section in faq_sections:
        q_elem = section.find(class_=re.compile(r"question|title|header", re.I))
        a_elem = section.find(class_=re.compile(r"answer|content|body", re.I))

        if q_elem and a_elem:
            question = q_elem.get_text(strip=True)
            answer = a_elem.get_text(strip=True)

            if question and answer:
                qa_pairs.append(
                    {
                        "question": question,
                        "answer": answer,
                    }
                )

    return qa_pairs


def parse_sec_docs(sample_limit: int = None) -> Iterator[StructuredQA]:
    """Parse SEC Investor.gov documents."""
    print("\nParsing SEC documents...")

    sec_dir = RAW_DATA_DIR / "sec"
    if not sec_dir.exists():
        print(f"  Warning: SEC directory not found at {sec_dir}")
        return

    # Load metadata
    metadata_path = sec_dir / "metadata.json"
    metadata = {}
    if metadata_path.exists():
        with open(metadata_path) as f:
            metadata_list = json.load(f)
            metadata = {m["filename"]: m for m in metadata_list}

    count = 0
    for html_file in tqdm(list(sec_dir.glob("*.html")), desc="Processing SEC"):
        with open(html_file, encoding="utf-8") as f:
            html = f.read()

        soup = BeautifulSoup(html, "lxml")

        # Check word count
        text = extract_text_content(soup)
        if len(text.split()) < MIN_WORD_COUNT:
            continue

        # Get metadata
        file_meta = metadata.get(html_file.name, {})
        url = file_meta.get("url", "")
        title = file_meta.get("title", "")

        # Extract Q&A pairs
        qa_pairs = extract_qa_heuristic(soup, url)

        for idx, qa in enumerate(qa_pairs):
            record = StructuredQA(
                source_id=f"sec_{html_file.stem}_{idx}",
                source_dataset="sec_investor_gov",
                title=title,
                question_body=qa["question"],
                answer_body=qa["answer"],
                source_url=url,
                tags=["regulatory", "investor-education"],
            )
            yield record
            count += 1

            if sample_limit and count >= sample_limit:
                return

    print(f"  Parsed {count} SEC Q&A pairs")


def parse_cfpb_docs(sample_limit: int = None) -> Iterator[StructuredQA]:
    """Parse CFPB consumer resources."""
    print("\nParsing CFPB documents...")

    cfpb_dir = RAW_DATA_DIR / "cfpb"
    if not cfpb_dir.exists():
        print(f"  Warning: CFPB directory not found at {cfpb_dir}")
        return

    # Load metadata
    metadata_path = cfpb_dir / "metadata.json"
    metadata = {}
    if metadata_path.exists():
        with open(metadata_path) as f:
            metadata_list = json.load(f)
            metadata = {m["filename"]: m for m in metadata_list}

    count = 0
    for html_file in tqdm(list(cfpb_dir.glob("*.html")), desc="Processing CFPB"):
        with open(html_file, encoding="utf-8") as f:
            html = f.read()

        soup = BeautifulSoup(html, "lxml")

        # Check word count
        text = extract_text_content(soup)
        if len(text.split()) < MIN_WORD_COUNT:
            continue

        # Get metadata
        file_meta = metadata.get(html_file.name, {})
        url = file_meta.get("url", "")
        title = file_meta.get("title", "")

        # Extract Q&A pairs
        qa_pairs = extract_qa_heuristic(soup, url)

        for idx, qa in enumerate(qa_pairs):
            record = StructuredQA(
                source_id=f"cfpb_{html_file.stem}_{idx}",
                source_dataset="cfpb",
                title=title,
                question_body=qa["question"],
                answer_body=qa["answer"],
                source_url=url,
                tags=["consumer-finance", "regulatory"],
            )
            yield record
            count += 1

            if sample_limit and count >= sample_limit:
                return

    print(f"  Parsed {count} CFPB Q&A pairs")


def parse_finra_docs(sample_limit: int = None) -> Iterator[StructuredQA]:
    """Parse FINRA investor education documents."""
    print("\nParsing FINRA documents...")

    finra_dir = RAW_DATA_DIR / "finra"
    if not finra_dir.exists():
        print(f"  Warning: FINRA directory not found at {finra_dir}")
        return

    # Load metadata
    metadata_path = finra_dir / "metadata.json"
    metadata = {}
    if metadata_path.exists():
        with open(metadata_path) as f:
            metadata_list = json.load(f)
            metadata = {m["filename"]: m for m in metadata_list}

    count = 0
    for html_file in tqdm(list(finra_dir.glob("*.html")), desc="Processing FINRA"):
        with open(html_file, encoding="utf-8") as f:
            html = f.read()

        soup = BeautifulSoup(html, "lxml")

        # Check word count
        text = extract_text_content(soup)
        if len(text.split()) < MIN_WORD_COUNT:
            continue

        # Get metadata
        file_meta = metadata.get(html_file.name, {})
        url = file_meta.get("url", "")
        title = file_meta.get("title", "")

        # Extract Q&A pairs
        qa_pairs = extract_qa_heuristic(soup, url)

        for idx, qa in enumerate(qa_pairs):
            record = StructuredQA(
                source_id=f"finra_{html_file.stem}_{idx}",
                source_dataset="finra",
                title=title,
                question_body=qa["question"],
                answer_body=qa["answer"],
                source_url=url,
                tags=["investor-education", "regulatory"],
            )
            yield record
            count += 1

            if sample_limit and count >= sample_limit:
                return

    print(f"  Parsed {count} FINRA Q&A pairs")


def main():
    parser = argparse.ArgumentParser(
        description="Structure authoritative financial documents"
    )
    parser.add_argument(
        "--source",
        choices=["sec", "cfpb", "finra", "all"],
        default="all",
        help="Which source to process (default: all)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Process only N records per source (for testing)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "authoritative_docs.jsonl",
        help="Output file path",
    )

    args = parser.parse_args()

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Collect parsers to run
    parsers = []
    if args.source in ["sec", "all"]:
        parsers.append(("SEC", parse_sec_docs))
    if args.source in ["cfpb", "all"]:
        parsers.append(("CFPB", parse_cfpb_docs))
    if args.source in ["finra", "all"]:
        parsers.append(("FINRA", parse_finra_docs))

    # Process and write output
    total_count = 0
    with open(args.output, "w") as f:
        for name, parser_fn in parsers:
            source_count = 0
            for record in parser_fn(sample_limit=args.sample):
                f.write(record.model_dump_json() + "\n")
                source_count += 1
                total_count += 1

            print(f"  {name}: {source_count} records")

    print("\n" + "=" * 60)
    print("Authoritative docs structuring complete!")
    print("=" * 60)
    print(f"Output: {args.output}")
    print(f"Total records: {total_count}")


if __name__ == "__main__":
    main()
