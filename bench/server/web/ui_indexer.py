"""Merged read model for the new isolated web UI.

This module intentionally lives under ``bench/server/web`` so the new UI stays
fully isolated from the legacy ``bench/web`` implementation.
"""

from __future__ import annotations

import csv
import json
import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

_CATEGORY_PREFIX_MAP = {
    "A": "adversarial",
    "B": "backtest",
    "D": "data_analysis",
    "E": "end_to_end",
    "I": "implementation",
    "S": "strategy",
    "X": "debug",
}

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}
_MARKDOWN_EXTENSIONS = {".md", ".markdown"}
_JSON_EXTENSIONS = {".json", ".ipynb"}
_TABULAR_EXTENSIONS = {".csv", ".tsv"}
_CODE_EXTENSIONS = {
    ".py",
    ".cs",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".sh",
    ".bash",
    ".zsh",
    ".sql",
    ".lean",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".xml",
    ".html",
    ".htm",
}
_TEXT_EXTENSIONS = _CODE_EXTENSIONS | {".txt", ".log", ".rst"}
_PREVIEW_TEXT_LIMIT_BYTES = 64 * 1024
_PREVIEW_TABLE_ROW_LIMIT = 100
_PREVIEW_TABLE_COLUMN_LIMIT = 24


class ResultIndexer:
    """Merged read model for archived server + client results."""

    def __init__(self, bench_root: str | Path):
        self.bench_root = Path(bench_root)
        self.server_results_dir = self.bench_root / "results" / "server"
        self.client_results_dir = self.bench_root / "results" / "client"
        self.tasks_dir = self.bench_root / "tasks" / "layer2"
        self.personas_dir = self.bench_root / "personas"

        # Task and persona metadata is stable across requests, so we cache it.
        self._task_meta_cache = self._load_task_meta()
        self._persona_cache = self._load_personas()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_tasks(self) -> dict[str, list[dict[str, Any]]]:
        tasks = sorted(self._task_meta_cache.values(), key=lambda item: item["task_id"])
        personas = sorted(
            self._persona_cache.values(), key=lambda item: item["persona_id"]
        )
        return {"tasks": tasks, "personas": personas}

    def list_results(
        self,
        *,
        category: str | None = None,
        task_id: str | None = None,
        eval_status: str | None = None,
    ) -> list[dict[str, Any]]:
        normalized_category = self._normalize_filter(category)
        normalized_task_id = self._normalize_filter(task_id)
        normalized_eval_status = self._normalize_filter(eval_status)

        results: list[dict[str, Any]] = []
        for result_dir in self._iter_result_dirs():
            summary = self._build_summary(result_dir)
            if not summary:
                continue

            if normalized_category and summary.get("category") != normalized_category:
                continue
            if normalized_task_id and summary.get("task_id") != normalized_task_id:
                continue
            if (
                normalized_eval_status
                and summary.get("evaluation_status") != normalized_eval_status
            ):
                continue

            results.append(summary)

        results.sort(
            key=lambda item: (
                float(item.pop("_sort_ts", 0.0)),
                str(item.get("session_id", "")),
            ),
            reverse=True,
        )
        return results

    def get_detail(self, session_id: str) -> dict[str, Any] | None:
        result_dir = self._find_result_dir(session_id)
        if result_dir is None:
            return None

        run_state = self._load_json(result_dir / "run_state.json")
        if not isinstance(run_state, dict):
            return None

        task_id = str(run_state.get("task_id") or result_dir.parent.name)
        task_meta = self._task_meta_cache.get(
            task_id, self._make_fallback_task_meta(task_id)
        )
        client_trace = self._load_client_trace(session_id)
        latest_eval_dir = self._resolve_latest_eval_dir(result_dir)
        latest_eval_meta = (
            self._load_json(latest_eval_dir / "eval_meta.json")
            if latest_eval_dir
            else None
        )

        conversation = self._ensure_list(run_state.get("conversation"))
        raw_tool_logs = self._ensure_list(run_state.get("tool_logs"))
        tool_logs, send_message_events = self._split_tool_logs(raw_tool_logs)
        send_message_events = self._enrich_send_message_events(
            send_message_events,
            conversation,
        )
        workspace_files = self._ensure_list(run_state.get("workspace_files"))
        distractor_names = self._ensure_list(run_state.get("distractor_names"))
        agent_cost = self._extract_agent_cost(client_trace)
        model = self._extract_model(agent_cost)
        timestamp_text, _ = self._resolve_timestamp(
            client_trace=client_trace,
            run_state_path=result_dir / "run_state.json",
        )

        detail = {
            "session_id": str(run_state.get("session_id") or session_id),
            "task_id": task_id,
            "category": task_meta.get("category", "unknown"),
            "difficulty": task_meta.get("difficulty", "unknown"),
            "description": task_meta.get("description", ""),
            "persona_id": str(run_state.get("persona_id") or "unknown"),
            "duration_seconds": self._coerce_float(
                run_state.get("duration_seconds"), default=0.0
            ),
            "turn_count": self._count_assistant_turns(conversation),
            "tool_count": len(tool_logs),
            "send_message_count": len(send_message_events),
            "step_count": self._coerce_int(
                run_state.get("step_count"), default=len(raw_tool_logs)
            ),
            "model": model,
            "agent_name": self._extract_agent_name(model),
            "timestamp": timestamp_text,
            "evaluation_status": self._resolve_evaluation_status(
                run_state, latest_eval_dir
            ),
            "overall_score": self._extract_overall_score(latest_eval_meta),
            "has_client_trace": client_trace is not None,
            "has_content_blocks": self._has_content_blocks(client_trace, conversation),
            "has_agent_files": self._dir_has_entries(result_dir / "agent_files"),
            "conversation": self._merge_conversation(conversation, client_trace),
            "tool_logs": tool_logs,
            "all_tool_logs": raw_tool_logs,
            "send_message_events": send_message_events,
            "workspace_files": workspace_files,
            "distractor_names": distractor_names,
            "scores_md": (
                self._read_text(latest_eval_dir / "scores.md")
                if latest_eval_dir
                else None
            ),
            "cost_md": (
                self._read_text(latest_eval_dir / "cost.md")
                if latest_eval_dir
                else None
            ),
            "trace_md": (
                self._read_text(latest_eval_dir / "trace.md")
                if latest_eval_dir
                else None
            ),
            "eval_history": self._load_eval_history(result_dir),
            "agent_cost": agent_cost,
            "requires_code": task_meta.get("requires_code", False),
            "max_turns": task_meta.get("max_turns"),
        }
        return detail

    def resolve_agent_file(self, session_id: str, relative_path: str) -> Path | None:
        result_dir = self._find_result_dir(session_id)
        if result_dir is None:
            return None

        agent_files_root = (result_dir / "agent_files").resolve()
        full_path = (agent_files_root / relative_path).resolve()

        try:
            full_path.relative_to(agent_files_root)
        except ValueError as exc:
            raise ValueError("Invalid path") from exc

        if not full_path.exists() or not full_path.is_file():
            return None
        return full_path

    def get_workspace_index(self, session_id: str) -> dict[str, Any] | None:
        result_dir = self._find_result_dir(session_id)
        if result_dir is None:
            return None

        run_state = self._load_json(result_dir / "run_state.json")
        workspace_files = self._extract_workspace_files(run_state, result_dir)
        entries = self._build_workspace_entries(session_id, result_dir, workspace_files)

        return {
            "session_id": session_id,
            "file_count": len(entries),
            "top_extensions": self._workspace_top_extensions(entries),
            "files": entries,
        }

    def get_workspace_preview(
        self, session_id: str, relative_path: str
    ) -> dict[str, Any] | None:
        resolved = self.resolve_agent_file(session_id, relative_path)
        if resolved is None:
            return None

        relative = self._normalize_workspace_relative_path(relative_path)
        entry = self._build_workspace_entry(session_id, relative, resolved)
        preview = dict(entry)
        preview["truncated"] = False

        kind = str(entry.get("kind") or "binary")
        if kind == "image":
            return preview

        if kind == "binary":
            preview["message"] = "Preview unavailable for this file type."
            return preview

        content_text, truncated = self._read_preview_text(resolved)
        preview["truncated"] = truncated

        if kind == "markdown":
            preview["content_text"] = content_text
            return preview

        if kind == "json":
            preview["content_text"] = self._format_json_preview(content_text, truncated)
            return preview

        if kind == "csv":
            preview.update(
                self._build_tabular_preview(
                    content_text, entry.get("extension") == ".tsv"
                )
            )
            return preview

        preview["content_text"] = content_text
        return preview

    # ------------------------------------------------------------------
    # Metadata loading
    # ------------------------------------------------------------------

    def _load_task_meta(self) -> dict[str, dict[str, Any]]:
        task_meta: dict[str, dict[str, Any]] = {}
        if not self.tasks_dir.is_dir():
            return task_meta

        for task_path in sorted(self.tasks_dir.rglob("*.json")):
            payload = self._load_json(task_path)
            if not isinstance(payload, dict):
                continue

            task_id = str(payload.get("task_id") or task_path.stem)
            task_meta[task_id] = {
                "task_id": task_id,
                "category": str(
                    payload.get("category")
                    or task_path.parent.name
                    or self._infer_category_from_task_id(task_id)
                ),
                "difficulty": str(payload.get("difficulty") or "unknown"),
                "description": str(payload.get("description") or ""),
                "persona_ids": self._ensure_list(payload.get("persona_ids")),
                "max_turns": payload.get("max_turns"),
                "timeout_minutes": payload.get("timeout_minutes"),
                "requires_code": bool(payload.get("requires_code", False)),
            }

        return task_meta

    def _load_personas(self) -> dict[str, dict[str, Any]]:
        personas: dict[str, dict[str, Any]] = {}
        if not self.personas_dir.is_dir():
            return personas

        for persona_path in sorted(self.personas_dir.glob("*.json")):
            payload = self._load_json(persona_path)
            if not isinstance(payload, dict):
                continue

            persona_id = str(payload.get("persona_id") or persona_path.stem)
            personas[persona_id] = {
                "persona_id": persona_id,
                "knowledge_level": str(payload.get("knowledge_level") or ""),
                "description": str(payload.get("description") or ""),
            }

        return personas

    # ------------------------------------------------------------------
    # Result scanning
    # ------------------------------------------------------------------

    def _iter_result_dirs(self):
        """Iterate all result directories.

        Supports both old layout  ``{task_id}/{session_id}/``
        and new layout ``{task_id}/{persona_id}/{ts}_{short_id}/``.
        A result dir is identified by containing ``run_state.json``.
        """
        if not self.server_results_dir.is_dir():
            return

        for task_dir in sorted(self.server_results_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            for child in sorted(task_dir.iterdir()):
                if not child.is_dir():
                    continue
                if (child / "run_state.json").exists():
                    # Old layout: {task_id}/{session_id}/
                    yield child
                else:
                    # New layout: {task_id}/{persona_id}/{ts}_{short_id}/
                    for run_dir in sorted(child.iterdir()):
                        if run_dir.is_dir() and (run_dir / "run_state.json").exists():
                            yield run_dir

    def _find_result_dir(self, session_id: str) -> Path | None:
        short_id = session_id[:8]
        for result_dir in self._iter_result_dirs():
            # Old layout: dir name == full session_id
            if result_dir.name == session_id:
                return result_dir
            # New layout: dir name ends with _{short_id}
            if result_dir.name.endswith(f"_{short_id}"):
                return result_dir
        return None

    def _build_summary(self, result_dir: Path) -> dict[str, Any] | None:
        run_state = self._load_json(result_dir / "run_state.json")
        if not isinstance(run_state, dict):
            return None

        task_id = str(run_state.get("task_id") or result_dir.parent.name)
        task_meta = self._task_meta_cache.get(
            task_id, self._make_fallback_task_meta(task_id)
        )
        session_id = str(run_state.get("session_id") or result_dir.name)
        client_trace = self._load_client_trace(session_id)
        latest_eval_dir = self._resolve_latest_eval_dir(result_dir)
        latest_eval_meta = (
            self._load_json(latest_eval_dir / "eval_meta.json")
            if latest_eval_dir
            else None
        )
        conversation = self._ensure_list(run_state.get("conversation"))
        raw_tool_logs = self._ensure_list(run_state.get("tool_logs"))
        tool_logs, send_message_events = self._split_tool_logs(raw_tool_logs)
        agent_cost = self._extract_agent_cost(client_trace)
        model = self._extract_model(agent_cost)
        timestamp_text, sort_ts = self._resolve_timestamp(
            client_trace=client_trace,
            run_state_path=result_dir / "run_state.json",
        )

        return {
            "run_id": str(run_state.get("run_id") or ""),
            "public_task_label": str(run_state.get("public_task_label") or ""),
            "session_id": session_id,
            "task_id": task_id,
            "category": task_meta.get("category", "unknown"),
            "difficulty": task_meta.get("difficulty", "unknown"),
            "persona_id": str(run_state.get("persona_id") or "unknown"),
            "duration_seconds": self._coerce_float(
                run_state.get("duration_seconds"), default=0.0
            ),
            "turn_count": self._count_assistant_turns(conversation),
            "tool_count": len(tool_logs),
            "send_message_count": len(send_message_events),
            "step_count": self._coerce_int(
                run_state.get("step_count"), default=len(raw_tool_logs)
            ),
            "evaluation_status": self._resolve_evaluation_status(
                run_state, latest_eval_dir
            ),
            "overall_score": self._extract_overall_score(latest_eval_meta),
            "has_client_trace": client_trace is not None,
            "has_content_blocks": self._has_content_blocks(client_trace, conversation),
            "has_agent_files": self._dir_has_entries(result_dir / "agent_files"),
            "model": model,
            "agent_name": self._extract_agent_name(model),
            "timestamp": timestamp_text,
            "_sort_ts": sort_ts,
        }

    def _load_client_trace(self, session_id: str) -> dict[str, Any] | None:
        """Best-effort load of client-side trace (thinking blocks, cost).

        Client and server result stores are fully independent.  When a
        matching client_trace.json exists we merge its content_blocks into
        the conversation replay; when it does not we silently fall back to
        server-only conversation display.
        """
        try:
            for trace_session_id in self._client_trace_session_id_candidates(
                session_id
            ):
                trace_path = (
                    self.client_results_dir / trace_session_id / "client_trace.json"
                )
                if not trace_path.exists():
                    continue
                payload = self._load_json(trace_path)
                if isinstance(payload, dict):
                    return payload
            return None
        except Exception:
            return None

    def _client_trace_session_id_candidates(self, session_id: str) -> list[str]:
        """Return possible client trace ids for a server-side session id.

        Server archives may persist ``run_state.session_id`` as
        ``{uuid}_{task_prefix}`` for faster lookup, while client traces are
        still written under the original ``{uuid}`` returned at registration.
        """
        raw = str(session_id or "").strip()
        candidates: list[str] = []

        def add(value: str) -> None:
            value = str(value or "").strip()
            if value and value not in candidates:
                candidates.append(value)

        add(raw)
        match = re.match(r"^(.+)_([A-Z]\d{2})$", raw)
        if match:
            add(match.group(1))

        short_id = raw[:8]
        if short_id and self.client_results_dir.is_dir():
            matches = sorted(
                path.name
                for path in self.client_results_dir.iterdir()
                if path.is_dir()
                and path.name.startswith(short_id)
                and (path / "client_trace.json").exists()
            )
            for name in matches:
                add(name)

        return candidates

    # ------------------------------------------------------------------
    # Evaluation loading
    # ------------------------------------------------------------------

    def _resolve_latest_eval_dir(self, result_dir: Path) -> Path | None:
        eval_root = result_dir / "evaluations"
        if not eval_root.exists():
            return None

        latest = eval_root / "latest"
        if latest.exists() and latest.is_dir():
            return latest.resolve()

        eval_dirs = sorted(
            [
                path
                for path in eval_root.iterdir()
                if path.is_dir() and path.name.startswith("eval_")
            ],
            key=lambda path: path.name,
            reverse=True,
        )
        return eval_dirs[0] if eval_dirs else None

    def _load_eval_history(self, result_dir: Path) -> list[dict[str, Any]]:
        eval_root = result_dir / "evaluations"
        if not eval_root.is_dir():
            return []

        history: list[dict[str, Any]] = []
        for eval_dir in sorted(eval_root.iterdir()):
            if not eval_dir.is_dir() or not eval_dir.name.startswith("eval_"):
                continue

            meta = self._load_json(eval_dir / "eval_meta.json")
            if not isinstance(meta, dict):
                continue

            entry = dict(meta)
            entry["eval_dir"] = eval_dir.name
            history.append(entry)

        history.sort(key=lambda item: str(item.get("timestamp", "")), reverse=True)
        return history

    # ------------------------------------------------------------------
    # Merge helpers
    # ------------------------------------------------------------------

    def _split_tool_logs(
        self,
        tool_logs: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        domain_tool_logs: list[dict[str, Any]] = []
        send_message_events: list[dict[str, Any]] = []

        for raw in tool_logs:
            log = raw if isinstance(raw, dict) else {}
            if str(log.get("name") or "") == "send_message":
                send_message_events.append(self._build_send_message_event(log))
            else:
                domain_tool_logs.append(log)

        return domain_tool_logs, send_message_events

    def _build_send_message_event(self, log: dict[str, Any]) -> dict[str, Any]:
        args = log.get("args")
        if not isinstance(args, dict):
            args = {}

        raw_result = str(log.get("result") or "")
        parsed_result: dict[str, Any] = {}
        if raw_result:
            try:
                payload = json.loads(raw_result)
                if isinstance(payload, dict):
                    parsed_result = payload
            except (json.JSONDecodeError, TypeError):
                parsed_result = {}

        success = bool(log.get("success", True))
        status = str(
            parsed_result.get("status") or ("failed" if not success else "active")
        )
        reason = parsed_result.get("reason")
        error = parsed_result.get("error")
        attachments = self._normalize_send_message_attachments(args.get("attachments"))

        return {
            "name": "send_message",
            "turn_index": self._coerce_int(log.get("turn_index"), default=0),
            "request_text": str(args.get("text") or ""),
            "student_message": str(parsed_result.get("student_message") or ""),
            "attachments": attachments,
            "status": status,
            "reason": str(reason) if reason is not None else None,
            "error": str(error) if error is not None else None,
            "success": success,
            "duration_ms": self._coerce_float(log.get("duration_ms"), default=None),
            "timestamp": log.get("timestamp"),
            "raw_result": raw_result,
        }

    def _enrich_send_message_events(
        self,
        events: list[dict[str, Any]],
        conversation: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        attachments_by_turn = self._conversation_attachments_by_turn(conversation)
        if not attachments_by_turn:
            return events

        enriched: list[dict[str, Any]] = []
        for event in events:
            turn_index = self._coerce_int(event.get("turn_index"), default=0)
            attachments = attachments_by_turn.get(turn_index)
            if attachments:
                merged = dict(event)
                merged["attachments"] = attachments
                enriched.append(merged)
            else:
                enriched.append(event)
        return enriched

    def _conversation_attachments_by_turn(
        self,
        conversation: list[dict[str, Any]],
    ) -> dict[int, list[dict[str, Any]]]:
        attachments_by_turn: dict[int, list[dict[str, Any]]] = {}
        assistant_idx = 0
        for turn in conversation:
            if not isinstance(turn, dict):
                continue
            if turn.get("role") != "assistant":
                continue
            attachments = self._normalize_send_message_attachments(
                turn.get("attachments")
            )
            if attachments:
                attachments_by_turn[assistant_idx] = attachments
            assistant_idx += 1
        return attachments_by_turn

    def _normalize_send_message_attachments(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []

        attachments: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, str):
                path = self._normalize_workspace_relative_path(item)
                if not path:
                    continue
                attachments.append(
                    {
                        "filename": Path(path).name,
                        "path": path,
                        "is_image": Path(path).suffix.lower() in _IMAGE_EXTENSIONS,
                    }
                )
                continue

            if not isinstance(item, dict):
                continue

            raw_path = item.get("path") or item.get("filename") or item.get("name")
            path = self._normalize_workspace_relative_path(str(raw_path or ""))
            if not path:
                continue

            record: dict[str, Any] = {
                "filename": str(item.get("filename") or Path(path).name),
                "path": path,
                "is_image": bool(
                    item.get("is_image")
                    or Path(path).suffix.lower() in _IMAGE_EXTENSIONS
                ),
            }
            for key in ("media_type", "truncated", "content"):
                if key in item:
                    record[key] = item[key]
            attachments.append(record)

        return attachments

    def _merge_conversation(
        self,
        server_conversation: list[dict[str, Any]],
        client_trace: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        if not client_trace:
            return [
                {
                    "role": turn.get("role"),
                    "content": turn.get("content", ""),
                    "content_blocks": None,
                }
                for turn in server_conversation
            ]

        content_blocks_by_turn = self._extract_turn_aligned_content_blocks(
            client_trace, server_conversation
        )

        merged: list[dict[str, Any]] = []
        assistant_idx = 0
        for turn in server_conversation:
            entry = {
                "role": turn.get("role"),
                "content": turn.get("content", ""),
                "content_blocks": None,
            }
            if turn.get("role") == "assistant":
                blocks = content_blocks_by_turn.get(str(assistant_idx))
                if isinstance(blocks, list) and blocks:
                    entry["content_blocks"] = blocks
                assistant_idx += 1
            merged.append(entry)

        return merged

    def _extract_turn_aligned_content_blocks(
        self,
        client_trace: dict[str, Any] | None,
        conversation: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        if not client_trace:
            return {}

        payload = client_trace.get("content_blocks")
        if not isinstance(payload, dict):
            return {}

        assistant_turn_count = self._count_assistant_turns(conversation)
        if assistant_turn_count <= 0:
            return {}

        cleaned: dict[int, list[dict[str, Any]]] = {}
        for key, value in payload.items():
            try:
                idx = int(key)
            except (TypeError, ValueError):
                return {}
            if isinstance(value, list) and value:
                cleaned[idx] = value

        if not cleaned:
            return {}

        # Short-term safeguard: the current whole-session baseline may save
        # the entire session under content_blocks["0"]. Inlining that into the
        # first tutor turn causes every later turn to appear shifted.
        if len(cleaned) == 1 and assistant_turn_count > 1:
            only_blocks = next(iter(cleaned.values()))
            send_message_uses = sum(
                1
                for block in only_blocks
                if isinstance(block, dict)
                and block.get("type") == "tool_use"
                and block.get("name") == "send_message"
            )
            if send_message_uses > 1:
                return {}

        expected = set(range(assistant_turn_count))
        if set(cleaned.keys()) != expected:
            return {}

        return {str(idx): cleaned[idx] for idx in sorted(cleaned)}

    def _extract_workspace_files(self, run_state: Any, result_dir: Path) -> list[str]:
        if isinstance(run_state, dict):
            files = self._ensure_list(run_state.get("workspace_files"))
            cleaned = [
                self._normalize_workspace_relative_path(path)
                for path in files
                if isinstance(path, str)
                and self._normalize_workspace_relative_path(path)
            ]
            if cleaned:
                return cleaned

        agent_files_root = result_dir / "agent_files"
        if not agent_files_root.is_dir():
            return []

        return sorted(
            str(path.relative_to(agent_files_root))
            for path in agent_files_root.rglob("*")
            if path.is_file()
        )

    def _build_workspace_entries(
        self,
        session_id: str,
        result_dir: Path,
        workspace_files: list[str],
    ) -> list[dict[str, Any]]:
        agent_files_root = (result_dir / "agent_files").resolve()
        entries: list[dict[str, Any]] = []

        for relative_path in workspace_files:
            normalized = self._normalize_workspace_relative_path(relative_path)
            if not normalized:
                continue
            full_path = (agent_files_root / normalized).resolve()
            try:
                full_path.relative_to(agent_files_root)
            except ValueError:
                continue
            if not full_path.exists() or not full_path.is_file():
                continue
            entries.append(
                self._build_workspace_entry(session_id, normalized, full_path)
            )

        entries.sort(key=lambda item: str(item.get("path", "")))
        return entries

    def _build_workspace_entry(
        self,
        session_id: str,
        relative_path: str,
        full_path: Path,
    ) -> dict[str, Any]:
        extension = full_path.suffix.lower()
        mime_type = (
            mimetypes.guess_type(full_path.name)[0] or "application/octet-stream"
        )
        kind = self._classify_workspace_kind(relative_path, mime_type)
        return {
            "path": relative_path,
            "name": full_path.name,
            "extension": extension or "",
            "size_bytes": full_path.stat().st_size,
            "mime_type": mime_type,
            "kind": kind,
            "raw_url": self._build_workspace_raw_url(session_id, relative_path),
        }

    def _workspace_top_extensions(self, entries: list[dict[str, Any]]) -> list[str]:
        counts: dict[str, int] = {}
        for entry in entries:
            ext = str(entry.get("extension") or "").lower()
            key = ext if ext else "[no_ext]"
            counts[key] = counts.get(key, 0) + 1
        ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
        return [key for key, _ in ranked[:4]]

    def _normalize_workspace_relative_path(self, value: str) -> str:
        text = str(value or "").replace("\\", "/").strip().lstrip("/")
        return str(Path(text)) if text else ""

    def _build_workspace_raw_url(self, session_id: str, relative_path: str) -> str:
        encoded = "/".join(
            quote(segment, safe="")
            for segment in self._normalize_workspace_relative_path(relative_path).split(
                "/"
            )
            if segment
        )
        return f"/ui/results/{quote(session_id, safe='')}/files/{encoded}"

    def _classify_workspace_kind(self, relative_path: str, mime_type: str) -> str:
        extension = Path(relative_path).suffix.lower()
        if extension in _IMAGE_EXTENSIONS:
            return "image"
        if extension in _MARKDOWN_EXTENSIONS:
            return "markdown"
        if extension in _JSON_EXTENSIONS:
            return "json"
        if extension in _TABULAR_EXTENSIONS:
            return "csv"
        if extension in _TEXT_EXTENSIONS:
            return "code" if extension in _CODE_EXTENSIONS else "text"
        if mime_type.startswith("text/"):
            return "text"
        return "binary"

    def _read_preview_text(self, path: Path) -> tuple[str, bool]:
        with path.open("rb") as handle:
            chunk = handle.read(_PREVIEW_TEXT_LIMIT_BYTES + 1)
        truncated = len(chunk) > _PREVIEW_TEXT_LIMIT_BYTES
        if truncated:
            chunk = chunk[:_PREVIEW_TEXT_LIMIT_BYTES]
        return chunk.decode("utf-8", errors="replace"), truncated

    def _format_json_preview(self, text: str, truncated: bool) -> str:
        if truncated:
            return text
        try:
            payload = json.loads(text)
        except Exception:
            return text
        return json.dumps(payload, indent=2, ensure_ascii=False)

    def _build_tabular_preview(self, text: str, is_tsv: bool) -> dict[str, Any]:
        delimiter = "\t" if is_tsv else ","
        rows: list[list[str]] = []
        try:
            reader = csv.reader(text.splitlines(), delimiter=delimiter)
            for row_index, row in enumerate(reader):
                if row_index >= _PREVIEW_TABLE_ROW_LIMIT:
                    break
                rows.append([str(cell) for cell in row[:_PREVIEW_TABLE_COLUMN_LIMIT]])
        except Exception:
            rows = []

        columns = rows[0] if rows else []
        data_rows = rows[1:] if len(rows) > 1 else []

        return {
            "columns": columns,
            "rows": data_rows,
            "row_limit": _PREVIEW_TABLE_ROW_LIMIT,
            "column_limit": _PREVIEW_TABLE_COLUMN_LIMIT,
        }

    # ------------------------------------------------------------------
    # Field extraction
    # ------------------------------------------------------------------

    def _resolve_evaluation_status(
        self, run_state: dict[str, Any], latest_eval_dir: Path | None
    ) -> str:
        status = str(run_state.get("evaluation_status") or "pending")
        if status == "pending" and latest_eval_dir is not None:
            return "completed"
        return status

    def _extract_overall_score(self, latest_eval_meta: Any) -> float | None:
        if not isinstance(latest_eval_meta, dict):
            return None
        return self._coerce_float(latest_eval_meta.get("overall_score"), default=None)

    def _extract_agent_cost(
        self, client_trace: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        if not client_trace:
            return None
        payload = client_trace.get("agent_cost")
        return payload if isinstance(payload, dict) else None

    def _extract_model(self, agent_cost: dict[str, Any] | None) -> str | None:
        if not agent_cost:
            return None
        model = agent_cost.get("model")
        if model is None:
            return None
        model_text = str(model).strip()
        return model_text or None

    def _extract_agent_name(self, model: str | None) -> str | None:
        if not model:
            return None
        if "/" not in model:
            return None
        prefix = model.split("/", 1)[0].strip()
        return prefix or None

    def _resolve_timestamp(
        self,
        *,
        client_trace: dict[str, Any] | None,
        run_state_path: Path,
    ) -> tuple[str, float]:
        if client_trace and client_trace.get("timestamp"):
            parsed = self._parse_timestamp(str(client_trace["timestamp"]))
            if parsed is not None:
                return str(client_trace["timestamp"]), parsed.timestamp()

        mtime = run_state_path.stat().st_mtime if run_state_path.exists() else 0.0
        timestamp = (
            datetime.fromtimestamp(mtime, tz=timezone.utc)
            .isoformat()
            .replace("+00:00", "Z")
        )
        return timestamp, mtime

    def _parse_timestamp(self, value: str) -> datetime | None:
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None

    def _has_content_blocks(
        self,
        client_trace: dict[str, Any] | None,
        conversation: list[dict[str, Any]],
    ) -> bool:
        return bool(
            self._extract_turn_aligned_content_blocks(client_trace, conversation)
        )

    def _count_assistant_turns(self, conversation: list[dict[str, Any]]) -> int:
        return sum(1 for turn in conversation if turn.get("role") == "assistant")

    def _dir_has_entries(self, directory: Path) -> bool:
        return directory.is_dir() and any(directory.iterdir())

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    def _load_json(self, path: Path) -> Any:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _read_text(self, path: Path) -> str | None:
        if not path.exists():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except Exception:
            return None

    def _normalize_filter(self, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = str(value).strip()
        if not normalized or normalized.lower() == "all":
            return None
        return normalized

    def _ensure_list(self, value: Any) -> list[Any]:
        return value if isinstance(value, list) else []

    def _coerce_float(self, value: Any, *, default: float | None) -> float | None:
        try:
            if value is None:
                return default
            return float(value)
        except (TypeError, ValueError):
            return default

    def _coerce_int(self, value: Any, *, default: int) -> int:
        try:
            if value is None:
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def _infer_category_from_task_id(self, task_id: str) -> str:
        prefix = str(task_id[:1]).upper()
        return _CATEGORY_PREFIX_MAP.get(prefix, "unknown")

    def _make_fallback_task_meta(self, task_id: str) -> dict[str, Any]:
        return {
            "task_id": task_id,
            "category": self._infer_category_from_task_id(task_id),
            "difficulty": "unknown",
            "description": "",
            "persona_ids": [],
            "max_turns": None,
            "timeout_minutes": None,
            "requires_code": False,
        }
