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
    SCHEMA_VERSION,
    AgentMessage,
    AgentMetadata,
    Bundle,
    ConversationTurn,
    RuntimeInfo,
    SessionInfo,
    StudentMessage,
    ToolCall,
    WorkspaceFile,
)

logger = logging.getLogger(__name__)

_BENCH_ROOT_DEFAULT = Path(__file__).resolve().parents[2]
_TOOL_RESULT_PREVIEW_LIMIT = 4096
_NPC_TOOL_NAME = "send_message"


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

    conversation = _build_conversation(state)
    bundle = Bundle(
        schema_version=SCHEMA_VERSION,
        task_id=str(state.get("task_id", "")),
        task_version=str(task_json.get("version", "")),
        task_spec_hash=_hash_task_json(task_json) if task_json else "",
        persona_id=str(state.get("persona_id", "")),
        session=_build_session(state, turn_count=len(conversation)),
        runtime=RuntimeInfo(),
        agent_metadata=AgentMetadata(harness="ref_harness"),
        conversation=conversation,
        workspace_manifest=_build_workspace_manifest(
            run_state_path.parent / "agent_files"
        ),
    )

    out = output or run_state_path.parent / "bundle.json"
    bundle_io.write(bundle, out)
    return out


def _find_task_json(bench_root: Path, task_id: str) -> Path | None:
    if not task_id:
        return None
    for path in (bench_root / "tasks" / "layer2").rglob(f"{task_id}.json"):
        return path
    return None


def _hash_task_json(task_json: dict) -> str:
    canonical = json.dumps(
        task_json, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _build_session(state: dict, *, turn_count: int) -> SessionInfo:
    conv = state.get("conversation") or []
    start_ts = ""
    end_ts = ""
    if conv:
        start_ts = _coerce_iso(_get(conv[0], "ts"))
        end_ts = _coerce_iso(_get(conv[-1], "ts"))
    if not end_ts:
        end_ts = str(state.get("timestamp") or "")

    return SessionInfo(
        session_id=str(state.get("session_id", "")),
        start_ts=start_ts,
        end_ts=end_ts,
        termination_reason=str(state.get("termination_reason") or ""),
        turn_count=turn_count,
    )


def _build_conversation(state: dict) -> list[ConversationTurn]:
    """Pair conversation entries by alternating user/assistant role.

    The persisted conversation is the source of truth for turn structure
    and message text (it omits synthetic auto-completion send_message
    logs added by the runtime when an agent gets stuck). tool_logs
    supply per-turn tool invocations matched by ``turn_index``; the
    synthetic ``send_message`` NPC tool is excluded as a tool call since
    its text is already in the conversation, but its ``args.attachments``
    are pulled forward into the agent message.
    """
    conv = state.get("conversation") or []
    tool_logs = state.get("tool_logs") or []

    tools_by_turn: dict[int, list[dict]] = {}
    npc_logs_by_turn: dict[int, dict] = {}
    for log in tool_logs:
        idx = int(_get(log, "turn_index") or 0)
        if _get(log, "name") == _NPC_TOOL_NAME:
            npc_logs_by_turn.setdefault(idx, log)
        else:
            tools_by_turn.setdefault(idx, []).append(log)

    turns: list[ConversationTurn] = []
    i = 0
    turn_idx = 0
    while i < len(conv):
        entry = conv[i]
        role = _get(entry, "role")
        if role == "user":
            user = StudentMessage(text=str(_get(entry, "content") or ""))
            agent_text = ""
            if i + 1 < len(conv) and _get(conv[i + 1], "role") == "assistant":
                agent_text = str(_get(conv[i + 1], "content") or "")
                i += 2
            else:
                i += 1
            turns.append(
                ConversationTurn(
                    turn=turn_idx + 1,
                    agent=AgentMessage(
                        text=agent_text,
                        attachments=_attachments_for_turn(npc_logs_by_turn, turn_idx),
                    ),
                    user=user,
                    tool_calls=[
                        _tool_call_from_log(log)
                        for log in tools_by_turn.get(turn_idx, [])
                    ],
                )
            )
            turn_idx += 1
        elif role == "assistant":
            turns.append(
                ConversationTurn(
                    turn=turn_idx + 1,
                    agent=AgentMessage(
                        text=str(_get(entry, "content") or ""),
                        attachments=_attachments_for_turn(npc_logs_by_turn, turn_idx),
                    ),
                    user=None,
                    tool_calls=[
                        _tool_call_from_log(log)
                        for log in tools_by_turn.get(turn_idx, [])
                    ],
                )
            )
            turn_idx += 1
            i += 1
        else:
            i += 1
    return turns


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


def _tool_call_from_log(log: dict) -> ToolCall:
    args = _get(log, "args")
    raw_result = _get(log, "result")
    result_str = "" if raw_result is None else str(raw_result)
    truncated = len(result_str) > _TOOL_RESULT_PREVIEW_LIMIT
    preview = (
        result_str[:_TOOL_RESULT_PREVIEW_LIMIT] + "..." if truncated else result_str
    )
    return ToolCall(
        call_id=str(_get(log, "call_id") or ""),
        tool=str(_get(log, "name") or ""),
        args=args if isinstance(args, dict) else {},
        result_preview=preview,
        result_truncated=truncated,
        ts=_epoch_to_iso(_get(log, "timestamp")),
        duration_ms=float(_get(log, "duration_ms") or 0.0),
        success=bool(_get(log, "success", True)),
    )


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
                    size=len(data),
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
