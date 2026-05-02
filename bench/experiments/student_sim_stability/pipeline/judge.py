"""Run rendered stability judge prompts through an OpenRouter judge model.

This stage consumes `judge_inputs/*.json` files produced by
`render_judge_prompts.py` and writes one JSON result per prompt into
`judge_outputs/`.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from experiments.student_sim_stability.core.io_utils import (
    atomic_write_json,
    input_payload_hash,
    safe_model_dir,
)
from experiments.student_sim_stability.core.paths import BENCH_ROOT, default_results_dir

if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from server.config.bootstrap import load_server_env  # noqa: E402

load_server_env(BENCH_ROOT)

from experiments.student_sim_stability.core.config import (  # noqa: E402
    JUDGE_MAX_WORKERS,
    JUDGE_MODEL,
    JUDGE_MODELS,
    JUDGE_TEMPERATURE,
)
from experiments.student_sim_stability.core.rubrics import (
    DIMENSION_TO_FILE,
    required_score_keys,
)

# noqa: E402
from server.config.llm_config import (  # noqa: E402
    get_openrouter_base_url,
    require_openrouter_api_key,
)
from server.config.pricing import get_llm_cost_kwargs  # noqa: E402
from eval.judges.runtime.llm_client import EwanLLMClient  # noqa: E402

log = logging.getLogger(__name__)

_THREAD_LOCAL = threading.local()

_DIMENSION_PREFIXES = tuple(DIMENSION_TO_FILE)


@dataclass(frozen=True)
class JudgeConfig:
    model: str
    temperature: float
    max_retries: int
    retry_delay: float


def _client_for_thread(config: JudgeConfig) -> EwanLLMClient:
    key = (config.model, config.temperature)
    cached = getattr(_THREAD_LOCAL, "client_key", None)
    if cached != key:
        _THREAD_LOCAL.client = EwanLLMClient(
            model=config.model,
            api_key=require_openrouter_api_key(
                purpose=f"student-sim-stability judge model {config.model}"
            ),
            base_url=get_openrouter_base_url(),
            temperature=config.temperature,
            **get_llm_cost_kwargs(config.model),
        )
        _THREAD_LOCAL.client_key = key
    return _THREAD_LOCAL.client


def extract_json_object(text: str) -> dict:
    """Extract the first JSON object from a model response.

    Why this isn't ``eval.judges.runtime.scoring_utils.extract_json_from_response``:
    that helper falls back to a flat-object regex (``\\{[^{}]*\\}``) and returns
    ``{}`` on failure. Judge outputs for S1/S2/S4/control contain nested
    structures (``per_turn`` is a list of dicts, ``scores_by_judge`` is a dict);
    the regex would silently truncate them and the empty-dict fallback would
    swallow malformed responses that we explicitly want to surface as retries.
    This implementation uses ``JSONDecoder.raw_decode`` to walk the text and
    parse the first complete JSON object, raising if none is found so the
    caller sees the failure.
    """
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
    required = required_score_keys(dimension)
    missing = sorted(required - set(scores))
    if missing:
        raise ValueError(f"{dimension} judge output missing keys: {missing}")


def _validate_existing_output(
    output_path: Path,
    payload: dict,
    config: JudgeConfig,
) -> None:
    with open(output_path, encoding="utf-8") as fh:
        existing = json.load(fh)
    expected_hash = input_payload_hash(payload)
    expected = {
        "eval_id": payload["eval_id"],
        "dimension": payload["dimension"],
        "judge_model": config.model,
        "judge_temperature": config.temperature,
        "input_sha256": expected_hash,
        "rubric_id": (payload.get("metadata") or {}).get("rubric_id")
        or payload.get("rubric_id"),
        "rubric_version": (payload.get("metadata") or {}).get("rubric_version")
        or payload.get("rubric_version"),
    }
    mismatches = [
        f"{field}: {existing.get(field)!r} != {value!r}"
        for field, value in expected.items()
        if existing.get(field) != value
    ]
    if mismatches:
        raise ValueError(
            f"Existing judge output is stale or from a different config: "
            f"{output_path.name}; " + "; ".join(mismatches)
        )
    validate_scores(existing["dimension"], existing.get("scores", {}))


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
    exclude_dimensions: Iterable[str] | None = None,
) -> list[Path]:
    excluded = set(exclude_dimensions or [])
    invalid = sorted(excluded - set(_DIMENSION_PREFIXES))
    if invalid:
        raise ValueError(f"Unsupported excluded judge dimensions: {invalid}")
    if dimension == "all":
        files = [
            path
            for path in sorted(input_dir.glob("*.json"))
            if path.name.split("__", 1)[0] not in excluded
        ]
    else:
        if dimension in excluded:
            return []
        files = sorted(input_dir.glob(f"{dimension}__*.json"))
    if manifest:
        wanted = _load_manifest(manifest)
        files = [p for p in files if p.name in wanted or p.stem in wanted]
    if limit is not None:
        files = files[:limit]
    return files


def _prefixes_for_dimension(dimension: str) -> list[str]:
    if dimension == "all":
        return list(_DIMENSION_PREFIXES)
    if dimension not in _DIMENSION_PREFIXES:
        raise ValueError(f"Unsupported judge dimension: {dimension}")
    return [dimension]


def expected_output_names(
    input_dir: Path,
    dimension: str,
    manifest: Path | None = None,
    limit: int | None = None,
    exclude_dimensions: Iterable[str] | None = None,
) -> set[str]:
    return {
        path.name
        for path in select_inputs(
            input_dir,
            dimension,
            manifest,
            limit,
            exclude_dimensions=exclude_dimensions,
        )
    }


def clean_judge_outputs(
    output_dir: Path,
    dimension: str,
    exclude_dimensions: Iterable[str] | None = None,
) -> int:
    """Remove stale judge outputs for the selected dimension only."""
    output_dir.mkdir(parents=True, exist_ok=True)
    excluded = set(exclude_dimensions or [])
    prefixes = [
        prefix
        for prefix in _prefixes_for_dimension(dimension)
        if prefix not in excluded
    ]
    removed = 0
    for prefix in prefixes:
        for path in output_dir.glob(f"{prefix}__*.json"):
            path.unlink()
            removed += 1
    return removed


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
    metadata = payload.get("metadata", {})
    output_path = output_dir / f"{eval_id}.json"
    if output_path.exists() and not overwrite:
        try:
            _validate_existing_output(output_path, payload, config)
        except Exception as exc:  # noqa: BLE001 - stale outputs must fail loudly
            return eval_id, False, 0.0, str(exc)
        return eval_id, False, 0.0, None

    last_error: Exception | None = None
    input_hash = input_payload_hash(payload)
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
                "rubric_id": metadata.get("rubric_id") or payload.get("rubric_id"),
                "rubric_version": metadata.get("rubric_version")
                or payload.get("rubric_version"),
                "persona_contract_id": metadata.get("persona_contract_id"),
                "persona_contract_version": metadata.get("persona_contract_version"),
                "input_sha256": input_hash,
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
    require_inputs: bool = False,
    exclude_dimensions: Iterable[str] | None = None,
) -> dict:
    require_openrouter_api_key(purpose="student-sim-stability judge run")

    files = select_inputs(
        input_dir,
        dimension=dimension,
        manifest=manifest,
        limit=limit,
        exclude_dimensions=exclude_dimensions,
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
        if require_inputs:
            stats["failed"] = 1
            stats["failures"].append(
                {
                    "eval_id": None,
                    "error": f"No judge inputs selected for dimension={dimension}",
                }
            )
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


def mirror_primary_outputs(
    primary_output_dir: Path,
    mirror_dir: Path,
    *,
    dimension: str = "all",
    expected_names: set[str] | None = None,
    exclude_dimensions: Iterable[str] | None = None,
) -> int:
    mirror_dir.mkdir(parents=True, exist_ok=True)
    clean_judge_outputs(mirror_dir, dimension, exclude_dimensions=exclude_dimensions)
    excluded = set(exclude_dimensions or [])
    copied = 0
    prefixes = tuple(
        f"{prefix}__"
        for prefix in _prefixes_for_dimension(dimension)
        if prefix not in excluded
    )
    for path in sorted(primary_output_dir.glob("*.json")):
        if expected_names is not None and path.name not in expected_names:
            continue
        if expected_names is None and not path.name.startswith(prefixes):
            continue
        shutil.copy2(path, mirror_dir / path.name)
        copied += 1
    return copied


def run_judge_for_models(
    input_dir: Path,
    primary_output_dir: Path,
    by_model_output_dir: Path,
    *,
    models: list[str] | None = None,
    primary_model: str = JUDGE_MODEL,
    dimension: str = "all",
    manifest: Path | None = None,
    limit: int | None = None,
    workers: int = JUDGE_MAX_WORKERS,
    temperature: float = JUDGE_TEMPERATURE,
    overwrite: bool = False,
    max_retries: int = 3,
    retry_delay: float = 2.0,
    require_inputs: bool = False,
    clean_stale: bool = False,
    model_workers: int | None = None,
    exclude_dimensions: Iterable[str] | None = None,
) -> dict:
    selected_models = list(models or JUDGE_MODELS)
    excluded = set(exclude_dimensions or [])
    expected_names = expected_output_names(
        input_dir,
        dimension,
        manifest,
        limit,
        exclude_dimensions=excluded,
    )
    workers_for_models = max(1, model_workers or len(selected_models) or 1)
    all_stats = {
        "models": selected_models,
        "primary_model": primary_model,
        "model_workers": workers_for_models,
        "per_model_workers": workers,
        "excluded_dimensions": sorted(excluded),
        "by_model_output_dir": str(by_model_output_dir),
        "primary_output_dir": str(primary_output_dir),
        "model_stats": {},
        "failed": 0,
        "cost_usd": 0.0,
    }
    by_model_output_dir.mkdir(parents=True, exist_ok=True)

    def run_one_model(model: str) -> tuple[str, dict]:
        target_dir = (
            primary_output_dir
            if model == primary_model
            else by_model_output_dir / safe_model_dir(model)
        )
        if clean_stale:
            clean_judge_outputs(target_dir, dimension, exclude_dimensions=excluded)
        stats = run_judge(
            input_dir=input_dir,
            output_dir=target_dir,
            dimension=dimension,
            manifest=manifest,
            limit=limit,
            workers=workers,
            model=model,
            temperature=temperature,
            overwrite=overwrite,
            max_retries=max_retries,
            retry_delay=retry_delay,
            require_inputs=require_inputs,
            exclude_dimensions=excluded,
        )
        return model, stats

    with ThreadPoolExecutor(max_workers=workers_for_models) as pool:
        futures = {
            pool.submit(run_one_model, model): model for model in selected_models
        }
        for future in as_completed(futures):
            model = futures[future]
            try:
                model, stats = future.result()
            except (
                Exception
            ) as exc:  # noqa: BLE001 - preserve per-model failure context
                stats = {
                    "selected": 0,
                    "written": 0,
                    "skipped": 0,
                    "failed": 1,
                    "cost_usd": 0.0,
                    "failures": [{"eval_id": None, "error": str(exc)}],
                }
            all_stats["model_stats"][model] = stats
            all_stats["failed"] += stats["failed"]
            all_stats["cost_usd"] += stats["cost_usd"]
    if primary_model in selected_models:
        copied = mirror_primary_outputs(
            primary_output_dir,
            by_model_output_dir / safe_model_dir(primary_model),
            dimension=dimension,
            expected_names=expected_names,
            exclude_dimensions=excluded,
        )
        all_stats["primary_mirrored"] = copied
    return all_stats


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", default=None)
    parser.add_argument("--output-dir", default=None)
    parser.add_argument(
        "--dimension",
        default="all",
        choices=(*DIMENSION_TO_FILE, "all"),
    )
    parser.add_argument("--manifest", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--workers", type=int, default=JUDGE_MAX_WORKERS)
    parser.add_argument("--model", default=JUDGE_MODEL)
    parser.add_argument("--all-models", action="store_true")
    parser.add_argument(
        "--model-workers",
        type=int,
        default=None,
        help="Judge models to run in parallel when --all-models is used",
    )
    parser.add_argument("--by-model-output-dir", default=None)
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
    if args.all_models:
        by_model_output_dir = (
            Path(args.by_model_output_dir)
            if args.by_model_output_dir
            else output_dir.parent / "judge_outputs_by_model"
        )
        stats = run_judge_for_models(
            input_dir=input_dir,
            primary_output_dir=output_dir,
            by_model_output_dir=by_model_output_dir,
            dimension=args.dimension,
            manifest=args.manifest,
            limit=args.limit,
            workers=args.workers,
            temperature=args.temperature,
            overwrite=args.overwrite,
            max_retries=args.max_retries,
            retry_delay=args.retry_delay,
            model_workers=args.model_workers,
            require_inputs=True,
        )
    else:
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
            require_inputs=True,
        )
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    return 1 if stats["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
