#!/usr/bin/env python3
"""
Synthesis pipeline: raw JSONL → QuantTutorTask JSON files.

For each raw JSONL record in bench/data/raw/:
1. If the record already has synthesized fields (learner_profile,
   tutoring_strategy, synthetic_response) from the upstream data pipeline,
   uses them directly.
2. If any field is missing, calls LLMs via OpenRouter to generate it:
   - Learner profile  → call_llm_with_json
   - Tutoring strategy → call_llm_with_json
   - Synthetic response → call_llm
3. Converts to unified QuantTutorTask format and writes to
   bench/tasks/layer1/{category}/{task_id}.json

Uses the same OpenRouter / llm_utils infrastructure as the upstream
synthesis pipeline (scripts/03_synthesize/synthesize_tsr.py).

Usage:
    # Convert all raw data to Layer 1 tasks (use existing synthesized fields)
    python synthesize_tasks.py

    # Force LLM re-synthesis even for records that already have fields
    python synthesize_tasks.py --force-llm

    # Limit to specific categories
    python synthesize_tasks.py --categories conceptual_qa code_generation

    # Use a specific model
    python synthesize_tasks.py --force-llm --model google/gemini-3-flash-preview
"""

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
BENCH_ROOT = SCRIPT_DIR.parent.parent  # bench/
PROJECT_ROOT = BENCH_ROOT.parent  # project root
RAW_DIR = BENCH_ROOT / "data" / "raw"
TASK_OUTPUT_DIR = BENCH_ROOT / "tasks" / "layer1"

# Import project LLM utilities
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
sys.path.insert(0, str(BENCH_ROOT))

from orchestrator.schemas import (
    Difficulty,
    GroundTruth,
    QuantTutorTask,
    TaskCategory,
    TaskType,
)

# ── Mapping tables ──────────────────────────────────────────────────

DATASET_CATEGORY_MAP: dict[str, TaskCategory] = {
    "fiqa": TaskCategory.CONCEPTUAL_QA,
    "money.stackexchange": TaskCategory.CONCEPTUAL_QA,
    "reddit.personalfinance": TaskCategory.CONCEPTUAL_QA,
    "cfpb": TaskCategory.CONCEPTUAL_QA,
    "finra": TaskCategory.CONCEPTUAL_QA,
    "sec_investor_gov": TaskCategory.CONCEPTUAL_QA,
    "finqa": TaskCategory.MULTI_STEP_REASONING,
    "convfinqa": TaskCategory.MULTI_STEP_REASONING,
    "tatqa": TaskCategory.DATA_INTERPRETATION,
    "strategy_explanation": TaskCategory.STRATEGY_EXPLANATION,
    "code_generation": TaskCategory.CODE_GENERATION,
    "code_debugging": TaskCategory.CODE_DEBUGGING,
}

LEVEL_DIFFICULTY_MAP: dict[str, Difficulty] = {
    # Layer 1 synthesis: LLM-inferred levels mapped to difficulty.
    # These are not persona IDs — they are difficulty labels for auto-generated tasks.
    "beginner": Difficulty.EASY,
    "intermediate": Difficulty.MEDIUM,
    "advanced": Difficulty.HARD,
}


# ── LLM synthesis prompts ──────────────────────────────────────────

PROFILE_SYSTEM_PROMPT = (
    "You are an expert educational psychologist specializing in financial "
    "literacy education. Analyze the learner's question and infer their "
    "profile. Respond with valid JSON only."
)

PROFILE_USER_PROMPT = """\
Analyze the following financial question and infer a learner profile.

Question: {question}
Tags: {tags}
Context: {context}

Based on this question, create a learner profile with:
1. Knowledge level (beginner/intermediate/advanced)
2. Financial background (e.g., retail investor, finance user, professional)
3. Learning goals (what they're trying to understand)
4. Potential misconceptions they might have
5. Emotional context if detectable (e.g., anxious, curious, frustrated)

Respond with JSON in this exact format:
{{
  "knowledge_level": "beginner|intermediate|advanced",
  "financial_background": "description",
  "learning_goals": ["goal1", "goal2"],
  "potential_misconceptions": ["misconception1", "misconception2"],
  "emotional_context": "description or null"
}}"""

