"""Convert legacy ``run_state.json`` to ``bundle.json`` v1.

The on-disk pre-v1 layout is one ``run_state.json`` per session under
``bench/results/server/{task}/{persona}/{ts}_{sid}/``. This script reads
that file plus its sibling ``agent_files/`` workspace and writes a
``bundle.json`` next to it conforming to ``eval.contracts.bundle``.

Usage:

    python -m eval.backfill.run_state_to_bundle <run_state.json>
    python -m eval.backfill.run_state_to_bundle <result_dir>
    python -m eval.backfill.run_state_to_bundle --recursive <results_root>

Existing bundle.json files are skipped unless --force is passed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval.contracts import bundle_io
from eval.contracts.bundle import (
    BENCH_EVAL_VERSION,
    REFERENCE_ARTIFACT_KEY,
    SCHEMA_VERSION,
    Bundle,
    BundleTimestamps,
    Message,
    ToolCall,
    WorkspaceFile,
    WorkspaceSnapshot,
)

logger = logging.getLogger(__name__)

_BENCH_ROOT_DEFAULT = Path(__file__).resolve().parents[2]
_NPC_TOOL_NAME = "send_message"
_ACTIVE_TASK_LAYERS = ("L0", "L1", "L2")
_ACTIVE_TASK_PREFIXES = tuple(f"{layer}_" for layer in _ACTIVE_TASK_LAYERS)


def backfill(
    run_state_path: Path,
    *,
    bench_root: Path | None = None,
    output: Path | None = None,
) -> Path:
    """Read one run_state.json and write a bundle.json next to it."""
    bench_root = bench_root or _BENCH_ROOT_DEFAULT
    state = json.loads(run_state_path.read_text(encoding="utf-8"))

    task_json_path = _find_task_json(bench_root, state.get("task_id", ""))
    task_json: dict = {}
    if task_json_path:
        task_json = json.loads(task_json_path.read_text(encoding="utf-8"))

    messages = _build_messages(state)
    bundle = Bundle(
        bundle_id=_bundle_id(
            state,
            run_state_path=run_state_path,
            bench_root=bench_root,
        ),
        schema_version=SCHEMA_VERSION,
        task_id=str(state.get("task_id", "")),
        timestamps=_build_timestamps(state),
        agent_id=_agent_id(state),
        sandbox_digest=_build_sandbox_digest(task_json, state),
        telemetry=_build_telemetry(state, messages=messages),
        messages=messages,
        tool_calls=_build_tool_calls(state),
        artifacts=_build_artifacts(
            state,
            task_json=task_json,
            task_spec_hash=_hash_task_json(task_json) if task_json else "",
        ),
        workspace=WorkspaceSnapshot(
            root="agent_files",
            files=_build_workspace_manifest(run_state_path.parent / "agent_files"),
        ),
    )

    out = output or run_state_path.parent / "bundle.json"
    bundle_io.write(bundle, out)
    return out


def _find_task_json(bench_root: Path, task_id: str) -> Path | None:
    if not task_id:
        return None
    if not task_id.startswith(_ACTIVE_TASK_PREFIXES):
        return None
    for layer in _ACTIVE_TASK_LAYERS:
        for path in (bench_root / "tasks" / layer).rglob(f"{task_id}.json"):
            return path
    return None


def _hash_task_json(task_json: dict) -> str:
    canonical = json.dumps(
        task_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bundle_id(state: dict, *, run_state_path: Path, bench_root: Path) -> str:
    for key in ("session_id", "run_id"):
        value = str(state.get(key) or "").strip()
        if value:
            return value

    source_path = _stable_source_path(run_state_path, bench_root=bench_root)
    canonical = json.dumps(
        {
            "task_id": state.get("task_id"),
            "persona_id": state.get("persona_id"),
            "source_path": source_path,
            "run_state": state,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:12]
    task_id = str(state.get("task_id") or "bundle")
    slug = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in task_id)
    return f"legacy-{slug}-{digest}"


def _stable_source_path(run_state_path: Path, *, bench_root: Path) -> str:
    try:
        return run_state_path.parent.relative_to(bench_root.parent).as_posix()
    except ValueError:
        return run_state_path.parent.as_posix()


def _build_timestamps(state: dict) -> BundleTimestamps:
    conv = state.get("conversation") or []
    start_ts = ""
    end_ts = ""
    if conv:
        start_ts = _coerce_iso(_get(conv[0], "ts"))
        end_ts = _coerce_iso(_get(conv[-1], "ts"))
    if not end_ts:
        end_ts = str(state.get("timestamp") or "")

    return BundleTimestamps(
        created_at=str(state.get("timestamp") or start_ts or end_ts),
        started_at=start_ts,
        completed_at=end_ts,
        duration_seconds=_float_or_none(state.get("duration_seconds")),
    )


def _agent_id(state: dict) -> str:
    agent_cost = _get(state, "agent_cost") or {}
    return str(
        state.get("agent_id")
        or _get(agent_cost, "model")
        or state.get("agent")
        or state.get("run_id")
        or "ref_harness"
    )


def _image_digest(image_uri: str) -> str:
    marker = "@sha256:"
    if marker in image_uri:
        return "sha256:" + image_uri.split(marker, 1)[1]
    return ""


def _build_sandbox_digest(task_json: dict, state: dict | None = None) -> dict[str, Any]:
    state_digest = state.get("sandbox_digest") if isinstance(state, dict) else None
    if isinstance(state_digest, dict) and state_digest:
        return dict(state_digest)

    environment = task_json.get("environment") if isinstance(task_json, dict) else {}
    if not isinstance(environment, dict):
        environment = {}
    sandbox_spec = environment.get("sandbox_spec")
    if not isinstance(sandbox_spec, dict):
        sandbox_spec = {}
    image_uri = str(
        sandbox_spec.get("image_uri") or environment.get("sandbox_image") or ""
    )
    resource_limits = sandbox_spec.get("resource_limits")
    if not isinstance(resource_limits, dict):
        resource_limits = {}
    else:
        resource_limits = dict(resource_limits)
    if "network_enabled" not in resource_limits and bool(
        environment.get("network_enabled")
    ):
        resource_limits["network_enabled"] = True
    data_mounts = environment.get("data_mounts")
    if not isinstance(data_mounts, list):
        data_mounts = []
    return {
        "sandbox_image": image_uri,
        "image_uri": image_uri,
        "digest": _image_digest(image_uri),
        "resource_limits": resource_limits,
        "data_mounts": data_mounts,
        "sandbox_policy": {
            "stage": "1",
            "image_policy": "reference_base_image",
            "data_fetch": "materialize_then_bind_mount",
        },
        "source": (
            "task.environment.sandbox_spec"
            if sandbox_spec
            else "task.environment.sandbox_image"
        ),
    }


def _build_telemetry(state: dict, *, messages: list[Message]) -> dict[str, Any]:
    return {
        "bench_eval_version": BENCH_EVAL_VERSION,
        "agent_cost": _get(state, "agent_cost") or {},
        "simulator_cost": state.get("simulator_cost"),
        "tc_checker_cost": state.get("tc_checker_cost"),
        "duration_seconds": state.get("duration_seconds"),
        "step_count": state.get("step_count"),
        "message_count": len(messages),
        "tool_call_count": len(state.get("tool_logs") or []),
    }


def _build_messages(state: dict) -> list[Message]:
    conv = state.get("conversation") or []
    npc_logs_by_turn: dict[int, dict] = {}
    for log in state.get("tool_logs") or []:
        idx = int(_get(log, "turn_index") or 0)
        if _get(log, "name") == _NPC_TOOL_NAME:
            npc_logs_by_turn.setdefault(idx, log)

    messages: list[Message] = []
    turn_idx = 0
    for idx, entry in enumerate(conv):
        role = str(_get(entry, "role") or "")
        if idx > 0:
            prev_role = _get(conv[idx - 1], "role")
            if prev_role == "assistant":
                turn_idx += 1
        attachments: list[dict[str, Any]] = []
        if role == "assistant":
            attachments = _attachments_for_turn(npc_logs_by_turn, turn_idx)
        messages.append(
            Message(
                message_id=f"msg_{idx + 1}",
                role=role,
                content=_get(entry, "content") or "",
                created_at=_coerce_iso(_get(entry, "ts")),
                turn_index=turn_idx,
                attachments=attachments,
            )
        )
    return messages


def _attachments_for_turn(
    npc_logs_by_turn: dict[int, dict], turn_idx: int
) -> list[dict]:
    log = npc_logs_by_turn.get(turn_idx)
    if not log:
        return []
    args = _get(log, "args") or {}
    raw = _get(args, "attachments") or []
    if not isinstance(raw, list):
        return []
    return [a for a in raw if isinstance(a, dict)]


def _build_tool_calls(state: dict) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for idx, log in enumerate(state.get("tool_logs") or []):
        if not isinstance(log, dict):
            continue
        calls.append(_tool_call_from_log(log, index=idx))
    return calls


def _tool_call_from_log(log: dict, *, index: int) -> ToolCall:
    metadata: dict[str, Any] = {}
    if _get(log, "name") == _NPC_TOOL_NAME:
        metadata["conversation_transport"] = True
    return ToolCall(
        tool_call_id=str(_get(log, "call_id") or f"tool_{index + 1}"),
        tool_name=str(_get(log, "name") or ""),
        args=_get(log, "args") if _get(log, "args") is not None else {},
        result=_get(log, "result"),
        created_at=_epoch_to_iso(_get(log, "timestamp")),
        duration_ms=_float_or_none(_get(log, "duration_ms")),
        success=_bool_or_none(_get(log, "success")),
        turn_index=_int_or_none(_get(log, "turn_index")),
        metadata=metadata,
    )


def _build_artifacts(
    state: dict,
    *,
    task_json: dict,
    task_spec_hash: str,
) -> dict[str, Any]:
    keys = (
        "public_task_label",
        "key_results",
        "workspace_files",
        "distractor_names",
        "trace_summary",
        "thinking_trace",
        "format_validation",
        "tc_coverage",
        "tc_debug_history",
        "artifact_debug_history",
        "evaluation_status",
    )
    reference: dict[str, Any] = {
        "run_id": state.get("run_id"),
        "session_id": state.get("session_id"),
        "persona_id": state.get("persona_id"),
        "session_status": state.get("session_status"),
        "termination_reason": state.get("termination_reason"),
        "task_version": task_json.get("version", "") if task_json else "",
        "task_spec_hash": task_spec_hash,
    }
    for key in keys:
        if key in state:
            reference[key] = state[key]
    return {REFERENCE_ARTIFACT_KEY: reference}


def _build_workspace_manifest(workspace_dir: Path) -> list[WorkspaceFile]:
    if not workspace_dir.is_dir():
        return []
    entries: list[WorkspaceFile] = []
    for root, _, files in os.walk(workspace_dir):
        for fn in files:
            full = Path(root) / fn
            try:
                data = full.read_bytes()
            except OSError as exc:
                logger.warning("workspace_manifest skip %s: %s", full, exc)
                continue
            rel = full.relative_to(workspace_dir).as_posix()
            entries.append(
                WorkspaceFile(
                    path=rel,
                    sha256=hashlib.sha256(data).hexdigest(),
                    size_bytes=len(data),
                )
            )
    return sorted(entries, key=lambda e: e.path)


def _epoch_to_iso(ts: Any) -> str:
    if ts is None:
        return ""
    if isinstance(ts, str):
        return ts
    try:
        return (
            datetime.fromtimestamp(float(ts), tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except (ValueError, TypeError, OSError):
        return ""


def _coerce_iso(ts: Any) -> str:
    if isinstance(ts, str):
        return ts
    return _epoch_to_iso(ts)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return default


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _resolve_targets(target: Path, recursive: bool) -> list[Path]:
    if target.is_file() and target.name == "run_state.json":
        return [target]
    if target.is_dir():
        candidate = target / "run_state.json"
        if candidate.exists():
            return [candidate]
        if recursive:
            return sorted(target.rglob("run_state.json"))
    raise FileNotFoundError(
        f"No run_state.json under {target}; pass --recursive to walk a results root"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "target",
        type=Path,
        help="Path to a run_state.json, its result_dir, or a results root with --recursive",
    )
    parser.add_argument(
        "--bench-root",
        type=Path,
        default=_BENCH_ROOT_DEFAULT,
        help="Repository bench/ root (used to locate task JSONs)",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Walk the target directory for every run_state.json",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing bundle.json instead of skipping",
    )
    args = parser.parse_args(argv)

    targets = _resolve_targets(args.target, args.recursive)
    written = 0
    skipped = 0
    failed = 0
    for run_state in targets:
        out_path = run_state.parent / "bundle.json"
        if out_path.exists() and not args.force:
            skipped += 1
            continue
        try:
            backfill(run_state, bench_root=args.bench_root)
            written += 1
        except Exception as exc:  # noqa: BLE001
            logger.error("backfill failed for %s: %s", run_state, exc)
            failed += 1

    print(f"wrote={written} skipped={skipped} failed={failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
