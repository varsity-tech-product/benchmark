"""Refresh static user-sim-stability artifacts inside a result directory."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from experiments.user_sim_stability.core.config import (
    JUDGE_MODEL,
    JUDGE_MODELS,
    JUDGE_TEMPERATURE,
    USER_MODELS,
    TUTOR_MODEL,
    TUTOR_TEMPERATURES,
    compute_trial_count,
)
from experiments.user_sim_stability.core.io_utils import (
    atomic_write_json,
    sha256_directory,
)
from experiments.user_sim_stability.core.paths import (
    BENCH_ROOT,
    RESOURCE_ROOT,
    default_results_dir,
)
from server.config.pricing import resolve_pricing


def _copytree_refresh(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)


def _provider_from_model(model: str) -> str:
    if model.startswith("openai/"):
        return "OpenAI"
    if model.startswith("anthropic/"):
        return "Anthropic"
    if model.startswith("google/"):
        return "Google"
    return model.split("/", 1)[0] if "/" in model else "unknown"


def _pricing_metadata(model: str) -> dict:
    pricing = resolve_pricing(model)
    if not pricing:
        return {
            "pricing_status": "missing_from_local_pricing_table",
            "input_price_usd_per_million_tokens": None,
            "output_price_usd_per_million_tokens": None,
        }
    return {
        "pricing_status": "from_bench_server_config_pricing",
        "input_price_usd_per_million_tokens": pricing[0] * 1_000_000,
        "output_price_usd_per_million_tokens": pricing[1] * 1_000_000,
    }


def _price_band(pricing: dict) -> str:
    if pricing["pricing_status"] != "from_bench_server_config_pricing":
        return "unknown"
    blended = (
        pricing["input_price_usd_per_million_tokens"]
        + pricing["output_price_usd_per_million_tokens"]
    ) / 2
    if blended < 1:
        return "low"
    if blended < 8:
        return "medium"
    return "high"


def _capability_tier(model: str, roles: set[str]) -> str:
    if "user_simulator_candidate" in roles or "judge_panel_member" in roles:
        return "frontier_or_flagship_candidate"
    if "live_tutor_stimulus" in roles:
        return "cost_controlled_live_tutor"
    return "supporting_model"


def _model_source_url(model: str) -> str:
    return f"https://openrouter.ai/{model}"


def _write_runtime_tutor_contract(contracts_snapshot: Path) -> None:
    path = contracts_snapshot / "tutor_contract.json"
    if not path.exists():
        return
    with open(path, encoding="utf-8") as fh:
        contract = json.load(fh)
    contract["model"] = TUTOR_MODEL
    contract["model_config_key"] = (
        "server.config.llm_config.STUDENT_SIM_STABILITY_TUTOR_MODEL"
    )
    contract["temperature_conditions"] = TUTOR_TEMPERATURES
    atomic_write_json(path, contract)


def _build_model_metadata() -> dict:
    role_by_model: dict[str, set[str]] = {}
    for model in USER_MODELS:
        role_by_model.setdefault(model, set()).add("user_simulator_candidate")
    role_by_model.setdefault(TUTOR_MODEL, set()).add("live_tutor_stimulus")
    for model in JUDGE_MODELS:
        role_by_model.setdefault(model, set()).add("judge_panel_member")
    role_by_model.setdefault(JUDGE_MODEL, set()).add("primary_judge")

    models = []
    for model, roles in sorted(role_by_model.items()):
        pricing = _pricing_metadata(model)
        models.append(
            {
                "model": model,
                "model_name": model.split("/")[-1],
                "provider": _provider_from_model(model),
                "roles": sorted(roles),
                "context_length_tokens": None,
                "context_length_status": "unavailable_in_local_repository",
                "price_band": _price_band(pricing),
                "capability_tier": _capability_tier(model, roles),
                "published_parameter_count": None,
                "active_parameter_count": None,
                "parameter_count_status": "not_disclosed_or_not_used",
                "model_card_or_source_url": _model_source_url(model),
                "metadata_status": "explicitly_unavailable_or_locally_sourced",
                "comparison_label": (
                    "cross-vendor candidate selection"
                    if "user_simulator_candidate" in roles
                    else "not_ranked_with_user_models"
                ),
                **pricing,
            }
        )

    return {
        "comparison_policy": "cross-vendor candidate selection",
        "last_updated": "2026-04-23",
        "pricing_source": "bench/server/config/pricing.py",
        "notes": (
            "Closed-model parameter counts are not inferred. Local pricing is "
            "recorded only when present in the repository pricing table."
        ),
        "models": models,
    }


def snapshot_static_artifacts(results_dir: Path | None = None) -> dict:
    out = results_dir or default_results_dir()
    if not out.is_absolute():
        out = BENCH_ROOT / out
    out.mkdir(parents=True, exist_ok=True)

    snapshots = {
        "contracts": out / "contracts_snapshot",
        "rubrics": out / "rubrics_snapshot",
        "policies": out / "policies_snapshot",
    }
    for source_name, target in snapshots.items():
        _copytree_refresh(RESOURCE_ROOT / source_name, target)
    _write_runtime_tutor_contract(snapshots["contracts"])

    model_metadata_dst = out / "report" / "model_metadata.json"
    model_metadata_dst.parent.mkdir(parents=True, exist_ok=True)
    static_manifest_path = out / "report" / "static_artifact_manifest.json"
    static_manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "snapshots": {
            "contracts": sha256_directory(snapshots["contracts"]),
            "rubrics": sha256_directory(snapshots["rubrics"]),
            "policies": sha256_directory(snapshots["policies"]),
        },
    }
    atomic_write_json(static_manifest_path, static_manifest)
    atomic_write_json(model_metadata_dst, _build_model_metadata())
    judge_agreement_path = out / "report" / "judge_agreement.json"
    atomic_write_json(
        judge_agreement_path,
        {
            "primary_judge": JUDGE_MODEL,
            "judge_models": JUDGE_MODELS,
            "temperature": JUDGE_TEMPERATURE,
            "multi_judge_status": "not_run",
            "agreement_metrics": None,
            "notes": "Reset by static snapshot; recompute after current judge outputs are complete.",
        },
    )
    human_alignment_dir = out / "human_alignment"
    human_alignment_dir.mkdir(parents=True, exist_ok=True)
    human_status_path = human_alignment_dir / "agreement_report.json"
    if not human_status_path.exists():
        atomic_write_json(
            human_status_path,
            {
                "human_alignment_status": "not_run",
                "agreement_metrics": None,
                "disagreement_examples": [],
            },
        )

    metadata = {
        "run_contract": "user_sim_validation",
        "user_models": USER_MODELS,
        "tutor_model": TUTOR_MODEL,
        "tutor_temperatures": TUTOR_TEMPERATURES,
        "primary_judge": JUDGE_MODEL,
        "judge_models": JUDGE_MODELS,
        "judge_temperature": JUDGE_TEMPERATURE,
        "multi_judge_status": "not_run",
        "human_alignment_status": "not_run",
        "model_comparison_label": "cross-vendor candidate selection",
        "trial_counts": compute_trial_count(),
        "snapshots": {key: str(path) for key, path in snapshots.items()},
        "static_artifact_manifest": str(static_manifest_path),
        "model_metadata": str(model_metadata_dst),
    }
    report_dir = out / "report"
    report_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(report_dir / "stability_metadata.json", metadata)
    return metadata
