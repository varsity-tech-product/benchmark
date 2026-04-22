#!/usr/bin/env python3
"""
Structure authoritative financial education documents.

Parses scraped HTML from SEC, CFPB, and FINRA.
Extracts Q&A pairs using heuristics, then uses LLM to rewrite
section headings into natural user questions.

Two-phase approach:
  Phase 1: Extract Q&A pairs from HTML using heuristic strategies
  Phase 2: Rewrite question_body via LLM (section heading -> natural question)
"""

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup
from tqdm import tqdm
from tqdm.asyncio import tqdm as atqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.llm_utils import call_llm
from lib.schemas import StructuredQA

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw"
OUTPUT_DIR = PROJECT_ROOT / "data" / "01_structured"

# Minimum word count for content
MIN_WORD_COUNT = 200

# LLM rewrite settings
REWRITE_MODEL = "x-ai/grok-4.1-fast"
MAX_CONCURRENT = 5

REWRITE_SYSTEM_PROMPT = """\
You are a financial literacy expert. Your task is to convert a document \
section heading into a natural question that a real person might ask.

Rules:
- The question must be answerable by the provided content
- Write from the perspective of someone seeking financial guidance
- Keep it concise (1-2 sentences)
- Use natural, conversational language
- Output ONLY the question text, nothing else"""

REWRITE_USER_PROMPT = """\
Document title: {title}
Section heading: {section_heading}

Section content (first 500 chars):
{answer_preview}

Generate a natural question that someone might ask, which this content answers:"""


# ── Phase 1: Heuristic extraction ─────────────────────────────────────


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


def parse_source_docs(
    source_name: str,
    source_dir: Path,
    source_dataset: str,
    tags: list[str],
    sample_limit: int = None,
) -> list[StructuredQA]:
    """Parse HTML documents from a single source."""
    print(f"\nParsing {source_name} documents...")

    if not source_dir.exists():
        print(f"  Warning: directory not found at {source_dir}")
        return []

    # Load metadata
    metadata_path = source_dir / "metadata.json"
    metadata = {}
    if metadata_path.exists():
        with open(metadata_path) as f:
            metadata_list = json.load(f)
            metadata = {m["filename"]: m for m in metadata_list}

    records = []
    for html_file in tqdm(
        list(source_dir.glob("*.html")), desc=f"Processing {source_name}"
    ):
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
                source_id=f"{source_dataset.split('_')[0]}_{html_file.stem}_{idx}",
                source_dataset=source_dataset,
                title=title,
                question_body=qa["question"],
                answer_body=qa["answer"],
                source_url=url,
                tags=tags,
            )
            records.append(record)

            if sample_limit and len(records) >= sample_limit:
                print(f"  Reached sample limit of {sample_limit}")
                return records

    print(f"  Extracted {len(records)} {source_name} Q&A pairs")
    return records


# ── Phase 2: LLM question rewrite ─────────────────────────────────────


async def rewrite_question(
    record: StructuredQA,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> StructuredQA:
    """Rewrite a single record's question_body using LLM."""
    async with semaphore:
        prompt = REWRITE_USER_PROMPT.format(
            title=record.title or "N/A",
            section_heading=record.question_body,
            answer_preview=record.answer_body[:500],
        )

        response, _ = await call_llm(
            prompt=prompt,
            model=REWRITE_MODEL,
            system_prompt=REWRITE_SYSTEM_PROMPT,
            temperature=0.3,
            max_tokens=150,
            session=session,
        )

        record.question_body = response.strip().strip('"').strip("'")
        return record


async def rewrite_all_questions(records: list[StructuredQA]) -> list[StructuredQA]:
    """Rewrite all question_body fields using LLM."""
    print(f"\nRewriting {len(records)} questions via LLM...")

    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    async with aiohttp.ClientSession() as session:
        tasks = [rewrite_question(r, session, semaphore) for r in records]
        results = []
        for coro in atqdm.as_completed(
            tasks, total=len(tasks), desc="Rewriting questions"
        ):
            result = await coro
            results.append(result)

    return results


# ── Main ───────────────────────────────────────────────────────────────


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
    parser.add_argument(
        "--skip-rewrite",
        action="store_true",
        help="Skip LLM question rewrite (output raw headings as questions)",
    )

    args = parser.parse_args()

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Source configs
    sources = {
        "sec": (
            "SEC",
            RAW_DATA_DIR / "sec",
            "sec_investor_gov",
            ["regulatory", "investor-education"],
        ),
        "cfpb": (
            "CFPB",
            RAW_DATA_DIR / "cfpb",
            "cfpb",
            ["consumer-finance", "regulatory"],
        ),
        "finra": (
            "FINRA",
            RAW_DATA_DIR / "finra",
            "finra",
            ["investor-education", "regulatory"],
        ),
    }

    # Phase 1: Heuristic extraction
    print("=" * 60)
    print("Phase 1: Extracting Q&A pairs from HTML")
    print("=" * 60)

    all_records = []
    source_keys = [args.source] if args.source != "all" else ["sec", "cfpb", "finra"]

    for key in source_keys:
        name, dir_path, dataset, tags = sources[key]
        records = parse_source_docs(
            name, dir_path, dataset, tags, sample_limit=args.sample
        )
        all_records.extend(records)

    if not all_records:
        print("No records extracted!")
        sys.exit(1)

    print(f"\nPhase 1 complete: {len(all_records)} records extracted")

    # Phase 2: LLM question rewrite
    if not args.skip_rewrite:
        print("\n" + "=" * 60)
        print("Phase 2: Rewriting questions via LLM")
        print("=" * 60)

        all_records = asyncio.run(rewrite_all_questions(all_records))

        print(f"Phase 2 complete: {len(all_records)} questions rewritten")
    else:
        print("\nSkipping LLM rewrite (--skip-rewrite)")

    # Write output
    with open(args.output, "w") as f:
        for record in all_records:
            f.write(record.model_dump_json() + "\n")

    print("\n" + "=" * 60)
    print("Authoritative docs structuring complete!")
    print("=" * 60)
    print(f"Output: {args.output}")
    print(f"Total records: {len(all_records)}")


if __name__ == "__main__":
    main()
