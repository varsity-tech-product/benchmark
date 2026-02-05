#!/usr/bin/env python3
"""
Structure Money Stack Exchange data.

Parses Posts.xml, filters high-quality Q&A pairs, and outputs structured JSONL.

Filter criteria:
- Score >= 5
- Has accepted answer
- Converts HTML to Markdown
"""

import argparse
import html
import re
import sys
from pathlib import Path
from typing import Iterator, Optional
from xml.etree.ElementTree import iterparse

from markdownify import markdownify
from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.schemas import StructuredQA

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw" / "money.stackexchange.com"
OUTPUT_DIR = PROJECT_ROOT / "data" / "01_structured"


def clean_html_to_markdown(html_content: str) -> str:
    """
    Convert HTML to clean markdown.

    Args:
        html_content: Raw HTML string

    Returns:
        Clean markdown string
    """
    if not html_content:
        return ""

    # Unescape HTML entities
    content = html.unescape(html_content)

    # Convert to markdown
    md = markdownify(content, heading_style="ATX", bullets="-")

    # Clean up excessive whitespace
    md = re.sub(r"\n{3,}", "\n\n", md)
    md = md.strip()

    return md


def parse_tags(tags_str: str) -> list[str]:
    """
    Parse Stack Exchange tags format.

    Tags are stored as: <tag1><tag2><tag3>

    Args:
        tags_str: Raw tags string

    Returns:
        List of tag strings
    """
    if not tags_str:
        return []

    # Extract tags between < and >
    return re.findall(r"<([^>]+)>", tags_str)


class PostsParser:
    """Memory-efficient parser for Stack Exchange Posts.xml."""

    def __init__(self, posts_path: Path, min_score: int = 5):
        self.posts_path = posts_path
        self.min_score = min_score

        # Store questions indexed by ID for matching with answers
        self.questions: dict[str, dict] = {}

        # Store accepted answer IDs to their question IDs
        self.accepted_answers: dict[str, str] = {}

    def _parse_row(self, elem) -> Optional[dict]:
        """Parse a single row element."""
        attrs = elem.attrib

        return {
            "id": attrs.get("Id"),
            "post_type": attrs.get("PostTypeId"),  # 1 = question, 2 = answer
            "parent_id": attrs.get("ParentId"),  # For answers, points to question
            "accepted_answer_id": attrs.get("AcceptedAnswerId"),
            "score": int(attrs.get("Score", 0)),
            "body": attrs.get("Body", ""),
            "title": attrs.get("Title"),
            "tags": attrs.get("Tags", ""),
            "creation_date": attrs.get("CreationDate"),
        }

    def first_pass(self) -> int:
        """
        First pass: identify qualifying questions.

        Returns:
            Number of qualifying questions
        """
        print("First pass: identifying qualifying questions...")

        count = 0
        for event, elem in tqdm(iterparse(self.posts_path, events=("end",))):
            if elem.tag != "row":
                continue

            post = self._parse_row(elem)

            # Check if it's a question (PostTypeId = 1)
            if post["post_type"] == "1":
                # Check filter criteria
                if post["score"] >= self.min_score and post["accepted_answer_id"]:
                    self.questions[post["id"]] = post
                    self.accepted_answers[post["accepted_answer_id"]] = post["id"]
                    count += 1

            # Clear element to save memory
            elem.clear()

        print(f"Found {count} qualifying questions")
        return count

    def second_pass(self) -> Iterator[StructuredQA]:
        """
        Second pass: match answers to questions and yield structured records.

        Yields:
            StructuredQA records
        """
        print("Second pass: matching answers to questions...")

        matched = 0
        for event, elem in tqdm(iterparse(self.posts_path, events=("end",))):
            if elem.tag != "row":
                continue

            post = self._parse_row(elem)

            # Check if it's an accepted answer we're looking for
            if post["id"] in self.accepted_answers:
                question_id = self.accepted_answers[post["id"]]
                question = self.questions.get(question_id)

                if question:
                    # Create structured record
                    record = StructuredQA(
                        source_id=question_id,
                        source_dataset="money.stackexchange",
                        title=question["title"],
                        question_body=clean_html_to_markdown(question["body"]),
                        answer_body=clean_html_to_markdown(post["body"]),
                        tags=parse_tags(question["tags"]),
                        creation_date=question["creation_date"],
                        source_url=f"https://money.stackexchange.com/questions/{question_id}",
                    )
                    matched += 1
                    yield record

            # Clear element to save memory
            elem.clear()

        print(f"Matched {matched} question-answer pairs")


def main():
    parser = argparse.ArgumentParser(description="Structure Money Stack Exchange data")
    parser.add_argument(
        "--min-score",
        type=int,
        default=5,
        help="Minimum question score (default: 5)",
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Process only N records (for testing)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "stack_exchange.jsonl",
        help="Output file path",
    )

    args = parser.parse_args()

    # Check input file exists
    posts_path = RAW_DATA_DIR / "Posts.xml"
    if not posts_path.exists():
        print(f"Error: Posts.xml not found at {posts_path}")
        print("Run ingest_stack_exchange.py first")
        sys.exit(1)

    # Ensure output directory exists
    args.output.parent.mkdir(parents=True, exist_ok=True)

    # Parse posts
    post_parser = PostsParser(posts_path, min_score=args.min_score)

    # First pass: identify questions
    post_parser.first_pass()

    # Second pass: match answers and write output
    count = 0
    with open(args.output, "w") as f:
        for record in post_parser.second_pass():
            f.write(record.model_dump_json() + "\n")
            count += 1

            if args.sample and count >= args.sample:
                print(f"Reached sample limit of {args.sample}")
                break

    print("\n" + "=" * 60)
    print("Stack Exchange structuring complete!")
    print("=" * 60)
    print(f"Output: {args.output}")
    print(f"Records: {count}")


if __name__ == "__main__":
    main()