STRATEGY_SYSTEM_PROMPT = (
    "You are an expert pedagogical designer for quantitative finance "
    "education. Design a tutoring strategy that adapts to the learner's "
    "level and maximizes understanding. Respond with valid JSON only."
)

STRATEGY_USER_PROMPT = """\
Design a tutoring strategy for this learner.

Learner Profile:
- Knowledge Level: {knowledge_level}
- Background: {financial_background}
- Goals: {learning_goals}
- Potential Misconceptions: {potential_misconceptions}
- Emotional Context: {emotional_context}

Original Question: {question}

Create a pedagogical strategy with:
1. Teaching approach (Socratic/direct instruction/scaffolded/exploratory)
2. 3-5 concrete teaching steps
3. Key concepts to cover
4. Helpful analogies or examples
5. Follow-up questions to check understanding

Respond with JSON in this exact format:
{{
  "approach": "Socratic|direct instruction|scaffolded|exploratory",
  "steps": ["step1", "step2", "step3"],
  "key_concepts": ["concept1", "concept2"],
  "analogies_or_examples": ["analogy1", "example1"],
  "follow_up_questions": ["question1", "question2"]
}}"""

RESPONSE_SYSTEM_PROMPT = (
    "You are a skilled quantitative finance tutor. Generate a high-quality "
    "tutoring response that teaches the concept effectively while adapting "
    "to the learner's level. Use markdown formatting. Include code examples "
    "when relevant."
)

RESPONSE_USER_PROMPT = """\
Generate a tutoring response for this learner.

LEARNER PROFILE:
- Knowledge Level: {knowledge_level}
- Background: {financial_background}
- Goals: {learning_goals}
- Emotional Context: {emotional_context}

QUESTION: {question}

TUTORING STRATEGY:
- Approach: {approach}
- Steps: {steps}
- Key Concepts: {key_concepts}
- Analogies: {analogies}

REFERENCE ANSWER (for factual accuracy):
{reference_answer}

Generate a detailed, pedagogically sound response that:
1. Matches the learner's knowledge level
2. Follows the tutoring strategy steps
3. Uses the suggested analogies where appropriate
4. Is factually consistent with the reference answer
5. Includes code examples if the topic involves programming
6. Ends with a follow-up question or invitation to continue"""


# ── Core synthesis logic ────────────────────────────────────────────


async def synthesize_profile(
    question: str,
    tags: list[str],
    context: str | None,
    session,
    model: str | None = None,
) -> tuple[dict, str]:
    """Generate a learner profile via LLM."""
    from lib.llm_utils import call_llm_with_json

    prompt = PROFILE_USER_PROMPT.format(
        question=question,
        tags=", ".join(tags) if tags else "N/A",
        context=(context or "N/A")[:2000],
    )
    return await call_llm_with_json(
        prompt=prompt,
        system_prompt=PROFILE_SYSTEM_PROMPT,
        session=session,
        model=model,
    )


async def synthesize_strategy(
    question: str,
    profile: dict,
    session,
    model: str | None = None,
) -> tuple[dict, str]:
    """Generate a tutoring strategy via LLM."""
    from lib.llm_utils import call_llm_with_json

    prompt = STRATEGY_USER_PROMPT.format(
        knowledge_level=profile.get("knowledge_level", "intermediate"),
        financial_background=profile.get("financial_background", "unknown"),
        learning_goals=", ".join(profile.get("learning_goals", [])),
        potential_misconceptions=", ".join(profile.get("potential_misconceptions", [])),
        emotional_context=profile.get("emotional_context") or "None detected",
        question=question,
    )
    return await call_llm_with_json(
        prompt=prompt,
        system_prompt=STRATEGY_SYSTEM_PROMPT,
        session=session,
        model=model,
    )


