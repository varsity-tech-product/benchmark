#!/usr/bin/env python3
"""Task-aware LEAN runtime audit for the pinned benchmark dataset revision.

This script validates both:
1. Direct task contract files (data_files, docs_available, sample_code)
2. Shared LEAN runtime assets: metadata sidecars under /lean/Data and
   12-col custom bars under /data/custom

The output is designed to answer "what is missing?" and
"which tasks are affected?" without relying on ad-hoc log inspection.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
BENCH_ROOT = SCRIPT_DIR.parent
if str(BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(BENCH_ROOT))

from config.benchmark_config import DATASET_REPO_ID, DATASET_REVISION, LEAN_IMAGE

from scripts.data_manager import ensure_data

TASK_ROOT = BENCH_ROOT / "tasks" / "layer2"
REFERENCE_ROOT = BENCH_ROOT / "reference"

_REFERENCE_SOURCE_MAP = {
    "I01": REFERENCE_ROOT / "Implementation" / "algorithms" / "I01_implement_sma.cs",
    "I02": REFERENCE_ROOT / "Implementation" / "algorithms" / "I02_trend_following.cs",
    "I03": REFERENCE_ROOT / "Implementation" / "algorithms" / "I03_mean_reversion.cs",
    "I04": REFERENCE_ROOT / "Implementation" / "algorithms" / "I04_multi_timeframe.cs",
    "I05": REFERENCE_ROOT / "Implementation" / "algorithms" / "I05_cross_asset.cs",
    "I06": REFERENCE_ROOT / "Implementation" / "algorithms" / "I06_multi_signal.cs",
    "I07": REFERENCE_ROOT / "Implementation" / "algorithms" / "I07_alpha_model.cs",
    "I08": REFERENCE_ROOT / "Implementation" / "algorithms" / "I08_multi_alpha.cs",
    "I09": REFERENCE_ROOT / "Implementation" / "algorithms" / "I09_risk_management.cs",
    "I10": REFERENCE_ROOT
    / "Implementation"
    / "algorithms"
    / "I10_parameter_optimization.cs",
    "E02": REFERENCE_ROOT / "end_to_end" / "algorithms" / "E02_bb_reversion.cs",
    "E04": REFERENCE_ROOT / "end_to_end" / "algorithms" / "E04_compound_fixed.cs",
    "E05": REFERENCE_ROOT / "end_to_end" / "algorithms" / "E05_momentum_topn.cs",
}


def _series_key(task_id: str) -> str:
    return task_id.split("_", 1)[0]


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if item not in seen:
            ordered.append(item)
            seen.add(item)
    return ordered


def _load_tasks(task_root: Path = TASK_ROOT) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for path in sorted(task_root.rglob("*.json")):
        task = _load_json(path)
        env = task.get("environment") or {}
        if env.get("sandbox_image") == LEAN_IMAGE:
            task["_task_path"] = str(path)
            tasks.append(task)
    return tasks


def _read_text(path: Path | None) -> str:
    if not path or not path.exists():
        return ""
    return path.read_text()


def _resolve_source_path(
    task: dict[str, Any], user_code_dir: Path | None
) -> Path | None:
    task_id = task["task_id"]
    series = _series_key(task_id)
    if series in _REFERENCE_SOURCE_MAP:
        path = _REFERENCE_SOURCE_MAP[series]
        if path.exists():
            return path

    sample_code = task.get("sample_code")
    if sample_code and user_code_dir:
        relative = sample_code.removeprefix("user_code/").lstrip("/")
        candidate = user_code_dir / relative
        if candidate.exists():
            return candidate

    return None


def _build_runtime_profile(
    task: dict[str, Any],
    universe_order: list[str],
    user_code_dir: Path | None,
) -> dict[str, Any]:
    source_path = _resolve_source_path(task, user_code_dir)
    source_text = _read_text(source_path)
    resolutions = _ordered_unique(
        re.findall(r"Resolution\.(Daily|Hour|Minute|Second)", source_text)
    )
    const_ints = {
        name: int(value)
        for name, value in re.findall(r"private const int (\w+) = (\d+);", source_text)
    }

    uses_universe = "LoadUniverse(" in source_text
    subset_match = re.search(r"\.Take\(\s*([A-Za-z0-9_]+)\s*\)", source_text)
    explicit_symbols = _ordered_unique(
        re.findall(r'AddCrypto(?:Future)?\("([A-Z0-9]+)"', source_text)
    )

    universe_mode = "unknown"
    expected_symbols: list[str] = []
    if uses_universe:
        if subset_match:
            token = subset_match.group(1)
            subset_size = int(token) if token.isdigit() else const_ints.get(token)
            universe_mode = "subset"
            if subset_size is not None:
                expected_symbols = universe_order[:subset_size]
        else:
            universe_mode = "full"
            expected_symbols = list(universe_order)
    elif explicit_symbols:
        universe_mode = "explicit"
        expected_symbols = explicit_symbols

    profile = {
        "source_path": str(source_path) if source_path else None,
        "resolutions": resolutions,
        "universe_mode": universe_mode,
        "expected_symbols": expected_symbols,
        "expected_symbol_count": len(expected_symbols),
        "uses_candidate_pairs": "candidate_pairs" in source_text.lower(),
    }
    return profile


def _resolve_declared_file(file_name: str, search_dirs: list[Path]) -> str | None:
    for search_dir in search_dirs:
        candidate = search_dir / file_name
        if candidate.is_file():
            return str(candidate)
    return None


def _resolve_doc(file_name: str, docs_dir: Path | None) -> str | None:
    if not docs_dir:
        return None
    candidate = docs_dir / file_name
    return str(candidate) if candidate.is_file() else None


def _resolve_sample_code(
    sample_code: str,
    user_code_dir: Path | None,
    data_search_dirs: list[Path],
) -> str | None:
    if not sample_code:
        return None

    if sample_code.startswith("user_code/") and user_code_dir:
        relative = sample_code.removeprefix("user_code/").lstrip("/")
        candidate = user_code_dir / relative
        if candidate.is_file():
            return str(candidate)

    relative_candidates = [
        sample_code,
        sample_code.removeprefix("data/"),
        sample_code.removeprefix("user_code/"),
        Path(sample_code).name,
    ]
    for relative in _ordered_unique([c for c in relative_candidates if c]):
        resolved = _resolve_declared_file(relative, data_search_dirs)
        if resolved is not None:
            return resolved

    return None


def _symbol_sets_for_resolution(custom_data_dir: Path, resolution: str) -> dict[str, Any]:
    root = custom_data_dir / resolution
    payload: dict[str, Any] = {
        "path": str(root),
        "exists": root.exists(),
        "trade_symbols": [],
        "quote_symbols": [],
        "zip_file_count": 0,
    }
    if not root.exists():
        return payload

    payload["trade_symbols"] = sorted(p.name.upper() for p in root.iterdir() if p.is_dir())
    payload["zip_file_count"] = sum(1 for _ in root.rglob("*.zip"))
    return payload


def _task_runtime_issues(
    task: dict[str, Any],
    profile: dict[str, Any],
    resolution_inventory: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    expected_symbols = profile.get("expected_symbols") or []
    if not expected_symbols:
        return issues

    for resolution in profile.get("resolutions") or []:
        resolution_key = resolution.lower()
        inventory = resolution_inventory.get(resolution_key)
        if not inventory:
            continue

        trade_symbols = set(inventory["trade_symbols"])
        missing_trade = sorted(set(expected_symbols) - trade_symbols)
        if missing_trade:
            issues.append(
                {
                    "gap_id": f"{resolution_key}_trade_missing",
                    "kind": "trade",
                    "resolution": resolution_key,
                    "expected_symbol_count": len(expected_symbols),
                    "missing_symbols": missing_trade,
                    "missing_count": len(missing_trade),
                }
            )

    return issues


def audit_lean_data(
    cache_dir: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    paths = (
        ensure_data(series="lean", cache_dir=cache_dir)
        if cache_dir
        else ensure_data(series="lean")
    )

    lean_data_dir = Path(paths.lean_data or "")
    custom_data_dir = (
        Path(paths.custom_data or "") / "binance" if paths.custom_data else Path()
    )
    docs_dir = Path(paths.docs or "") if paths.docs else None
    user_code_dir = Path(paths.user_code or "") if paths.user_code else None
    data_search_dirs = [Path(p) for p in paths.data_search_dirs]
    tasks = _load_tasks()

    universe_resolved = _resolve_declared_file("universe.json", data_search_dirs)
    universe_path = (
        Path(universe_resolved) if universe_resolved else lean_data_dir / "universe.json"
    )
    universe_payload = _load_json(universe_path)
    if isinstance(universe_payload, list):
        universe_order = list(universe_payload)
    else:
        universe_order = []
        for value in universe_payload.values():
            if isinstance(value, list):
                universe_order.extend(value)
            else:
                universe_order.append(value)

    resolution_inventory = {
        "daily": _symbol_sets_for_resolution(custom_data_dir, "daily"),
        "hour": _symbol_sets_for_resolution(custom_data_dir, "hour"),
        "4hour": _symbol_sets_for_resolution(custom_data_dir, "4hour"),
        "minute": _symbol_sets_for_resolution(custom_data_dir, "minute"),
        "5minute": _symbol_sets_for_resolution(custom_data_dir, "5minute"),
    }

    direct_missing: dict[str, list[dict[str, Any]]] = {
        "data_files": [],
        "docs": [],
        "sample_code": [],
    }
    gap_aggregate: dict[str, dict[str, Any]] = {}
    task_impacts: dict[str, dict[str, Any]] = {}

    for task in tasks:
        task_id = task["task_id"]
        env = task.get("environment") or {}
        profile = _build_runtime_profile(task, universe_order, user_code_dir)

        declared = {
            "data_files": [],
            "docs_available": [],
            "sample_code": [],
        }

        for file_name in env.get("data_files", []):
            resolved = _resolve_declared_file(file_name, data_search_dirs)
            item = {"name": file_name, "resolved_path": resolved}
            declared["data_files"].append(item)
            if resolved is None:
                direct_missing["data_files"].append(
                    {
                        "task_id": task_id,
                        "name": file_name,
                        "search_dirs": [str(p) for p in data_search_dirs],
                    }
                )

        for doc_name in env.get("docs_available", []):
            resolved = _resolve_doc(doc_name, docs_dir)
            item = {"name": doc_name, "resolved_path": resolved}
            declared["docs_available"].append(item)
            if resolved is None:
                direct_missing["docs"].append(
                    {
                        "task_id": task_id,
                        "name": doc_name,
                        "docs_dir": str(docs_dir) if docs_dir else None,
                    }
                )

        sample_code = task.get("sample_code")
        if sample_code:
            resolved = _resolve_sample_code(
                sample_code, user_code_dir, data_search_dirs
            )
            item = {"name": sample_code, "resolved_path": resolved}
            declared["sample_code"].append(item)
            if resolved is None:
                direct_missing["sample_code"].append(
                    {
                        "task_id": task_id,
                        "name": sample_code,
                        "user_code_dir": (
                            str(user_code_dir) if user_code_dir else None
                        ),
                    }
                )

        runtime_issues = _task_runtime_issues(task, profile, resolution_inventory)
        for issue in runtime_issues:
            gap = gap_aggregate.setdefault(
                issue["gap_id"],
                {
                    "gap_id": issue["gap_id"],
                    "kind": issue["kind"],
                    "resolution": issue.get("resolution"),
                    "missing_count": issue["missing_count"],
                    "missing_symbols": issue.get("missing_symbols", []),
                    "impacted_tasks": [],
                    "details": issue.get("details"),
                },
            )
            gap["impacted_tasks"].append(task_id)

        task_impacts[task_id] = {
            "task_path": task["_task_path"],
            "category": task["category"],
            "declared_contract": declared,
            "runtime_profile": profile,
            "direct_missing": {
                "data_files": [
                    item["name"]
                    for item in declared["data_files"]
                    if item["resolved_path"] is None
                ],
                "docs": [
                    item["name"]
                    for item in declared["docs_available"]
                    if item["resolved_path"] is None
                ],
                "sample_code": [
                    item["name"]
                    for item in declared["sample_code"]
                    if item["resolved_path"] is None
                ],
            },
            "runtime_issues": runtime_issues,
        }

    sidecars = {
        "universe_json": str(universe_path) if universe_path.exists() else None,
        "i05_candidate_pairs": _resolve_declared_file(
            "I05_candidate_pairs.json", data_search_dirs
        ),
        "market_hours_database": (
            str(lean_data_dir / "market-hours" / "market-hours-database.json")
            if (lean_data_dir / "market-hours" / "market-hours-database.json").exists()
            else None
        ),
        "symbol_properties_database": (
            str(lean_data_dir / "symbol-properties" / "symbol-properties-database.csv")
            if (
                lean_data_dir / "symbol-properties" / "symbol-properties-database.csv"
            ).exists()
            else None
        ),
        "security_database": (
            str(lean_data_dir / "symbol-properties" / "security-database.csv")
            if (lean_data_dir / "symbol-properties" / "security-database.csv").exists()
            else None
        ),
        "quote_policy": "de_scoped",
        "margin_interest_policy": "de_scoped",
        "margin_interest_family_present": False,
        "daily_quote_symbol_count": len(resolution_inventory["daily"]["quote_symbols"]),
        "hour_quote_symbol_count": len(resolution_inventory["hour"]["quote_symbols"]),
    }

    for gap in gap_aggregate.values():
        gap["impacted_tasks"] = sorted(gap["impacted_tasks"])

    result = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "dataset_repo_id": DATASET_REPO_ID,
        "dataset_revision": DATASET_REVISION,
        "lean_image": LEAN_IMAGE,
        "cache_layout": {
            "lean_metadata_dir": str(lean_data_dir),
            "custom_data_dir": str(custom_data_dir),
            "docs_dir": str(docs_dir) if docs_dir else None,
            "user_code_dir": str(user_code_dir) if user_code_dir else None,
            "data_search_dirs": [str(p) for p in data_search_dirs],
        },
        "summary": {
            "lean_task_count": len(tasks),
            "direct_missing_count": sum(len(v) for v in direct_missing.values()),
            "shared_runtime_gap_count": len(gap_aggregate),
            "universe_symbol_count": len(universe_order),
        },
        "direct_missing_files": direct_missing,
        "shared_runtime_gaps": sorted(
            gap_aggregate.values(), key=lambda item: item["gap_id"]
        ),
        "resolution_inventory": {
            name: {
                **payload,
                "trade_symbol_count": len(payload["trade_symbols"]),
                "quote_symbol_count": len(payload["quote_symbols"]),
            }
            for name, payload in resolution_inventory.items()
        },
        "sidecars": sidecars,
        "task_impacts": task_impacts,
    }

    if output_path:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(result, indent=2))

    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Override HF cache dir (default: bench/data/hf_cache).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional JSON output path.",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    result = audit_lean_data(cache_dir=args.cache_dir, output_path=args.output)
    print(
        json.dumps(
            {
                "lean_task_count": result["summary"]["lean_task_count"],
                "direct_missing_count": result["summary"]["direct_missing_count"],
                "shared_runtime_gap_count": result["summary"][
                    "shared_runtime_gap_count"
                ],
                "daily_trade_symbols": result["resolution_inventory"]["daily"][
                    "trade_symbol_count"
                ],
                "daily_quote_symbols": result["resolution_inventory"]["daily"][
                    "quote_symbol_count"
                ],
                "hour_trade_symbols": result["resolution_inventory"]["hour"][
                    "trade_symbol_count"
                ],
            },
            indent=2,
        )
    )
    if args.output:
        print(f"Wrote audit report to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
