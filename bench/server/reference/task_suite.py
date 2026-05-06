"""Reference task suite adapter for the platform plugin API."""

from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
from typing import Any

from config.benchmark_config import BENCH_IMAGE_V3, BENCH_IMAGE_V3_LEAN
from eval.contracts.schemas import QuantTutorTask
from eval.rubrics.task_profiles import rubric_profile_for_task
from platform_api.contracts import DataMount, EvalItem, SandboxSpec, TaskSuite
from server.config.benchmark_config import DATASET_REPO_ID, DATASET_REVISION


_CODE_CATEGORIES = {
    "code_generation",
    "code_debugging",
    "implementation",
    "end_to_end",
    "alpha_research",
    "backtest_engine",
    "data_engineering",
    "debug",
    "diagnostic",
}
_V3_LEAN_CATEGORIES = {"implementation"}
_TASK_LAYERS = ("L0", "L1", "L2")
_TASK_PREFIXES = tuple(f"{layer}_" for layer in _TASK_LAYERS)
DEFAULT_AGENT_BRIEF = (
    "You are a tutoring agent. The user is a learner working on a "
    "quantitative-finance task. Teach the concept and guide the learner "
    "through the answer. Tool errors are diagnostic signals. When a tool "
    "returns an error (PermissionError, missing path, timeout), explain "
    "the constraint to the user and adjust your approach."
)