async def synthesize_response(
    question: str,
    profile: dict,
    strategy: dict,
    reference_answer: str,
    session,
    model: str | None = None,
) -> tuple[str, str]:
    """Generate a synthetic tutoring response via LLM."""
    from lib.llm_utils import call_llm

    prompt = RESPONSE_USER_PROMPT.format(
        knowledge_level=profile.get("knowledge_level", "intermediate"),
        financial_background=profile.get("financial_background", "unknown"),
        learning_goals=", ".join(profile.get("learning_goals", [])),
        emotional_context=profile.get("emotional_context") or "None",
        question=question,
        approach=strategy.get("approach", "scaffolded"),
        steps="\n".join(f"- {s}" for s in strategy.get("steps", [])),
        key_concepts=", ".join(strategy.get("key_concepts", [])),
        analogies=", ".join(strategy.get("analogies_or_examples", []))
        or "None specified",
        reference_answer=reference_answer[:2000],
    )
    return await call_llm(
        prompt=prompt,
        system_prompt=RESPONSE_SYSTEM_PROMPT,
        temperature=0.7,
        max_tokens=3072,
        session=session,
        model=model,
    )


async def synthesize_record(
    record: dict,
    session,
    force_llm: bool = False,
    model: str | None = None,
) -> dict:
    """Synthesize missing fields for a single raw record.

    If force_llm=False and fields already exist (from upstream pipeline),
    they are reused. Otherwise, calls LLMs to generate them.

    Returns the record enriched with synthesized fields + model metadata.
    """
    question = record.get("question", "")
    tags = record.get("tags", [])
    context = record.get("context")
    reference_answer = record.get("reference_answer", "")

    # --- Learner Profile ---
    existing_profile = record.get("learner_profile")
    if existing_profile and not force_llm:
        profile = existing_profile
        profile_model = record.get("profile_model", "upstream")
    else:
        profile, profile_model = await synthesize_profile(
            question, tags, context, session, model
        )

    # --- Tutoring Strategy ---
    existing_strategy = record.get("tutoring_strategy")
    if existing_strategy and not force_llm:
        strategy = existing_strategy
        strategy_model = record.get("strategy_model", "upstream")
    else:
        strategy, strategy_model = await synthesize_strategy(
            question, profile, session, model
        )

    # --- Synthetic Response ---
    existing_response = record.get("synthetic_response")
    if existing_response and not force_llm:
        response = existing_response
        teacher_model = record.get("teacher_model", "upstream")
    else:
        response, teacher_model = await synthesize_response(
            question, profile, strategy, reference_answer, session, model
        )

    # Merge back into record
    record["learner_profile"] = profile
    record["tutoring_strategy"] = strategy
    record["synthetic_response"] = response
    record["teacher_model"] = teacher_model
    record["profile_model"] = profile_model
    record["strategy_model"] = strategy_model
    record["synthesis_timestamp"] = datetime.now(timezone.utc).isoformat()

    return record


def record_to_task(record: dict) -> QuantTutorTask:
    """Convert an enriched raw record into a QuantTutorTask."""
    source_dataset = record.get("source_dataset", "unknown")
    category = DATASET_CATEGORY_MAP.get(source_dataset, TaskCategory.CONCEPTUAL_QA)

    learner_profile = record.get("learner_profile", {})
    knowledge_level = learner_profile.get("knowledge_level", "intermediate")
    difficulty = LEVEL_DIFFICULTY_MAP.get(knowledge_level, Difficulty.MEDIUM)

    ground_truth = GroundTruth(
        expected_outcome=record.get("reference_answer", ""),
    )

    return QuantTutorTask(
        task_id=record.get("id", ""),
        version="1.0",
        difficulty=difficulty,
        category=category,
        task_type=TaskType.SINGLE_TURN,
        description=record.get("question", ""),
        context=record.get("context"),
        reference_answer=record.get("reference_answer", ""),
        synthetic_response=record.get("synthetic_response", ""),
        source_dataset=source_dataset,
        tags=record.get("tags", []),
        ground_truth=ground_truth,
    )


def save_task(task: QuantTutorTask, output_dir: Path) -> Path:
    """Save a QuantTutorTask as JSON in the appropriate category folder."""
    cat_dir = output_dir / task.category.value
    cat_dir.mkdir(parents=True, exist_ok=True)
    out_path = cat_dir / f"{task.task_id}.json"
    with open(out_path, "w") as f:
        f.write(task.model_dump_json(indent=2))
    return out_path


# ── Main pipeline ───────────────────────────────────────────────────


