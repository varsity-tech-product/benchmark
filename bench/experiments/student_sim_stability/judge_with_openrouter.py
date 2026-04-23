"""Run rendered stability judge prompts through an OpenRouter judge model.

This stage consumes `judge_inputs/*.json` files produced by
`render_judge_prompts.py` and writes one JSON result per prompt into
`judge_outputs/`.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

BENCH_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(BENCH_ROOT))

from experiments.student_sim_stability.config import (  # noqa: E402
    JUDGE_MAX_WORKERS,
    JUDGE_MODEL,
    JUDGE_TEMPERATURE,
    OUTPUT_DIR,
)
from server.config.llm_config import OPENROUTER_BASE_URL  # noqa: E402
from server.config.pricing import get_llm_cost_kwargs  # noqa: E402
from server.eval.judges.runtime.llm_client import EwanLLMClient  # noqa: E402

log = logging.getLogger(__name__)

_THREAD_LOCAL = threading.local()

_REQUIRED_SCORE_KEYS: dict[str, set[str]] = {
    "D1": {
        "reasoning",
        "knowledge_boundary",
        "emotional_tone",
        "behavioral_rules",
        "overall",
    },
    "D2": {
        "reasoning",
        "topic_trajectory",
        "knowledge_display",
        "emotional_consistency",
        "question_patterns",
        "overall_reproducibility",
    },
    "D3": {
        "reasoning",
        "knowledge_boundary_preserved",
        "emotional_profile_preserved",
        "behavioral_rules_preserved",
        "persona_distinguishability",
        "overall_cross_model",
        "best_set",
        "worst_set",
    },
    "D4": {"reasoning", "per_turn", "overall_drift_score", "drift_onset_turn"},
    "control": {"reasoning", "distinctiveness", "persona_value_add"},
}


@dataclass(frozen=True)
class JudgeConfig:
    model: str
    temperature: float
    max_retries: int
    retry_delay: float


def default_results_dir() -> Path:
    return BENCH_ROOT / OUTPUT_DIR


def _client_for_thread(config: JudgeConfig) -> EwanLLMClient:
    key = (config.model, config.temperature)
    cached = getattr(_THREAD_LOCAL, "client_key", None)
    if cached != key:
        _THREAD_LOCAL.client = EwanLLMClient(
            model=config.model,
            base_url=OPENROUTER_BASE_URL,
            temperature=config.temperature,
            **get_llm_cost_kwargs(config.model),
        )
        _THREAD_LOCAL.client_key = key
    return _THREAD_LOCAL.client


def extract_json_object(text: str) -> dict:
    """Extract the first JSON object from a model response."""
    decoder = json.JSONDecoder()
    for idx, char in enumerate(text):
        if char != "{":
            continue
        try:
            value, _ = decoder.raw_decode(text[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ValueError(f"No JSON object found in judge response: {text[:300]}")


def validate_scores(dimension: str, scores: dict) -> None:
    required = _REQUIRED_SCORE_KEYS.get(dimension)
    if not required:
        raise ValueError(f"Unsupported judge dimension: {dimension}")
    missing = sorted(required - set(scores))
    if missing:
        raise ValueError(f"{dimension} judge output missing keys: {missing}")


def atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    ) as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
        tmp_path = Path(fh.name)
    tmp_path.replace(path)


def _load_manifest(path: Path) -> set[str]:
    text = path.read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {line.strip() for line in text.splitlines() if line.strip()}
    if not isinstance(data, list):
        raise ValueError(f"Manifest must be a list or newline file: {path}")
    return {str(item) for item in data}


def select_inputs(
    input_dir: Path,
    dimension: str,
    manifest: Path | None = None,
    limit: int | None = None,
) -> list[Path]:
    if dimension == "all":
        files = sorted(input_dir.glob("*.json"))
    else:
        files = sorted(input_dir.glob(f"{dimension}__*.json"))
    if manifest:
        wanted = _load_manifest(manifest)
        files = [p for p in files if p.name in wanted or p.stem in wanted]
    if limit is not None:
        files = files[:limit]
    return files


def judge_one(
    input_path: Path,
    output_dir: Path,
    config: JudgeConfig,
    overwrite: bool,
) -> tuple[str, bool, float, str | None]:
    with open(input_path, encoding="utf-8") as fh:
        payload = json.load(fh)
    eval_id = payload["eval_id"]
    dimension = payload["dimension"]
    output_path = output_dir / f"{eval_id}.json"
    if output_path.exists() and not overwrite:
        return eval_id, False, 0.0, None

    last_error: Exception | None = None
    for attempt in range(1, config.max_retries + 1):
        try:
            client = _client_for_thread(config)
            text, cost = client.generate(payload["prompt"])
            scores = extract_json_object(text)
            validate_scores(dimension, scores)
            result = {
                "eval_id": eval_id,
                "dimension": dimension,
                "scores": scores,
                "approach": "openrouter",
                "judge_model": config.model,
                "judge_temperature": config.temperature,
                "cost_usd": cost,
                "source_file": input_path.name,
                "judged_at": datetime.now(timezone.utc).isoformat(),
            }
            atomic_write_json(output_path, result)
            return eval_id, True, cost, None
        except Exception as exc:  # noqa: BLE001 - report and retry judge failures
            last_error = exc
            if attempt < config.max_retries:
                time.sleep(config.retry_delay * attempt)

    return eval_id, False, 0.0, str(last_error)


def run_judge(
    input_dir: Path,
    output_dir: Path,
    dimension: str = "all",
    manifest: Path | None = None,
    limit: int | None = None,
    workers: int = JUDGE_MAX_WORKERS,
    model: str = JUDGE_MODEL,
    temperature: float = JUDGE_TEMPERATURE,
    overwrite: bool = False,
    max_retries: int = 3,
    retry_delay: float = 2.0,
) -> dict:
    import os

    if not os.environ.get("OPENROUTER_API_KEY"):
        raise RuntimeError("OPENROUTER_API_KEY environment variable is not set")

    files = select_inputs(
        input_dir, dimension=dimension, manifest=manifest, limit=limit
    )
    config = JudgeConfig(
        model=model,
        temperature=temperature,
        max_retries=max_retries,
        retry_delay=retry_delay,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    stats = {
        "selected": len(files),
        "written": 0,
        "skipped": 0,
        "failed": 0,
        "cost_usd": 0.0,
        "failures": [],
    }
    if not files:
        return stats

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = [
            pool.submit(judge_one, p, output_dir, config, overwrite) for p in files
        ]
        for future in as_completed(futures):
            eval_id, wrote, cost, error = future.result()
            if error:
                stats["failed"] += 1
                stats["failures"].append({"eval_id": eval_id, "error": error})
                log.error("Judge failed for %s: %s", eval_id, error)
            elif wrote:
                stats["written"] += 1
                stats["cost_usd"] += cost
                log.info("Judged %s", eval_id)
            else:
                stats["skipped"] += 1
    return stats


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--dimension", default="all", choices=["D1", "D2", "D3", "D4", "control", "all"]
    )
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=JUDGE_MAX_WORKERS)
    parser.add_argument("--model", default=JUDGE_MODEL)
    parser.add_argument("--temperature", type=float, default=JUDGE_TEMPERATURE)
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument(
        "--log-level", default="INFO", choices=["DEBUG", "INFO", "WARNING", "ERROR"]
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    results_dir = default_results_dir()
    input_dir = Path(args.input_dir) if args.input_dir else results_dir / "judge_inputs"
    output_dir = (
        Path(args.output_dir) if args.output_dir else results_dir / "judge_outputs"
    )
    stats = run_judge(
        input_dir=input_dir,
        output_dir=output_dir,
        dimension=args.dimension,
        manifest=args.manifest,
        limit=args.limit,
        workers=args.workers,
        model=args.model,
        temperature=args.temperature,
        overwrite=args.overwrite,
        max_retries=args.max_retries,
        retry_delay=args.retry_delay,
    )
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
