#!/usr/bin/env python3
"""
Synthesize Tutoring Strategy and Response (TSR) data.

For each structured Q&A record, generates:
1. Learner Profile - inferred knowledge state
2. Tutoring Strategy - pedagogical approach
3. Synthetic Response - generated tutoring response

Uses async processing with checkpointing for resumability.
"""

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import aiohttp
import yaml
from tqdm.asyncio import tqdm

# Add parent directory to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from lib.llm_utils import call_llm_with_json, call_llm, MODELS, LLMError
from lib.schemas import (
    StructuredQA,
    LearnerProfile,
    TutoringStrategy,
    FinalBenchmarkRecord,
)

# Base paths
SCRIPT_DIR = Path(__file__).parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
STRUCTURED_DIR = PROJECT_ROOT / "data" / "01_structured"
OUTPUT_DIR = PROJECT_ROOT / "data" / "02_synthesized"
CONFIG_DIR = PROJECT_ROOT / "configs"


def load_prompts() -> dict:
    """Load prompt templates from config."""
    prompts_path = CONFIG_DIR / "prompts.yaml"
    with open(prompts_path) as f:
        return yaml.safe_load(f)


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
            print(f"Loaded checkpoint: {len(self.processed_ids)} records already processed")

    def _save_checkpoint(self):
        """Save checkpoint to file."""
        if self.checkpoint_file:
            self.checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self.checkpoint_file, "w") as f:
                json.dump({"processed_ids": list(self.processed_ids)}, f)

    async def generate_learner_profile(
        self,
        qa: StructuredQA,
        session: aiohttp.ClientSession,
    ) -> tuple[LearnerProfile, str]:
        """Generate learner profile for a Q&A pair."""
        prompt_template = self.prompts["learner_profile_prompt"]

        prompt = prompt_template.format(
            title=qa.title or "N/A",
            question=qa.question_body,
            tags=", ".join(qa.tags) if qa.tags else "N/A",
        )

        system_prompt = self.prompts["system_prompts"]["learner_analysis"]

        data, model_used = await call_llm_with_json(
            prompt=prompt,
            system_prompt=system_prompt,
            session=session,
        )

        # Validate and create LearnerProfile
        profile = LearnerProfile(**data)
        return profile, model_used

    async def generate_tutoring_strategy(
        self,
        qa: StructuredQA,
        profile: LearnerProfile,
        session: aiohttp.ClientSession,
    ) -> tuple[TutoringStrategy, str]:
        """Generate tutoring strategy based on learner profile."""
        prompt_template = self.prompts["tutoring_strategy_prompt"]

        prompt = prompt_template.format(
            knowledge_level=profile.knowledge_level,
            financial_background=profile.financial_background,
            learning_goals=", ".join(profile.learning_goals),
            potential_misconceptions=", ".join(profile.potential_misconceptions),
            emotional_context=profile.emotional_context or "None detected",
            question=qa.question_body,
        )

        system_prompt = self.prompts["system_prompts"]["strategy_design"]

        data, model_used = await call_llm_with_json(
            prompt=prompt,
            system_prompt=system_prompt,
            session=session,
        )

        # Validate and create TutoringStrategy
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
        prompt_template = self.prompts["response_generation_prompt"]

        prompt = prompt_template.format(
            knowledge_level=profile.knowledge_level,
            financial_background=profile.financial_background,
            learning_goals=", ".join(profile.learning_goals),
            emotional_context=profile.emotional_context or "None",
            question=qa.question_body,
            approach=strategy.approach,
            steps="\n".join(f"- {s}" for s in strategy.steps),
            key_concepts=", ".join(strategy.key_concepts),
            analogies_or_examples=", ".join(strategy.analogies_or_examples) if strategy.analogies_or_examples else "None specified",
            reference_answer=qa.answer_body[:2000],  # Truncate long answers
        )

        system_prompt = self.prompts["system_prompts"]["response_generation"]

        response, model_used = await call_llm(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=0.7,
            max_tokens=2048,
            session=session,
        )

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
                profile, profile_model = await self.generate_learner_profile(qa, session)

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
                    teacher_model=response_model,  # Record which model generated response
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
        print(f"Processing {len(to_process)} records ({len(self.processed_ids)} already done)")

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
                    iterator = tqdm.as_completed(tasks, total=len(tasks), desc="Synthesizing")
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
    structured_dir: Path,
    sample: Optional[int] = None,
) -> list[StructuredQA]:
    """Load all structured JSONL files."""
    records = []

    jsonl_files = list(structured_dir.glob("*.jsonl"))
    if not jsonl_files:
        print(f"Warning: No JSONL files found in {structured_dir}")
        return records

    print(f"Loading structured data from {structured_dir}")

    for filepath in jsonl_files:
        print(f"  Loading {filepath.name}...")
        with open(filepath) as f:
            for line in f:
                record = StructuredQA.model_validate_json(line)
                records.append(record)

                if sample and len(records) >= sample:
                    break

        if sample and len(records) >= sample:
            break

    print(f"Loaded {len(records)} records total")
    return records


def main():
    parser = argparse.ArgumentParser(
        description="Synthesize tutoring data for benchmark"
    )
    parser.add_argument(
        "--sample",
        type=int,
        default=None,
        help="Process only N records (for testing)",
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
        default=STRUCTURED_DIR,
        help="Input directory with structured JSONL files",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR / "synthesized_data.jsonl",
        help="Output file path",
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

    # Handle reset
    if args.reset:
        checkpoint_file = OUTPUT_DIR / "synthesis_checkpoint.json"
        if checkpoint_file.exists():
            checkpoint_file.unlink()
            print("Checkpoint reset")
        if args.output.exists():
            args.output.unlink()
            print("Output file reset")

    # Load structured data
    records = load_structured_data(args.input_dir, sample=args.sample)

    if not records:
        print("No records to process!")
        sys.exit(1)

    # Set up checkpoint
    checkpoint_file = None
    if not args.no_checkpoint:
        checkpoint_file = OUTPUT_DIR / "synthesis_checkpoint.json"

    # Create pipeline
    pipeline = SynthesisPipeline(
        max_concurrent=args.max_concurrent,
        checkpoint_every=args.checkpoint_every,
        checkpoint_file=checkpoint_file,
    )

    # Run synthesis
    print("\n" + "=" * 60)
    print("Starting synthesis pipeline")
    print("=" * 60)
    print(f"Input records: {len(records)}")
    print(f"Max concurrent: {args.max_concurrent}")
    print(f"Output: {args.output}")
    print()

    try:
        success_count = asyncio.run(
            pipeline.process_batch(records, args.output)
        )
    except KeyboardInterrupt:
        print("\n\nInterrupted! Progress has been checkpointed.")
        sys.exit(1)

    print("\n" + "=" * 60)
    print("Synthesis complete!")
    print("=" * 60)
    print(f"Successfully processed: {success_count} records")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