def _default_bench_root() -> Path:
    env_root = os.environ.get("QTB_BENCH_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[2]


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _jsonable_task(task: QuantTutorTask) -> dict[str, Any]:
    return task.model_dump(mode="json")


class ReferenceTaskSuite(TaskSuite):
    """Bridge reference QuantTutor task JSONs into EvalItem envelopes."""

    def __init__(self, bench_root: str | Path | None = None) -> None:
        self.bench_root = Path(bench_root) if bench_root else _default_bench_root()
        self._task_paths: dict[str, Path] | None = None
        self._task_cache: dict[str, QuantTutorTask] = {}

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
        task = self.get_business_task(task_id)
        env = task.environment
        metadata = {
            "business_schema": "QuantTutorTask",
            "difficulty": _enum_value(task.difficulty),
            "category": _enum_value(task.category),
            "rubric_profile": rubric_profile_for_task(task),
            "requires_code": bool(task.requires_code),
            "requires_tool": bool(task.requires_tool),
        }
        return EvalItem(
            task_id=task.task_id,
            version=task.version,
            task_type=_enum_value(task.task_type),
            payload={"quant_tutor_task": _jsonable_task(task)},
            metadata=metadata,
            data_mounts=self._translate_environment_mounts(env, task=task),
            sandbox_spec=self._sandbox_spec_for_task(task),
            agent_brief=DEFAULT_AGENT_BRIEF,
        )

    def get_business_task(self, task_id: str) -> QuantTutorTask:
        if task_id in self._task_cache:
            return self._task_cache[task_id]
        paths = self._index_task_paths()
        path = paths.get(task_id)
        if path is None:
            raise KeyError(f"Task not found: {task_id}")
        task = QuantTutorTask(**json.loads(path.read_text(encoding="utf-8")))
        self._task_cache[task_id] = task
        return task

    def required_bundle_fields(self) -> set[str]:
        return {
            "conversation",
            "tool_logs",
            "workspace_path",
            "result_dir",
            "persona",
        }

    def _index_task_paths(self) -> dict[str, Path]:
        if self._task_paths is None:
            tasks_dir = self.bench_root / "tasks"
            task_paths: list[Path] = []
            for layer in _TASK_LAYERS:
                layer_dir = tasks_dir / layer
                if layer_dir.is_dir():
                    task_paths.extend(layer_dir.rglob("*.json"))
            self._task_paths = {
                path.stem: path
                for path in sorted(task_paths)
                if path.stem.startswith(_TASK_PREFIXES)
            }
        return self._task_paths

    def _translate_environment_mounts(
        self,
        env: Any,
        *,
        task: QuantTutorTask,
    ) -> tuple[DataMount, ...]:
        if env is None:
            return ()

        mounts: list[DataMount] = []
        for raw_mount in getattr(env, "data_mounts", []) or []:
            payload = (
                raw_mount.model_dump(mode="json")
                if hasattr(raw_mount, "model_dump")
                else dict(raw_mount)
            )
            mounts.append(
                DataMount(
                    uri=str(payload["uri"]),
                    target_path=str(payload["target_path"]),
                    read_only=bool(payload.get("read_only", True)),
                )
            )

        for data_file in getattr(env, "data_files", []) or []:
            data_file_text = str(data_file)
            path = PurePosixPath(data_file_text)
            target_name = path.name or data_file_text.strip("/").replace("/", "_")
            source = self._resolve_data_file(data_file_text, task=task)
            uri = (
                f"file://{source.resolve().as_posix()}"
                if source is not None
                else self._remote_data_uri(data_file_text)
            )
            mounts.append(
                DataMount(
                    uri=uri,
                    target_path=f"/data/{target_name}",
                    read_only=True,
                )
            )
        return tuple(mounts)

    def _data_search_roots(self, *, task: QuantTutorTask) -> tuple[Path, ...]:
        data_root = self.bench_root / "data"
        normal_root = data_root / "hf_cache" / "normal"
        roots = [
            normal_root / "BDEX",
            normal_root / "A",
            self.bench_root / "runtime_assets" / "lean" / "data",
            data_root / "custom",
            data_root / "hf_cache",
            data_root,
        ]
        sandbox_spec = self._sandbox_spec_for_task(task)
        if sandbox_spec is not None and "lean" in sandbox_spec.image_uri:
            lean_root = self.bench_root / "runtime_assets" / "lean" / "data"
            roots = [lean_root, *[root for root in roots if root != lean_root]]
        return tuple(dict.fromkeys(root for root in roots))

    def _resolve_data_file(
        self,
        data_file: str,
        *,
        task: QuantTutorTask,
    ) -> Path | None:
        raw = data_file.strip()
        if not raw:
            return None
        direct = Path(raw).expanduser()
        if direct.is_absolute() and direct.is_file():
            return direct

        rel = Path(*PurePosixPath(raw).parts)
        rels = [rel]
        if rel.name and rel.name != raw:
            rels.append(Path(rel.name))

        for root in self._data_search_roots(task=task):
            for relative in rels:
                candidate = root / relative
                if candidate.is_file():
                    return candidate
        return None

    @staticmethod
    def _remote_data_uri(data_file: str) -> str:
        raw = data_file.strip().lstrip("/")
        name = PurePosixPath(raw).name or raw
        if raw.startswith("adversarial/") or name in {
            "malicious_backtest.py",
            "portfolio_data_poisoned.csv",
        }:
            subpath = f"A/{name}"
        elif name == "universe.json":
            subpath = name
        else:
            subpath = f"BDS/{name}"
        return (
            f"hf://{DATASET_REPO_ID}@{DATASET_REVISION}/{subpath}"
            "?repo_type=dataset"
        )

    def _sandbox_spec_for_task(self, task: QuantTutorTask) -> SandboxSpec | None:
        env = task.environment
        if env is not None:
            return SandboxSpec(
                image_uri=env.sandbox_image_uri,
                resource_limits=env.sandbox_resource_limits,
            )

        if _enum_value(getattr(task, "layer", "")) == "L0":
            return None

        category = _enum_value(task.category)
        if _enum_value(getattr(task, "layer", "")) in {"L1", "L2"}:
            image = (
                BENCH_IMAGE_V3_LEAN
                if category in _V3_LEAN_CATEGORIES
                else BENCH_IMAGE_V3
            )
        elif category in _CODE_CATEGORIES or task.requires_code:
            image = BENCH_IMAGE_V3_LEAN
        else:
            image = BENCH_IMAGE_V3
        return SandboxSpec(image_uri=image, resource_limits={})
