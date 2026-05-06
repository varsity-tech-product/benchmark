"""Programmatic-only Impl B task suite."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from eval.contracts.schemas import QuantTutorTask
from platform_api.contracts import DataMount, EvalItem, SandboxSpec, TaskSuite

_CORE_TOOLS = (
    "shell_exec",
    "file_write",
    "file_read",
    "file_list",
    "get_environment_info",
)
_TASK_DIR = "impl_b"
_DEFAULT_SANDBOX_IMAGE = "quant-bench-env:v3.0"
_DEFAULT_RESOURCE_LIMITS = {
    "cpu_count": 1,
    "memory_mb": 512,
    "wall_timeout_seconds": 120,
    "network_enabled": False,
}


def _default_bench_root() -> Path:
    env_root = os.environ.get("QTB_BENCH_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[2]


class ImplBTaskSuite(TaskSuite):
    """Small deterministic task corpus for framework swappability checks."""

    def __init__(self, bench_root: str | Path | None = None) -> None:
        self.bench_root = Path(bench_root) if bench_root else _default_bench_root()
        self._task_paths: dict[str, Path] | None = None
        self._task_cache: dict[str, dict[str, Any]] = {}

    def configure(
        self,
        *,
        bench_root: str | Path | None = None,
        eval_model: str | None = None,
    ) -> None:
        if bench_root is not None:
            self.bench_root = Path(bench_root)
            self._task_paths = None
            self._task_cache.clear()

    def supported_tasks(self) -> set[str]:
        return set(self._index_task_paths())

    def get_task(self, task_id: str) -> EvalItem:
        raw = self._load_task_payload(task_id)
        expected_outputs = list(raw.get("expected_outputs") or [])
        sandbox_spec = self._sandbox_spec(raw)
        data_mounts = self._data_mounts(raw)
        return EvalItem(
            task_id=str(raw["task_id"]),
            version=str(raw.get("version") or "1.0"),
            task_type="agent_execution",
            payload={
                "impl_b_task": raw,
                "expected_outputs": expected_outputs,
                "prompt": self._prompt(raw),
            },
            metadata={
                "business_schema": "ImplBTask",
                "public_run_task": True,
                "programmatic_only": True,
                "npc_optional": True,
                "requires_llm_judge": False,
            },
            data_mounts=data_mounts,
            sandbox_spec=sandbox_spec,
        )

    def get_business_task(self, task_id: str) -> QuantTutorTask:
        """Return a server-compatible task object for existing session runtime."""
        raw = self._load_task_payload(task_id)
        sandbox_spec = self._sandbox_spec(raw)
        return QuantTutorTask(
            task_id=str(raw["task_id"]),
            version=str(raw.get("version") or "1.0"),
            layer="L1",
            category=str(raw.get("category") or "data_engineering"),
            subcategory=str(raw.get("subcategory") or "impl_b"),
            task_type="agent_execution",
            difficulty=str(raw.get("difficulty") or "easy"),
            description=str(raw.get("description") or self._prompt(raw)),
            agent_prompt=self._prompt(raw),
            persona_id=str(raw.get("persona_id") or "double_novice"),
            user_opening=self._prompt(raw),
            environment={
                "data_files": [],
                "data_mounts": [
                    self._json_data_mount(mount) for mount in self._data_mounts(raw)
                ],
                "core_mcp_tools": list(raw.get("core_mcp_tools") or _CORE_TOOLS),
                "docs_available": [],
                "sandbox_image": sandbox_spec.image_uri,
                "sandbox_spec": {
                    "image_uri": sandbox_spec.image_uri,
                    "resource_limits": dict(sandbox_spec.resource_limits),
                },
                "network_enabled": False,
            },
            ground_truth={
                "required_capabilities": list(raw.get("required_capabilities") or []),
                "expected_outputs": list(raw.get("expected_outputs") or []),
                "expected_outcome": str(raw.get("expected_outcome") or ""),
            },
            requires_code=True,
            requires_tool=True,
            max_turns=int(raw.get("max_turns") or 2),
            timeout_minutes=int(raw.get("timeout_minutes") or 5),
            seed=int(raw["seed"]) if raw.get("seed") is not None else None,
        )

    def required_bundle_fields(self) -> set[str]:
        return {"conversation", "tool_logs", "workspace_path", "result_dir"}

    def _index_task_paths(self) -> dict[str, Path]:
        if self._task_paths is None:
            tasks_dir = self.bench_root / "tasks" / _TASK_DIR
            self._task_paths = {
                path.stem: path
                for path in sorted(tasks_dir.glob("*.json"))
                if path.is_file()
            }
        return self._task_paths

    def _load_task_payload(self, task_id: str) -> dict[str, Any]:
        if task_id in self._task_cache:
            return self._task_cache[task_id]
        paths = self._index_task_paths()
        path = paths.get(task_id)
        if path is None:
            raise KeyError(f"Task not found: {task_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self._task_cache[task_id] = payload
        return payload

    def _data_mounts(self, raw: dict[str, Any]) -> tuple[DataMount, ...]:
        mounts: list[DataMount] = []
        for item in raw.get("data_mounts") or []:
            uri = str(item["uri"])
            mounts.append(
                DataMount(
                    uri=self._resolve_file_uri(uri),
                    target_path=str(item["target_path"]),
                    read_only=bool(item.get("read_only", True)),
                )
            )
        return tuple(mounts)

    def _resolve_file_uri(self, uri: str) -> str:
        if not uri.startswith("file://"):
            return uri
        raw = uri.removeprefix("file://")
        if raw.startswith("localhost/"):
            raw = raw.removeprefix("localhost")
        path = Path(unquote(raw)).expanduser()
        if not path.is_absolute():
            path = self.bench_root / path
        return f"file://{path.resolve().as_posix()}"

    @staticmethod
    def _json_data_mount(mount: DataMount) -> dict[str, Any]:
        return {
            "uri": mount.uri,
            "target_path": mount.target_path,
            "read_only": mount.read_only,
        }

    @staticmethod
    def _sandbox_spec(raw: dict[str, Any]) -> SandboxSpec:
        sandbox = raw.get("sandbox") if isinstance(raw.get("sandbox"), dict) else {}
        return SandboxSpec(
            image_uri=str(sandbox.get("image_uri") or _DEFAULT_SANDBOX_IMAGE),
            resource_limits=dict(
                sandbox.get("resource_limits") or _DEFAULT_RESOURCE_LIMITS
            ),
        )

    @staticmethod
    def _prompt(raw: dict[str, Any]) -> str:
        return str(raw.get("prompt") or raw.get("description") or "")
