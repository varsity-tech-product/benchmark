#!/usr/bin/env python3
"""
Synthesize Tutoring Strategy and Response (TSR) data.

For each structured Q&A record, generates:
1. Learner Profile - inferred knowledge state
2. Tutoring Strategy - pedagogical approach
3. Synthetic Response - generated tutoring response

Uses async processing with checkpointing for resumability.
Randomly selects from 10 diverse models per API call.

Usage:
    # Synthesize all datasets
    python synthesize_tsr.py --all

    # Synthesize a specific dataset with sample limit
    python synthesize_tsr.py --dataset personal_finance_planning --sample 100

    # Synthesize multiple datasets
    python synthesize_tsr.py --dataset tax_and_accounting regulatory_compliance

    # List available datasets
    python synthesize_tsr.py --list
"""

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import yaml
from tqdm.asyncio import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.llm_utils import LLMError, call_llm, call_llm_with_json, select_random_model
from lib.schemas import (
    FinalBenchmarkRecord,
    LearnerProfile,
    StructuredQA,
    TutoringStrategy,
)

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
CLASSIFIED_DIR = PROJECT_ROOT / "data" / "01_structured" / "classified"
OUTPUT_DIR = PROJECT_ROOT / "data" / "02_synthesized"
CONFIG_DIR = PROJECT_ROOT / "configs"


def load_prompts() -> dict:
    """Load prompt templates from config."""
    prompts_path = CONFIG_DIR / "prompts.yaml"
    with open(prompts_path) as f:
        return yaml.safe_load(f)


def list_datasets(classified_dir: Path) -> list[str]:
    """List available dataset names from classified directory."""
    return sorted(f.stem for f in classified_dir.glob("*.jsonl"))


def _normalize_string_list(items: list) -> list[str]:
    """Convert list items that may be dicts into flat strings.

    Some models return structured objects like {"step_number": 1, "title": "...", "description": "..."}
    instead of plain strings. This flattens them.
    """
    result = []
    for item in items:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            parts = [str(v) for v in item.values() if v is not None]
            result.append(" - ".join(parts))
        else:
            result.append(str(item))
    return result