async def run_pipeline(
    raw_dir: Path,
    output_dir: Path,
    force_llm: bool = False,
    model: str | None = None,
    categories: list[str] | None = None,
    max_concurrent: int = 5,
    verbose: bool = True,
) -> dict[str, int]:
    """Run the full synthesis pipeline.

    Args:
        raw_dir: Directory containing raw JSONL files.
        output_dir: Root output directory for task JSONs.
        force_llm: If True, re-synthesize all fields via LLM even if present.
        model: Force a specific LLM model for all calls.
        categories: Filter output to specific categories.
        max_concurrent: Max concurrent LLM API calls.
        verbose: Print progress.

    Returns:
        Dict mapping category → count of generated tasks.
    """
    import aiohttp

    if not raw_dir.exists():
        print(f"Error: raw data directory not found: {raw_dir}")
        return {}

    # Collect all raw records
    all_records: list[dict] = []
    for jsonl_file in sorted(raw_dir.glob("*.jsonl")):
        with open(jsonl_file) as f:
            for line in f:
                line = line.strip()
                if line:
                    all_records.append(json.loads(line))

    if verbose:
        print(f"Loaded {len(all_records)} raw records from {raw_dir}")

    # Determine which records need LLM synthesis
    needs_llm = []
    ready = []
    for rec in all_records:
        has_all = (
            rec.get("learner_profile")
            and rec.get("tutoring_strategy")
            and rec.get("synthetic_response")
        )
        if has_all and not force_llm:
            ready.append(rec)
        else:
            needs_llm.append(rec)

    if verbose:
        print(f"  Records with existing synthesis: {len(ready)}")
        print(f"  Records needing LLM synthesis:   {len(needs_llm)}")

    # Run LLM synthesis for records that need it
    if needs_llm:
        if verbose:
            print(
                f"\nStarting LLM synthesis ({len(needs_llm)} records, "
                f"concurrency={max_concurrent})..."
            )

        semaphore = asyncio.Semaphore(max_concurrent)

        async def _synth(rec):
            async with semaphore:
                async with aiohttp.ClientSession() as session:
                    return await synthesize_record(
                        rec, session, force_llm=True, model=model
                    )

        tasks = [_synth(rec) for rec in needs_llm]
        results = []
        for i, coro in enumerate(asyncio.as_completed(tasks)):
            try:
                result = await coro
                results.append(result)
                if verbose and (i + 1) % 10 == 0:
                    print(f"  Synthesized {i + 1}/{len(needs_llm)}...")
            except Exception as e:
                print(f"  Error synthesizing record: {e}")

        ready.extend(results)

    # Convert to QuantTutorTask and save
    stats: dict[str, int] = {}
    total = 0

    for rec in ready:
        task = record_to_task(rec)

        # Apply category filter
        if categories and task.category.value not in categories:
            continue

        save_task(task, output_dir)
        stats[task.category.value] = stats.get(task.category.value, 0) + 1
        total += 1

    if verbose:
        print(f"\nGenerated {total} Layer 1 task JSONs:")
        for cat, count in sorted(stats.items()):
            print(f"  {cat}: {count}")
        print(f"Output: {output_dir}")

    return stats


def main():
    parser = argparse.ArgumentParser(
        description="Synthesize Layer 1 tasks from raw JSONL data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        default=RAW_DIR,
        help=f"Input directory with raw JSONL files (default: {RAW_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=TASK_OUTPUT_DIR,
        help=f"Output directory for task JSONs (default: {TASK_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--force-llm",
        action="store_true",
        help="Re-synthesize all fields via LLM even if already present",
    )
    parser.add_argument(
        "--model",
        type=str,
        default=None,
        help="Force a specific LLM model (e.g., google/gemini-3-flash-preview)",
    )
    parser.add_argument(
        "--categories",
        nargs="+",
        default=None,
        help="Filter to specific categories (e.g., conceptual_qa code_generation)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=5,
        help="Max concurrent LLM API calls (default: 5)",
    )
    args = parser.parse_args()

    asyncio.run(
        run_pipeline(
            raw_dir=args.raw_dir,
            output_dir=args.output_dir,
            force_llm=args.force_llm,
            model=args.model,
            categories=args.categories,
            max_concurrent=args.max_concurrent,
        )
    )


if __name__ == "__main__":
    main()
