"""Run and summarize the Impl A baseline matrix for issue #185.

The driver is intentionally thin: it uses the production HTTP run API to
create sessions, delegates the agent loop to ``client.runner``, exports Bundle
v1 alpha artifacts from completed server results, and writes summary tables.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

_BENCH_ROOT = Path(__file__).resolve().parents[1]
if str(_BENCH_ROOT) not in sys.path:
    sys.path.insert(0, str(_BENCH_ROOT))


BASELINE_VERSION = "baseline_run_v1"
DEFAULT_OUTPUT_DIR = _BENCH_ROOT / "data" / BASELINE_VERSION
DEFAULT_LAYERS = ("L0", "L1", "L2")
HTTP_SESSION_LAYERS = ("L2",)


AGENT_PROFILES = {
    "claude_haiku_4_5": {
        "adapter": "anthropic",
        "model": "claude-haiku-4-5",
        "label": "Claude Haiku 4.5",
    },
    "claude_sonnet_4_6": {
        "adapter": "anthropic",
        "model": "claude-sonnet-4-6",
        "label": "Claude Sonnet 4.6",
    },
}

CONDITION_PROFILES = {
    "agent": {
        "label": "Tool-using tutor agent",
        "system_prompt": "",
    },
    "direct_answer_baseline": {
        "label": "Direct-answer baseline",
        "system_prompt": (
            "You are a quantitative finance assistant. Solve the task directly, "
            "use tools only when computation or file inspection is necessary, "
            "and keep tutoring dialogue brief."
        ),
    },
}


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    layer: str
    category: str
    difficulty: str
    persona_id: str
    source_path: str


@dataclass(frozen=True)
class MatrixCell:
    cell_id: str
    task: TaskSpec
    agent_id: str
    condition_id: str
    http_runnable: bool

    def to_record(self) -> dict[str, Any]:
        agent = AGENT_PROFILES[self.agent_id]
        condition = CONDITION_PROFILES[self.condition_id]
        return {
            "cell_id": self.cell_id,
            "task_id": self.task.task_id,
            "layer": self.task.layer,
            "category": self.task.category,
            "difficulty": self.task.difficulty,
            "persona_id": self.task.persona_id,
            "agent_id": self.agent_id,
            "agent_label": agent["label"],
            "agent_model": agent["model"],
            "condition": self.condition_id,
            "condition_label": condition["label"],
            "http_runnable": self.http_runnable,
        }


def discover_tasks(
    bench_root: Path = _BENCH_ROOT,
    *,
    layers: tuple[str, ...] = DEFAULT_LAYERS,
) -> list[TaskSpec]:
    tasks: list[TaskSpec] = []
    for layer in layers:
        layer_dir = bench_root / "tasks" / layer
        if not layer_dir.is_dir():
            continue
        for path in sorted(layer_dir.rglob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            task_id = str(data.get("task_id") or path.stem)
            if not task_id.startswith(f"{layer}_"):
                continue
            tasks.append(
                TaskSpec(
                    task_id=task_id,
                    layer=str(data.get("layer") or layer),
                    category=str(data.get("category") or path.parent.name),
                    difficulty=str(data.get("difficulty") or ""),
                    persona_id=str(data.get("persona_id") or ""),
                    source_path=path.relative_to(bench_root).as_posix(),
                )
            )
    return tasks


def build_matrix(
    tasks: list[TaskSpec],
    *,
    agent_ids: tuple[str, ...] = tuple(AGENT_PROFILES),
    condition_ids: tuple[str, ...] = tuple(CONDITION_PROFILES),
) -> list[MatrixCell]:
    cells: list[MatrixCell] = []
    for task in tasks:
        for agent_id in agent_ids:
            for condition_id in condition_ids:
                cell_id = "__".join((task.task_id, agent_id, condition_id))
                cells.append(
                    MatrixCell(
                        cell_id=cell_id,
                        task=task,
                        agent_id=agent_id,
                        condition_id=condition_id,
                        http_runnable=task.layer in HTTP_SESSION_LAYERS
                        and bool(task.persona_id),
                    )
                )
    return cells


def load_run_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")


def summarize_records(
    records: list[dict[str, Any]],
    matrix: list[MatrixCell],
) -> dict[str, Any]:
    by_cell = {str(record.get("cell_id")): record for record in records}
    completed = [
        record
        for record in by_cell.values()
        if str(record.get("status")) == "completed"
        and _score_value(record.get("score")) is not None
    ]
    runnable = [cell for cell in matrix if cell.http_runnable]
    return {
        "baseline_version": BASELINE_VERSION,
        "generated_at": utc_now(),
        "planned_cells": len(matrix),
        "http_runnable_cells": len(runnable),
        "completed_cells": len(completed),
        "coverage": {
            "planned_task_count": len({cell.task.task_id for cell in matrix}),
            "http_runnable_task_count": len({cell.task.task_id for cell in runnable}),
            "completed_task_count": len(
                {str(record.get("task_id")) for record in completed}
            ),
        },
        "agents": AGENT_PROFILES,
        "conditions": CONDITION_PROFILES,
        "storage_policy": {
            "bundle_json": "bench/data/baseline_run_v1/bundles/ is generated and gitignored",
            "summary_json": "bench/data/baseline_run_v1/summary.json is tracked",
            "runs_jsonl": "bench/data/baseline_run_v1/runs.jsonl is generated and gitignored",
        },
        "rationale": (
            "The full v3 corpus has 142 tasks. The current production HTTP run "
            "catalog exposes the 19 multi-turn L2 tasks, so the first executable "
            "HTTP slice is the L2 subset. L0/L1 coverage is planned after those "
            "single-turn tasks have run-token catalog support."
        ),
        "by_layer": aggregate(completed, ("layer",)),
        "by_category": aggregate(completed, ("layer", "category")),
        "by_agent": aggregate(completed, ("agent_id",)),
        "by_condition": aggregate(completed, ("condition",)),
        "by_agent_condition": aggregate(completed, ("agent_id", "condition")),
    }


def aggregate(
    records: list[dict[str, Any]],
    keys: tuple[str, ...],
) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for record in records:
        key = tuple(str(record.get(item) or "") for item in keys)
        groups.setdefault(key, []).append(record)

    rows: list[dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        scores = [
            score
            for item in items
            if (score := _score_value(item.get("score"))) is not None
        ]
        passes = [
            task_pass
            for item in items
            if isinstance(item.get("score"), dict)
            and isinstance((task_pass := item["score"].get("task_pass")), bool)
        ]
        row = {name: value for name, value in zip(keys, key)}
        row.update(
            {
                "n": len(items),
                "pass_rate": round(sum(passes) / len(passes), 4) if passes else None,
                "mean": round(sum(scores) / len(scores), 4) if scores else None,
                "median": round(statistics.median(scores), 4) if scores else None,
            }
        )
        rows.append(row)
    return rows


def write_outputs(
    output_dir: Path,
    summary: dict[str, Any],
    *,
    docs_dir: Path = _BENCH_ROOT.parent / "docs",
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    write_summary_doc(summary, docs_dir / "baseline_run_v1_summary.md")


def write_manifest(output_dir: Path, matrix: list[MatrixCell]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "baseline_version": BASELINE_VERSION,
        "generated_at": utc_now(),
        "cells": [cell.to_record() for cell in matrix],
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def write_summary_doc(summary: dict[str, Any], path: Path) -> None:
    lines = [
        "# Baseline Run v1 Summary",
        "",
        f"Generated: `{summary.get('generated_at', '')}`",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Planned matrix cells | {summary.get('planned_cells', 0)} |",
        f"| HTTP-runnable cells | {summary.get('http_runnable_cells', 0)} |",
        f"| Completed scored cells | {summary.get('completed_cells', 0)} |",
        "",
        "## Agent x Condition",
        "",
        _markdown_table(
            summary.get("by_agent_condition", []),
            ("agent_id", "condition", "n", "pass_rate", "mean", "median"),
        ),
        "",
        "## Category",
        "",
        _markdown_table(
            summary.get("by_category", []),
            ("layer", "category", "n", "pass_rate", "mean", "median"),
        ),
        "",
        "## Coverage Note",
        "",
        str(summary.get("rationale") or ""),
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


async def run_cells(
    args: argparse.Namespace,
    cells: list[MatrixCell],
) -> list[dict[str, Any]]:
    records_path = args.output_dir / "runs.jsonl"
    existing = {
        str(record.get("cell_id")): record
        for record in load_run_records(records_path)
        if str(record.get("status")) == "completed"
    }
    selected = [
        cell
        for cell in cells
        if cell.http_runnable and (args.force or cell.cell_id not in existing)
    ]
    if args.limit:
        selected = selected[: args.limit]
    if args.dry_run:
        return [cell.to_record() | {"status": "dry_run"} for cell in selected]

    semaphore = asyncio.Semaphore(args.workers)
    write_lock = asyncio.Lock()

    async def _one(cell: MatrixCell) -> dict[str, Any]:
        async with semaphore:
            record = await run_one_cell(args, cell)
            async with write_lock:
                append_jsonl(records_path, record)
            return record

    return list(await asyncio.gather(*[_one(cell) for cell in selected]))


async def run_one_cell(args: argparse.Namespace, cell: MatrixCell) -> dict[str, Any]:
    started_at = utc_now()
    base_record = cell.to_record() | {
        "baseline_version": BASELINE_VERSION,
        "started_at": started_at,
    }
    try:
        run_data = await create_run(args, cell)
        token = run_data["token"]
        result = await execute_agent(args, cell, token)
        session_id = str(result.get("session_id") or "")
        score = await poll_score(args.server, session_id, token, args.score_timeout)
        bundle_path = export_bundle(args, session_id, cell)
        return base_record | {
            "status": cell_status(session_id, result, score),
            "run_id": run_data.get("run_id", ""),
            "session_id": session_id,
            "duration_seconds": result.get("duration_seconds"),
            "agent_cost": result.get("agent_cost") or {},
            "score": score,
            "bundle_path": bundle_path,
            "completed_at": utc_now(),
            "error": result.get("error", ""),
        }
    except Exception as exc:  # noqa: BLE001
        return base_record | {
            "status": "failed",
            "completed_at": utc_now(),
            "error": str(exc),
        }


async def create_run(args: argparse.Namespace, cell: MatrixCell) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {args.api_key}"} if args.api_key else {}
    payload = {
        "task": cell.task.task_id,
        "client": {
            "name": "baseline_run",
            "version": BASELINE_VERSION,
            "cell_id": cell.cell_id,
            "agent_id": cell.agent_id,
            "condition": cell.condition_id,
        },
    }
    async with httpx.AsyncClient(base_url=args.server.rstrip("/"), timeout=30.0) as http:
        response = await http.post("/client/runs/start", json=payload, headers=headers)
    if response.status_code != 200:
        raise RuntimeError(f"run create failed for {cell.cell_id}: {response.text}")
    return response.json()


async def execute_agent(
    args: argparse.Namespace,
    cell: MatrixCell,
    token: str,
) -> dict[str, Any]:
    from client.runner import run_via_attach

    def factory():
        from client.adapters.anthropic_adapter import ClaudeAgentAdapter

        agent = AGENT_PROFILES[cell.agent_id]
        condition = CONDITION_PROFILES[cell.condition_id]
        return ClaudeAgentAdapter(
            model=str(agent["model"]),
            system_prompt=str(condition.get("system_prompt") or ""),
            agent_name=cell.agent_id,
        )

    return await run_via_attach(
        server_base_url=args.server,
        token=token,
        adapter_factory=factory,
        result_dir=args.output_dir / "client_traces",
        protocol=args.protocol,
    )


async def poll_score(
    server: str,
    session_id: str,
    token: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    if not session_id:
        return {}
    headers = {"Authorization": f"Bearer {token}"}
    deadline = time.monotonic() + timeout_seconds
    async with httpx.AsyncClient(base_url=server.rstrip("/"), timeout=30.0) as http:
        while True:
            response = await http.get(
                f"/session/{session_id}/scores?history=true",
                headers=headers,
            )
            payload = response.json() if response.content else {}
            score = _latest_public_score(payload)
            status = str(score.get("score_status") or score.get("status") or "")
            if status.startswith("completed") or status in {"failed", "interrupted"}:
                return score
            if time.monotonic() >= deadline:
                return {"status": "timeout", "payload": payload}
            await asyncio.sleep(5.0)


def export_bundle(args: argparse.Namespace, session_id: str, cell: MatrixCell) -> str:
    if not session_id:
        return ""
    result_dir = find_result_dir(args.server_results_root, session_id)
    if result_dir is None:
        return ""

    from eval.backfill.run_state_to_bundle import backfill
    from eval.contracts.bundle_schema import validate_bundle_path

    out = args.output_dir / "bundles" / cell.cell_id / "bundle.json"
    backfill(result_dir / "run_state.json", bench_root=args.bench_root, output=out)
    validate_bundle_path(out)
    return out.as_posix()


def find_result_dir(results_root: Path, session_id: str) -> Path | None:
    if not session_id or not results_root.is_dir():
        return None
    for marker in results_root.rglob(".session_id"):
        try:
            if marker.read_text(encoding="utf-8").strip() == session_id:
                return marker.parent
        except OSError:
            continue
    for state_path in results_root.rglob("run_state.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(state.get("session_id") or "") == session_id:
            return state_path.parent
    return None


def validate_bundles(output_dir: Path) -> int:
    from eval.contracts.bundle_schema import validate_bundle_path

    failures = 0
    for path in sorted((output_dir / "bundles").rglob("bundle.json")):
        try:
            validate_bundle_path(path)
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"{path}: invalid: {exc}", file=sys.stderr)
        else:
            print(f"{path}: valid")
    return failures


def _latest_public_score(payload: dict[str, Any]) -> dict[str, Any]:
    scores = payload.get("scores")
    if isinstance(scores, list) and scores:
        latest = scores[-1]
        return latest if isinstance(latest, dict) else {}
    if isinstance(scores, dict):
        return scores
    return payload if isinstance(payload, dict) else {}


def _score_value(score: Any) -> float | None:
    if not isinstance(score, dict):
        return None
    for key in ("task_score", "overall_score", "overall"):
        raw = score.get(key)
        if raw is None:
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return None


def _score_status(score: Any) -> str:
    if not isinstance(score, dict):
        return ""
    return str(score.get("score_status") or score.get("status") or "")


def score_is_completed(score: Any) -> bool:
    return _score_status(score).startswith("completed") and _score_value(score) is not None


def cell_status(session_id: str, result: dict[str, Any], score: dict[str, Any]) -> str:
    if not session_id or result.get("error"):
        return "failed"
    if score_is_completed(score):
        return "completed"
    status = _score_status(score)
    if status == "timeout":
        return "score_timeout"
    if status in {"failed", "interrupted"}:
        return "score_failed"
    return "score_pending"


def _markdown_table(rows: list[dict[str, Any]], columns: tuple[str, ...]) -> str:
    if not rows:
        return "_Pending baseline execution._"
    lines = [
        "| " + " | ".join(columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(str(row.get(col, "")) for col in columns) + " |")
    return "\n".join(lines)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_csv(value: str, allowed: set[str]) -> tuple[str, ...]:
    raw = tuple(item.strip() for item in value.split(",") if item.strip())
    unknown = [item for item in raw if item not in allowed]
    if unknown:
        raise argparse.ArgumentTypeError(f"unknown value(s): {', '.join(unknown)}")
    return raw


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bench-root", type=Path, default=_BENCH_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    sub = parser.add_subparsers(dest="command", required=True)

    def add_matrix_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--layers", default=",".join(DEFAULT_LAYERS))
        p.add_argument("--agents", default=",".join(AGENT_PROFILES))
        p.add_argument("--conditions", default=",".join(CONDITION_PROFILES))

    plan = sub.add_parser("plan", help="Write manifest and pending summary")
    add_matrix_args(plan)

    run = sub.add_parser("run", help="Execute HTTP-runnable matrix cells")
    add_matrix_args(run)
    run.add_argument(
        "--server",
        default=os.environ.get("QTB_BASELINE_SERVER", "http://127.0.0.1:8000"),
    )
    run.add_argument("--api-key", default=os.environ.get("QTB_CLIENT_API_KEY", ""))
    run.add_argument("--protocol", choices=("mcp", "rest"), default="mcp")
    run.add_argument("--workers", type=positive_int, default=1)
    run.add_argument("--limit", type=int, default=0)
    run.add_argument("--score-timeout", type=float, default=1800.0)
    run.add_argument(
        "--server-results-root",
        type=Path,
        default=_BENCH_ROOT / "results" / "server",
    )
    run.add_argument("--force", action="store_true")
    run.add_argument("--dry-run", action="store_true")

    summary = sub.add_parser("summarize", help="Regenerate summary from runs.jsonl")
    add_matrix_args(summary)

    sub.add_parser("validate", help="Validate exported bundle.json files")
    return parser


def matrix_from_args(args: argparse.Namespace) -> list[MatrixCell]:
    layers = parse_csv(args.layers, set(DEFAULT_LAYERS))
    agent_ids = parse_csv(args.agents, set(AGENT_PROFILES))
    condition_ids = parse_csv(args.conditions, set(CONDITION_PROFILES))
    return build_matrix(
        discover_tasks(args.bench_root, layers=layers),
        agent_ids=agent_ids,
        condition_ids=condition_ids,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.bench_root = args.bench_root.resolve()
    args.output_dir = args.output_dir.resolve()

    if args.command == "validate":
        return 1 if validate_bundles(args.output_dir) else 0

    matrix = matrix_from_args(args)
    write_manifest(args.output_dir, matrix)

    if args.command == "run":
        asyncio.run(run_cells(args, matrix))

    records = load_run_records(args.output_dir / "runs.jsonl")
    summary = summarize_records(records, matrix)
    write_outputs(args.output_dir, summary, docs_dir=args.bench_root.parent / "docs")
    print(json.dumps(summary["coverage"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