class SynthesisPipeline:
    """Pipeline for synthesizing tutoring data."""

    def __init__(
        self,
        max_concurrent: int = 5,
        checkpoint_every: int = 50,
        checkpoint_file: Optional[Path] = None,
    ):
        self.max_concurrent = max_concurrent
        self.checkpoint_every = checkpoint_every
        self.checkpoint_file = checkpoint_file
        self.prompts = load_prompts()
        self.processed_ids: set[str] = set()
        self.results: list[dict] = []
        self.semaphore: Optional[asyncio.Semaphore] = None

        if checkpoint_file:
            self._load_checkpoint()

    def _load_checkpoint(self):
        """Load checkpoint from file."""
        if self.checkpoint_file and self.checkpoint_file.exists():
            with open(self.checkpoint_file) as f:
                data = json.load(f)
                self.processed_ids = set(data.get("processed_ids", []))
            print(
                f"Loaded checkpoint: {len(self.processed_ids)} records already processed"
            )

    def _save_checkpoint(self):
        """Save checkpoint to file."""
        if self.checkpoint_file:
            self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.checkpoint_file, "w") as f:
                json.dump({"processed_ids": list(self.processed_ids)}, f)

    @staticmethod
    def _is_numerical_reasoning(qa: StructuredQA) -> bool:
        """Check if a record is a numerical/tabular reasoning type."""
        if not qa.tags:
            return False
        numerical_tags = {"numerical-reasoning", "tabular-reasoning", "conversational"}
        return bool(set(qa.tags) & numerical_tags)

    @staticmethod
    def _format_conversation_history(qa: StructuredQA) -> str:
        """Format conversation history for prompt injection."""
        if not qa.conversation_history:
            return "N/A"
        parts = []
        for turn in qa.conversation_history:
            q = turn.get("question", "")
            a = turn.get("answer", "")
            parts.append(f"Q: {q}")
            if a:
                parts.append(f"A: {a}")
        return "\n".join(parts)

    async def generate_learner_profile(
        self,
        qa: StructuredQA,
        session: aiohttp.ClientSession,
    ) -> tuple[LearnerProfile, str]:
        """Generate learner profile for a Q&A pair."""
        if self._is_numerical_reasoning(qa):
            prompt_template = self.prompts["numerical_learner_profile_prompt"]
            prompt = prompt_template.format(
                question=qa.question_body,
                tags=", ".join(qa.tags) if qa.tags else "N/A",
                context=qa.context or "N/A",
                conversation_history=self._format_conversation_history(qa),
            )
            system_prompt = self.prompts["system_prompts"]["numerical_learner_analysis"]
        else:
            prompt_template = self.prompts["learner_profile_prompt"]
            prompt = prompt_template.format(
                title=qa.title or "N/A",
                question=qa.question_body,
                tags=", ".join(qa.tags) if qa.tags else "N/A",
            )
            system_prompt = self.prompts["system_prompts"]["learner_analysis"]

        model = select_random_model()
        data, model_used = await call_llm_with_json(
            prompt=prompt,
            system_prompt=system_prompt,
            session=session,
            model=model,
        )

        # Normalize list fields (some models return dicts instead of strings)
        for key in ("learning_goals", "potential_misconceptions"):
            if key in data and isinstance(data[key], list):
                data[key] = _normalize_string_list(data[key])

        profile = LearnerProfile(**data)
        return profile, model_used

    async def generate_tutoring_strategy(
        self,
        qa: StructuredQA,
        profile: LearnerProfile,
        session: aiohttp.ClientSession,
    ) -> tuple[TutoringStrategy, str]:
        """Generate tutoring strategy based on learner profile."""
        common_kwargs = dict(
            knowledge_level=profile.knowledge_level,
            financial_background=profile.financial_background,
            learning_goals=", ".join(profile.learning_goals),
            potential_misconceptions=", ".join(profile.potential_misconceptions),
            emotional_context=profile.emotional_context or "None detected",
            question=qa.question_body,
        )

        if self._is_numerical_reasoning(qa):
            prompt_template = self.prompts["numerical_tutoring_strategy_prompt"]
            prompt = prompt_template.format(
                **common_kwargs,
                context=qa.context or "N/A",
            )
            system_prompt = self.prompts["system_prompts"]["numerical_strategy_design"]
        else:
            prompt_template = self.prompts["tutoring_strategy_prompt"]
            prompt = prompt_template.format(**common_kwargs)
            system_prompt = self.prompts["system_prompts"]["strategy_design"]

        model = select_random_model()
        data, model_used = await call_llm_with_json(
            prompt=prompt,
            system_prompt=system_prompt,
            session=session,
            model=model,
        )

        # Normalize list fields (some models return dicts instead of strings)
        for key in (
            "steps",
            "key_concepts",
            "analogies_or_examples",
            "follow_up_questions",
        ):
            if key in data and isinstance(data[key], list):
                data[key] = _normalize_string_list(data[key])

        strategy = TutoringStrategy(**data)
        return strategy, model_used

    async def generate_response(
        self,
        qa: StructuredQA,
        profile: LearnerProfile,
        strategy: TutoringStrategy,
        session: aiohttp.ClientSession,
    ) -> tuple[str, str]:
        """Generate synthetic tutoring response."""
        common_kwargs = dict(
            knowledge_level=profile.knowledge_level,
            financial_background=profile.financial_background,
            learning_goals=", ".join(profile.learning_goals),
            emotional_context=profile.emotional_context or "None",
            question=qa.question_body,
            approach=strategy.approach,
            steps="\n".join(f"- {s}" for s in strategy.steps),
            key_concepts=", ".join(strategy.key_concepts),
            analogies_or_examples=(
                ", ".join(strategy.analogies_or_examples)
                if strategy.analogies_or_examples
                else "None specified"
            ),
            reference_answer=qa.answer_body[:2000],
        )

        if self._is_numerical_reasoning(qa):
            prompt_template = self.prompts["numerical_response_generation_prompt"]
            prompt = prompt_template.format(
                **common_kwargs,
                context=(qa.context or "N/A")[:3000],
                conversation_history=self._format_conversation_history(qa),
            )
            system_prompt = self.prompts["system_prompts"][
                "numerical_response_generation"
            ]
        else:
            prompt_template = self.prompts["response_generation_prompt"]
            prompt = prompt_template.format(**common_kwargs)
            system_prompt = self.prompts["system_prompts"]["response_generation"]

        model = select_random_model()
        response, model_used = await call_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=2048,
            session=session,
            model=model,
        )

        # Clean up excessive whitespace (some models pad markdown tables with thousands of spaces)
        response = re.sub(r" {4,}", "    ", response)

        return response, model_used

    async def process_record(
        self,
        qa: StructuredQA,
        session: aiohttp.ClientSession,
    ) -> Optional[FinalBenchmarkRecord]:
        """Process a single Q&A record through the full pipeline."""
        async with self.semaphore:
            try:
                # Step 1: Generate learner profile
                profile, profile_model = await self.generate_learner_profile(
                    qa, session
                )

                # Step 2: Generate tutoring strategy
                strategy, strategy_model = await self.generate_tutoring_strategy(
                    qa, profile, session
                )

                # Step 3: Generate response
                response, response_model = await self.generate_response(
                    qa, profile, strategy, session
                )

                # Create final record
                record = FinalBenchmarkRecord(
                    id=f"{qa.source_dataset}_{qa.source_id}",
                    source_id=qa.source_id,
                    source_dataset=qa.source_dataset,
                    title=qa.title,
                    question=qa.question_body,
                    reference_answer=qa.answer_body,
                    tags=qa.tags,
                    source_url=qa.source_url,
                    creation_date=qa.creation_date,
                    context=qa.context,
                    conversation_history=qa.conversation_history,
                    learner_profile=profile,
                    tutoring_strategy=strategy,
                    synthetic_response=response,
                    teacher_model=response_model,
                    synthesis_timestamp=datetime.now(timezone.utc).isoformat(),
                )

                return record

            except LLMError as e:
                print(f"\n  LLM error for {qa.source_id}: {e}")
                return None
            except Exception as e:
                print(f"\n  Error processing {qa.source_id}: {e}")
                return None

    async def process_batch(
        self,
        records: list[StructuredQA],
        output_file: Path,
        progress_bar: bool = True,
    ) -> int:
        """
        Process a batch of records with concurrency control.

        Args:
            records: List of StructuredQA records
            output_file: Path to write output JSONL
            progress_bar: Whether to show progress bar

        Returns:
            Number of successfully processed records
        """
        self.semaphore = asyncio.Semaphore(self.max_concurrent)

        # Filter out already processed records
        to_process = [r for r in records if r.source_id not in self.processed_ids]
        print(
            f"Processing {len(to_process)} records ({len(self.processed_ids)} already done)"
        )

        if not to_process:
            print("All records already processed!")
            return 0

        success_count = 0
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Open file in append mode
        async with aiohttp.ClientSession() as session:
            with open(output_file, "a") as f:
                tasks = [self.process_record(qa, session) for qa in to_process]

                if progress_bar:
                    iterator = tqdm.as_completed(
                        tasks, total=len(tasks), desc="Synthesizing"
                    )
                else:
                    iterator = asyncio.as_completed(tasks)

                for coro in iterator:
                    result = await coro
                    if result:
                        f.write(result.model_dump_json() + "\n")
                        f.flush()  # Ensure written to disk
                        self.processed_ids.add(result.source_id)
                        success_count += 1

                        # Checkpoint periodically
                        if success_count % self.checkpoint_every == 0:
                            self._save_checkpoint()

        # Final checkpoint
        self._save_checkpoint()
        return success_count


