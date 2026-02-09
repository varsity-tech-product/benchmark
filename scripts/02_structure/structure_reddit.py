#!/usr/bin/env python3
"""
Structure Reddit finance subreddit data.

Two-pass approach over Arctic Shift zstandard dumps:
  Pass 1: Stream submissions, collect qualifying posts (score >= threshold, has selftext)
  Pass 2: Stream comments, match top-scored direct replies to qualifying posts

Quality filters:
  - Minimum post score (default 10)
  - Minimum comment score (default 5)
  - Minimum comment word count (50)
  - Skip [removed] / [deleted] content
"""

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Iterator, Optional

from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.schemas import StructuredQA

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw" / "reddit"
OUTPUT_DIR = PROJECT_ROOT / "data" / "01_structured"

SUBREDDITS = [
    "personalfinance",
    "investing",
    "financialindependence",
    "stocks",
    "tax",
    "realestateinvesting",
]

SKIP_BODIES = {"[removed]", "[deleted]", ""}


def _stream_zst_lines(path: Path) -> Iterator[dict]:
    """
    Stream JSON lines from a zstandard-compressed file.

    Uses zstandard streaming decompression for memory efficiency.

    Yields:
        Parsed JSON objects, one per line
    """
    import zstandard as zstd

    dctx = zstd.ZstdDecompressor()
    with open(path, "rb") as fh:
        reader = dctx.stream_reader(fh)
        text_reader = io.TextIOWrapper(reader, encoding="utf-8")
        for line in text_reader:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


class RedditParser:
    """Memory-efficient parser for Reddit Arctic Shift dumps."""

    def __init__(
        self,
        subreddit: str,
        min_post_score: int = 10,
        min_comment_score: int = 5,
        min_comment_words: int = 50,
    ):
        self.subreddit = subreddit
        self.min_post_score = min_post_score
        self.min_comment_score = min_comment_score
        self.min_comment_words = min_comment_words

        self.data_dir = RAW_DATA_DIR / subreddit
        self.submissions_path = self.data_dir / "submissions.zst"
        self.comments_path = self.data_dir / "comments.zst"

        # Qualifying posts keyed by Reddit fullname (t3_<id>)
        self.posts: dict[str, dict] = {}

    def first_pass(self) -> int:
        """
        First pass: stream submissions, collect qualifying posts.

        Returns:
            Number of qualifying posts found
        """
        print(f"Pass 1: scanning submissions for r/{self.subreddit}...")

        count = 0
        for obj in tqdm(_stream_zst_lines(self.submissions_path), desc="  Submissions"):
            selftext = (obj.get("selftext") or "").strip()
            if selftext in SKIP_BODIES:
                continue

            score = obj.get("score", 0)
            if score < self.min_post_score:
                continue

            post_id = obj.get("id", "")
            self.posts[f"t3_{post_id}"] = {
                "id": post_id,
                "title": obj.get("title", ""),
                "selftext": selftext,
                "score": score,
                "created_utc": obj.get("created_utc"),
                "link_flair_text": obj.get("link_flair_text"),
                "permalink": obj.get("permalink", ""),
            }
            count += 1

        print(f"  Found {count} qualifying posts")
        return count

    def second_pass(self) -> Iterator[StructuredQA]:
        """
        Second pass: stream comments, pick top direct reply per qualifying post.

        Yields:
            StructuredQA records
        """
        print(f"Pass 2: scanning comments for r/{self.subreddit}...")

        # Best comment per post (keyed by parent fullname t3_<id>)
        best: dict[str, dict] = {}

        for obj in tqdm(_stream_zst_lines(self.comments_path), desc="  Comments"):
            parent_id: str = obj.get("parent_id", "")
            if not parent_id.startswith("t3_"):
                continue
            if parent_id not in self.posts:
                continue

            body = (obj.get("body") or "").strip()
            if body in SKIP_BODIES:
                continue
            if len(body.split()) < self.min_comment_words:
                continue

            score = obj.get("score", 0)
            if score < self.min_comment_score:
                continue

            prev = best.get(parent_id)
            if prev is None or score > prev["score"]:
                best[parent_id] = {
                    "body": body,
                    "score": score,
                }

        matched = 0
        for fullname, comment in best.items():
            post = self.posts[fullname]
            tags = [self.subreddit]
            if post.get("link_flair_text"):
                tags.append(post["link_flair_text"])

            permalink = post.get("permalink", "")
            source_url: Optional[str] = (
                f"https://www.reddit.com{permalink}" if permalink else None
            )

            created = post.get("created_utc")
            creation_date: Optional[str] = None
            if created is not None:
                try:
                    from datetime import datetime, timezone

                    creation_date = datetime.fromtimestamp(
                        int(created), tz=timezone.utc
                    ).isoformat()
                except (ValueError, TypeError, OSError):
                    pass

            record = StructuredQA(
                source_id=post["id"],
                source_dataset=f"reddit.{self.subreddit}",
                title=post["title"],
                question_body=post["selftext"],
                answer_body=comment["body"],
                tags=tags,
                creation_date=creation_date,
                source_url=source_url,
            )
            matched += 1
            yield record

        print(f"  Matched {matched} post-comment pairs")


def main():
    parser = argparse.ArgumentParser(
        description="Structure Reddit finance subreddit data into QA pairs"
    )
    parser.add_argument(
        "--subreddit",
        type=str,
        choices=SUBREDDITS,
        default=None,
        help="Process a specific subreddit (default: all)",
    )
    parser.add_argument(
        "--min-post-score",
        type=int,
        default=10,
        help="Minimum post score (default: 10)",
    )
    parser.add_argument(
        "--min-comment-score",
        type=int,
        default=5,
        help="Minimum comment score (default: 5)",
    )
    parser.add_argument(
        "--min-comment-words",
        type=int,
        default=50,
        help="Minimum comment word count (default: 50)",
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
        default=OUTPUT_DIR / "reddit.jsonl",
        help="Output file path",
    )

    args = parser.parse_args()

    subreddits = [args.subreddit] if args.subreddit else SUBREDDITS

    args.output.parent.mkdir(parents=True, exist_ok=True)

    total_count = 0
    with open(args.output, "w") as f:
        for sub in subreddits:
            sub_dir = RAW_DATA_DIR / sub
            submissions_path = sub_dir / "submissions.zst"
            comments_path = sub_dir / "comments.zst"

            if not submissions_path.exists() or not comments_path.exists():
                print(f"Skipping r/{sub}: data files not found")
                print("  Run ingest_reddit.py first")
                continue

            reddit_parser = RedditParser(
                sub,
                min_post_score=args.min_post_score,
                min_comment_score=args.min_comment_score,
                min_comment_words=args.min_comment_words,
            )
            reddit_parser.first_pass()

            for record in reddit_parser.second_pass():
                f.write(record.model_dump_json() + "\n")
                total_count += 1

                if args.sample and total_count >= args.sample:
                    print(f"Reached sample limit of {args.sample}")
                    break

            if args.sample and total_count >= args.sample:
                break

    print("\n" + "=" * 60)
    print("Reddit structuring complete!")
    print("=" * 60)
    print(f"Output: {args.output}")
    print(f"Records: {total_count}")


if __name__ == "__main__":
    main()
