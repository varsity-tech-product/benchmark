#!/usr/bin/env python3
"""
Upload packaged dataset to HuggingFace Hub.

Uploads the final JSONL file and dataset card directly via HfApi,
avoiding memory-heavy dataset loading.
"""

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import HfApi

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
PACKAGED_DIR = PROJECT_ROOT / "data" / "03_packaged"
DEFAULT_INPUT = PACKAGED_DIR / "quant_tutor_benchmark.jsonl"
DATASET_CARD = PACKAGED_DIR / "DATASET_CARD.md"


def main():
    parser = argparse.ArgumentParser(
        description="Upload benchmark dataset to HuggingFace Hub"
    )
    parser.add_argument(
        "--repo-id",
        type=str,
        required=True,
        help="HuggingFace repo ID (e.g. 'your-org/quant-tutor-benchmark')",
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Path to JSONL file to upload (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--public",
        action="store_true",
        help="Make the repo public (default: private)",
    )

    args = parser.parse_args()

    # Load .env for HF_TOKEN
    load_dotenv(PROJECT_ROOT / ".env")

    if not args.input.exists():
        print(f"Error: input file not found: {args.input}")
        print("Run the validate/package step first.")
        sys.exit(1)

    api = HfApi()

    # Create or get repo
    repo_url = api.create_repo(
        repo_id=args.repo_id,
        repo_type="dataset",
        private=not args.public,
        exist_ok=True,
    )
    print(f"Repository: {repo_url}")

    # Upload JSONL
    print(f"Uploading {args.input.name}...")
    api.upload_file(
        path_or_fileobj=str(args.input),
        path_in_repo=args.input.name,
        repo_id=args.repo_id,
        repo_type="dataset",
    )
    print(f"  Uploaded {args.input.name}")

    # Upload dataset card as README.md
    if DATASET_CARD.exists():
        print("Uploading dataset card as README.md...")
        api.upload_file(
            path_or_fileobj=str(DATASET_CARD),
            path_in_repo="README.md",
            repo_id=args.repo_id,
            repo_type="dataset",
        )
        print("  Uploaded README.md")
    else:
        print(f"Warning: dataset card not found at {DATASET_CARD}, skipping")

    print("\n" + "=" * 60)
    print("Upload complete!")
    print(f"View at: https://huggingface.co/datasets/{args.repo_id}")
    print("=" * 60)


if __name__ == "__main__":
    main()