def load_structured_data(
    classified_dir: Path,
    datasets: Optional[list[str]] = None,
    sample: Optional[int] = None,
) -> dict[str, list[StructuredQA]]:
    """Load structured JSONL files from classified directory, grouped by dataset.

    Args:
        classified_dir: Path to classified data directory
        datasets: List of dataset names to load (None = all)
        sample: Max records per dataset (None = all)

    Returns:
        Dict mapping dataset name (file stem) to list of records
    """
    result: dict[str, list[StructuredQA]] = {}

    if datasets:
        jsonl_files = []
        for name in datasets:
            filepath = classified_dir / f"{name}.jsonl"
            if not filepath.exists():
                print(f"Warning: Dataset '{name}' not found at {filepath}")
                continue
            jsonl_files.append(filepath)
    else:
        jsonl_files = sorted(classified_dir.glob("*.jsonl"))

    if not jsonl_files:
        print(f"Warning: No JSONL files found in {classified_dir}")
        return result

    print(f"Loading structured data from {classified_dir}")
    total = 0

    for filepath in jsonl_files:
        ds_name = filepath.stem
        records = []
        print(f"  Loading {filepath.name}...")
        with open(filepath) as f:
            for line in f:
                records.append(StructuredQA.model_validate_json(line))
                if sample and len(records) >= sample:
                    break

        result[ds_name] = records
        total += len(records)
        print(f"    -> {len(records)} records loaded")

    print(f"Loaded {total} records total across {len(result)} datasets")
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Synthesize tutoring data for benchmark",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --all                          Synthesize all datasets
  %(prog)s --dataset tax_and_accounting   Synthesize one dataset
  %(prog)s --dataset tax_and_accounting regulatory_compliance
                                          Synthesize multiple datasets
  %(prog)s --all --sample 50             50 records per dataset
  %(prog)s --list                         List available datasets
        """,
    )

    # Dataset selection (mutually exclusive)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--all",
        action="store_true",
        help="Synthesize all datasets",
    )
    group.add_argument(
        "--dataset",
        nargs="+",
        metavar="NAME",
        help="Dataset name(s) to synthesize (stem of JSONL file)",
    )
    group.add_argument(
        "--list",
        action="store_true",
        help="List available datasets and exit",
    )

    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Max records per dataset (for testing)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=5,
        help="Maximum concurrent API calls (default: 5)",
    )
    parser.add_argument(
        "--checkpoint-every",
        type=int,
        default=50,
        help="Save checkpoint every N records (default: 50)",
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=CLASSIFIED_DIR,
        help="Input directory with classified JSONL files",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Output directory for synthesized data",
    )
    parser.add_argument(
        "--no-checkpoint",
        action="store_true",
        help="Disable checkpointing",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset checkpoint and output (start fresh)",
    )

    args = parser.parse_args()

    # Handle --list
    if args.list:
        datasets = list_datasets(args.input_dir)
        if not datasets:
            print(f"No datasets found in {args.input_dir}")
            sys.exit(1)
        print("Available datasets:")
        for name in datasets:
            filepath = args.input_dir / f"{name}.jsonl"
            line_count = sum(1 for _ in open(filepath))
            print(f"  {name:45s} ({line_count:,} records)")
        sys.exit(0)

    # Determine which datasets to process
    dataset_names = None if args.all else args.dataset

    # Resolve datasets for output file naming and reset
    if dataset_names:
        target_datasets = dataset_names
    else:
        target_datasets = list_datasets(args.input_dir)

    # Handle reset
    if args.reset:
        for ds_name in target_datasets:
            checkpoint = args.output_dir / f"{ds_name}_checkpoint.json"
            output = args.output_dir / f"{ds_name}.jsonl"
            if checkpoint.exists():
                checkpoint.unlink()
                print(f"Checkpoint reset: {ds_name}")
            if output.exists():
                output.unlink()
                print(f"Output reset: {ds_name}")

    # Load structured data (grouped by dataset file)
    records_by_dataset = load_structured_data(
        args.input_dir, datasets=dataset_names, sample=args.sample
    )

    if not records_by_dataset:
        print("No records to process!")
        sys.exit(1)

    total_records = sum(len(v) for v in records_by_dataset.values())
    total_success = 0

    print("\n" + "=" * 60)
    print("Starting synthesis pipeline")
    print("=" * 60)
    print(f"Datasets: {len(records_by_dataset)}")
    print(f"Total records: {total_records}")
    print(f"Max concurrent: {args.max_concurrent}")
    print("Models: 10 (random selection per API call)")
    print(f"Output dir: {args.output_dir}")
    print()

    for ds_name, ds_records in records_by_dataset.items():
        output_file = args.output_dir / f"{ds_name}.jsonl"
        checkpoint_file = None
        if not args.no_checkpoint:
            checkpoint_file = args.output_dir / f"{ds_name}_checkpoint.json"

        pipeline = SynthesisPipeline(
            max_concurrent=args.max_concurrent,
            checkpoint_every=args.checkpoint_every,
            checkpoint_file=checkpoint_file,
        )

        print(f"\n--- [{ds_name}] {len(ds_records)} records -> {output_file.name} ---")

        try:
            success_count = asyncio.run(pipeline.process_batch(ds_records, output_file))
            total_success += success_count
        except KeyboardInterrupt:
            print("\n\nInterrupted! Progress has been checkpointed.")
            sys.exit(1)

        print(f"--- [{ds_name}] Done: {success_count} records ---")

    print("\n" + "=" * 60)
    print("Synthesis complete!")
    print("=" * 60)
    print(f"Successfully processed: {total_success} records")
    print(f"Output directory: {args.output_dir}")


if __name__ == "__main__":
    main()
