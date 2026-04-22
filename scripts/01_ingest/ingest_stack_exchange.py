#!/usr/bin/env python3
"""
Ingest Money Stack Exchange data dump from Internet Archive.

Downloads the 7z archive and extracts XML files.
"""

import argparse
import sys
from pathlib import Path

import requests
from tqdm import tqdm

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
RAW_DATA_DIR = PROJECT_ROOT / "data" / "00_raw" / "money.stackexchange.com"

# Stack Exchange data dump URL
ARCHIVE_URL = "https://archive.org/download/stackexchange/money.stackexchange.com.7z"


def download_archive(dest_path: Path, resume: bool = True) -> bool:
    """
    Download the Stack Exchange archive with progress and resume support.

    Args:
        dest_path: Path to save the archive
        resume: Whether to resume partial downloads

    Returns:
        True if successful
    """
    print(f"Downloading from {ARCHIVE_URL}")
    print(f"Destination: {dest_path}")

    headers = {}
    mode = "wb"
    initial_size = 0

    # Check for partial download
    if resume and dest_path.exists():
        initial_size = dest_path.stat().st_size
        headers["Range"] = f"bytes={initial_size}-"
        mode = "ab"
        print(f"Resuming from {initial_size / 1024 / 1024:.1f} MB")

    try:
        response = requests.get(ARCHIVE_URL, stream=True, headers=headers)

        # Handle resume response
        if response.status_code == 416:  # Range not satisfiable
            print("File already complete")
            return True
        elif response.status_code == 206:  # Partial content
            print("Resuming download...")
        elif response.status_code == 200:
            if initial_size > 0:
                print("Server doesn't support resume, starting fresh")
                mode = "wb"
                initial_size = 0
        else:
            response.raise_for_status()

        # Get total size
        content_length = response.headers.get("content-length")
        if content_length:
            total_size = int(content_length) + initial_size
        else:
            total_size = None

        # Download with progress bar
        with open(dest_path, mode) as f:
            with tqdm(
                total=total_size,
                initial=initial_size,
                unit="iB",
                unit_scale=True,
                desc="Downloading",
            ) as pbar:
                for chunk in response.iter_content(
                    chunk_size=1024 * 1024
                ):  # 1MB chunks
                    size = f.write(chunk)
                    pbar.update(size)

        print("✓ Download complete")
        return True

    except requests.RequestException as e:
        print(f"Error downloading: {e}")
        return False


def extract_archive(archive_path: Path, output_dir: Path) -> bool:
    """
    Extract the 7z archive.

    Args:
        archive_path: Path to the 7z archive
        output_dir: Directory to extract to

    Returns:
        True if successful
    """
    print(f"\nExtracting {archive_path} to {output_dir}")

    try:
        import py7zr

        with py7zr.SevenZipFile(archive_path, mode="r") as archive:
            archive.extractall(path=output_dir)

        print("✓ Extraction complete")
        return True

    except ImportError:
        print("Error: py7zr not installed. Run: pip install py7zr")
        return False
    except Exception as e:
        print(f"Error extracting archive: {e}")
        return False


def verify_extraction(output_dir: Path) -> bool:
    """
    Verify that expected files were extracted.

    Returns:
        True if key files exist
    """
    expected_files = [
        "Posts.xml",
        "Users.xml",
        "Comments.xml",
        "Tags.xml",
    ]

    print("\nVerifying extracted files...")

    all_found = True
    for filename in expected_files:
        filepath = output_dir / filename
        if filepath.exists():
            size_mb = filepath.stat().st_size / 1024 / 1024
            print(f"  ✓ {filename} ({size_mb:.1f} MB)")
        else:
            print(f"  ✗ {filename} NOT FOUND")
            all_found = False

    return all_found


def main():
    parser = argparse.ArgumentParser(
        description="Download and extract Money Stack Exchange data dump"
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip download if archive exists",
    )
    parser.add_argument(
        "--skip-extract",
        action="store_true",
        help="Skip extraction if files exist",
    )
    parser.add_argument(
        "--keep-archive",
        action="store_true",
        help="Keep the 7z archive after extraction",
    )

    args = parser.parse_args()

    # Ensure output directory exists
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)

    archive_path = RAW_DATA_DIR / "money.stackexchange.com.7z"

    # Step 1: Download
    if args.skip_download and archive_path.exists():
        print(f"Skipping download, archive exists: {archive_path}")
    else:
        if not download_archive(archive_path):
            sys.exit(1)

    # Step 2: Check if already extracted
    posts_xml = RAW_DATA_DIR / "Posts.xml"
    if args.skip_extract and posts_xml.exists():
        print("\nSkipping extraction, Posts.xml exists")
    else:
        if not extract_archive(archive_path, RAW_DATA_DIR):
            sys.exit(1)

    # Step 3: Verify
    if not verify_extraction(RAW_DATA_DIR):
        print("\nWarning: Some expected files are missing")
        sys.exit(1)

    # Step 4: Optionally remove archive
    if not args.keep_archive and archive_path.exists():
        print("\nRemoving archive to save space...")
        archive_path.unlink()
        print("✓ Archive removed")

    print("\n" + "=" * 60)
    print("Stack Exchange ingestion complete!")
    print(f"Data location: {RAW_DATA_DIR}")
    print("=" * 60)


if __name__ == "__main__":
    main()
