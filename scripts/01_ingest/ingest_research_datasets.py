#!/usr/bin/env python3
"""
Ingest research datasets from Hugging Face and GitHub.

Datasets:
- FiQA: Financial QA from BeIR benchmark
- FinQA: Numerical reasoning over financial reports
- ConvFinQA: Conversational FinQA
- TAT-QA: Tabular and Textual QA
"""

import argparse
import sys
from pathlib import Path

import requests
from tqdm import tqdm

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw"


def download_file(url: str, dest_path: Path, desc: str = None) -> bool:
    """Download a file with progress bar."""
    try:
        response = requests.get(url, stream=True)
        response.raise_for_status()

        total_size = int(response.headers.get("content-length", 0))

        with open(dest_path, "wb") as f:
            with tqdm(
                total=total_size,
                unit="iB",
                unit_scale=True,
                desc=desc or dest_path.name,
            ) as pbar:
                for chunk in response.iter_content(chunk_size=8192):
                    size = f.write(chunk)
                    pbar.update(size)

        return True
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return False


def ingest_fiqa():
    """
    Download FiQA dataset from BEIR.

    FiQA is part of the BeIR benchmark and contains financial QA pairs
    from financial forums and news.
    """
    print("\n" + "=" * 60)
    print("Ingesting FiQA dataset from BEIR...")
    print("=" * 60)

    output_dir = RAW_DATA_DIR / "fiqa"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Download from BEIR directly
    zip_url = (
        "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/fiqa.zip"
    )
    zip_path = output_dir / "fiqa.zip"

    # Check if already extracted
    corpus_path = output_dir / "corpus.jsonl"
    if corpus_path.exists():
        print("  FiQA already downloaded and extracted, skipping...")
        return True

    try:
        # Download zip file
        print("  Downloading FiQA dataset...")
        if not download_file(zip_url, zip_path, desc="fiqa.zip"):
            return False

        # Extract zip file
        print("  Extracting...")
        import zipfile

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(output_dir)

        # Move files from nested directory if needed
        nested_dir = output_dir / "fiqa"
        if nested_dir.exists():
            for item in nested_dir.iterdir():
                target = output_dir / item.name
                if not target.exists():
                    item.rename(target)
            # Remove empty nested directory
            try:
                nested_dir.rmdir()
            except OSError:
                pass

        # Clean up zip file
        zip_path.unlink()

        print(f"✓ FiQA dataset saved to {output_dir}")
        return True

    except Exception as e:
        print(f"Error ingesting FiQA: {e}")
        return False


def ingest_finqa():
    """
    Download FinQA dataset from GitHub.

    FinQA contains questions requiring numerical reasoning over
    financial reports with tables and text.
    """
    print("\n" + "=" * 60)
    print("Ingesting FinQA dataset from GitHub...")
    print("=" * 60)

    output_dir = RAW_DATA_DIR / "finqa"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Download the dataset files directly from GitHub
    base_url = "https://raw.githubusercontent.com/czyssrs/FinQA/main/dataset"

    files_to_download = [
        "train.json",
        "dev.json",
        "test.json",
    ]

    success = True
    for filename in files_to_download:
        url = f"{base_url}/{filename}"
        dest = output_dir / filename

        if dest.exists():
            print(f"  {filename} already exists, skipping...")
            continue

        print(f"  Downloading {filename}...")
        if not download_file(url, dest, desc=filename):
            success = False

    if success:
        print(f"✓ FinQA dataset saved to {output_dir}")
    return success


def ingest_convfinqa():
    """
    Download ConvFinQA dataset from GitHub.

    ConvFinQA extends FinQA with conversational context,
    where questions build on previous Q&A turns.
    """
    print("\n" + "=" * 60)
    print("Ingesting ConvFinQA dataset from GitHub...")
    print("=" * 60)

    output_dir = RAW_DATA_DIR / "convfinqa"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Check if already extracted
    train_path = output_dir / "train.json"
    if train_path.exists():
        print("  ConvFinQA already downloaded and extracted, skipping...")
        return True

    # Download data.zip from GitHub
    zip_url = "https://github.com/czyssrs/ConvFinQA/raw/main/data.zip"
    zip_path = output_dir / "data.zip"

    try:
        print("  Downloading ConvFinQA data.zip...")
        if not download_file(zip_url, zip_path, desc="data.zip"):
            return False

        # Extract zip file
        print("  Extracting...")
        import zipfile

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(output_dir)

        # Clean up zip file
        zip_path.unlink()

        print(f"✓ ConvFinQA dataset saved to {output_dir}")
        return True

    except Exception as e:
        print(f"Error ingesting ConvFinQA: {e}")
        return False


def ingest_tatqa():
    """
    Download TAT-QA dataset from GitHub.

    TAT-QA (Tabular And Textual QA) requires reasoning over
    both tables and text in financial documents.
    """
    print("\n" + "=" * 60)
    print("Ingesting TAT-QA dataset from GitHub...")
    print("=" * 60)

    output_dir = RAW_DATA_DIR / "tatqa"
    output_dir.mkdir(parents=True, exist_ok=True)

    # Download from GitHub - the dataset is in the repository
    base_url = (
        "https://raw.githubusercontent.com/NExTplusplus/TAT-QA/master/dataset_raw"
    )

    files_to_download = [
        "tatqa_dataset_train.json",
        "tatqa_dataset_dev.json",
        "tatqa_dataset_test.json",
    ]

    success = True
    for filename in files_to_download:
        url = f"{base_url}/{filename}"
        dest = output_dir / filename

        if dest.exists():
            print(f"  {filename} already exists, skipping...")
            continue

        print(f"  Downloading {filename}...")
        if not download_file(url, dest, desc=filename):
            # Try alternative path
            alt_url = f"https://raw.githubusercontent.com/NExTplusplus/TAT-QA/master/dataset/{filename}"
            if not download_file(alt_url, dest, desc=filename):
                success = False

    if success:
        print(f"✓ TAT-QA dataset saved to {output_dir}")
    return success


def main():
    parser = argparse.ArgumentParser(
        description="Ingest research datasets for Quant Tutor Benchmark"
    )
    parser.add_argument(
        "--dataset",
        choices=["fiqa", "finqa", "convfinqa", "tatqa", "all"],
        default="all",
        help="Which dataset to ingest (default: all)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip datasets that already have data",
    )

    args = parser.parse_args()

    # Ensure output directory exists
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    results = {}

    if args.dataset in ["fiqa", "all"]:
        results["fiqa"] = ingest_fiqa()

    if args.dataset in ["finqa", "all"]:
        results["finqa"] = ingest_finqa()

    if args.dataset in ["convfinqa", "all"]:
        results["convfinqa"] = ingest_convfinqa()

    if args.dataset in ["tatqa", "all"]:
        results["tatqa"] = ingest_tatqa()

    # Summary
    print("\n" + "=" * 60)
    print("Ingestion Summary")
    print("=" * 60)
    for dataset, success in results.items():
        status = "✓ Success" if success else "✗ Failed"
        print(f"  {dataset}: {status}")

    # Exit with error if any failed
    if not all(results.values()):
        sys.exit(1)


if __name__ == "__main__":
    main()
