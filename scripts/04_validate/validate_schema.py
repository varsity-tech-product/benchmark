#!/usr/bin/env python3
"""
Validate synthesized data against the final schema.

Reads synthesized JSONL, validates each record, and outputs
valid records to the packaged directory.
"""

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError
from tqdm import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.schemas import FinalBenchmarkRecord

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
SYNTHESIZED_DIR = PROJECT_ROOT / "data" / "02_synthesized"
OUTPUT_DIR = PROJECT_ROOT / "data" / "03_packaged"


def validate_record(record_dict: dict) -> tuple[bool, str]:
    """
    Validate a single record against the schema.

    Args:
        record_dict: Record as dictionary

    Returns:
        Tuple of (is_valid, error_message)
    """
    try:
        FinalBenchmarkRecord.model_validate(record_dict)
        return True, ""
    except ValidationError as e:
        return False, str(e)


def validate_file(
    input_path: Path,
    output_path: Path,
    error_log_path: Path,
) -> dict:
    """
    Validate all records in a JSONL file.

    Args:
        input_path: Input JSONL file
        output_path: Output JSONL file for valid records
        error_log_path: Path to write validation errors

    Returns:
        Statistics dictionary
    """
    stats = {
        "total": 0,
        "valid": 0,
        "invalid": 0,
        "errors": [],
    }

    # Ensure output directories exist
    output_path.parent.mkdir(parents=True, exist_ok=True)
    error_log_path.parent.mkdir(parents=True, exist_ok=True)

    # Count total lines for progress bar
    with open(input_path) as f:
        total_lines = sum(1 for _ in f)

    # Process records
    with open(input_path) as f_in, \
         open(output_path, "w") as f_out, \
         open(error_log_path, "w") as f_err:

        for line_num, line in enumerate(tqdm(f_in, total=total_lines, desc="Validating"), 1):
            stats["total"] += 1

            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                stats["invalid"] += 1
                error_info = {
                    "line": line_num,
                    "error": f"JSON parse error: {e}",
                    "content_preview": line[:200],
                }
                stats["errors"].append(error_info)
                f_err.write(json.dumps(error_info) + "\n")
                continue

            is_valid, error_msg = validate_record(record)

            if is_valid:
                stats["valid"] += 1
                f_out.write(line)
            else:
                stats["invalid"] += 1
                error_info = {
                    "line": line_num,
                    "record_id": record.get("id", "unknown"),
                    "error": error_msg,
                }
                stats["errors"].append(error_info)
                f_err.write(json.dumps(error_info) + "\n")

    return stats


def print_summary(stats: dict):
    """Print validation summary."""
    print("\n" + "=" * 60)
    print("Validation Summary")
    print("=" * 60)
    print(f"Total records:   {stats['total']}")
    print(f"Valid records:   {stats['valid']} ({100*stats['valid']/max(stats['total'],1):.1f}%)")
    print(f"Invalid records: {stats['invalid']} ({100*stats['invalid']/max(stats['total'],1):.1f}%)")

    if stats["errors"]:
        print("\nSample errors (first 5):")
        for error in stats["errors"][:5]:
            print(f"  - Line {error.get('line', '?')}: {error.get('error', 'unknown')[:100]}")


def main():
    parser = argparse.ArgumentParser(
        description="Validate synthesized data against schema"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=SYNTHESIZED_DIR / "synthesized_data.jsonl",
        help="Input synthesized JSONL file",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "quant_tutor_benchmark.jsonl",
        help="Output validated JSONL file",
    )
    parser.add_argument(
        "--error-log",
        type=Path,
        default=OUTPUT_DIR / "validation_errors.jsonl",
        help="Path to write validation errors",
    )

    args = parser.parse_args()

    # Check input exists
    if not args.input.exists():
        print(f"Error: Input file not found: {args.input}")
        sys.exit(1)

    print(f"Validating: {args.input}")
    print(f"Output: {args.output}")

    stats = validate_file(args.input, args.output, args.error_log)

    print_summary(stats)

    # Exit with error if any invalid records
    if stats["invalid"] > 0:
        print(f"\nError log written to: {args.error_log}")
        sys.exit(1)
    else:
        print("\n✓ All records valid!")


if __name__ == "__main__":
    main()
