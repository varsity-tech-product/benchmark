"""Plugin loader for TaskSuite, NPCProvider, and Evaluator triples."""

from __future__ import annotations

import importlib
import importlib.metadata
import json
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

from platform_api.contracts import Evaluator, NPCProvider, TaskSuite


class PluginLoadError(RuntimeError):
    """Raised when a platform plugin cannot be loaded."""


@dataclass(frozen=True)
class PluginBundle:
    """Loaded plugin implementation triple."""

    name: str
    task_suite: TaskSuite
    npc_provider: NPCProvider
    evaluator: Evaluator
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PluginSpec:
    """Import or object references for one plugin."""

    name: str
    task_suite: Any
    npc_provider: Any
    evaluator: Any
    metadata: Mapping[str, Any] = field(default_factory=dict)


def resolve_import(path: str) -> Any:
    """Resolve ``module:attribute`` import strings."""

    if ":" not in path:
        raise PluginLoadError(f"Import path must use module:attribute form: {path}")
    module_name, attr_path = path.split(":", 1)
    try:
        obj: Any = importlib.import_module(module_name)
    except Exception as exc:
        raise PluginLoadError(f"Failed to import module {module_name}: {exc}") from exc
    for attr in attr_path.split("."):
        try:
            obj = getattr(obj, attr)
        except AttributeError as exc:
            raise PluginLoadError(
                f"Import path {path} missing attribute {attr}"
            ) from exc
    return obj


class PluginLoader:
    """Load plugins from explicit specs, config files, or entry points."""

    DEFAULT_ENTRY_POINT_GROUP = "quantagentbench.plugins"

    def __init__(self, *, entry_point_group: str | None = None) -> None:
        self.entry_point_group = entry_point_group or self.DEFAULT_ENTRY_POINT_GROUP

    def load_spec(self, spec: PluginSpec | Mapping[str, Any]) -> PluginBundle:
        """Load one plugin from a spec object or mapping."""

        if isinstance(spec, Mapping):
            spec = PluginSpec(
                name=str(spec["name"]),
                task_suite=spec["task_suite"],
                npc_provider=spec["npc_provider"],
                evaluator=spec["evaluator"],
                metadata=spec.get("metadata", {}),
            )
        bundle = PluginBundle(
            name=spec.name,
            task_suite=self._materialize(spec.task_suite, TaskSuite, "task_suite"),
            npc_provider=self._materialize(
                spec.npc_provider, NPCProvider, "npc_provider"
            ),
            evaluator=self._materialize(spec.evaluator, Evaluator, "evaluator"),
            metadata=dict(spec.metadata),
        )
        return self._validate_bundle(bundle)

    def load_config(self, source: str | Path | Mapping[str, Any]) -> list[PluginBundle]:
        """Load plugins from a JSON/TOML config file or mapping."""

        config = self._read_config(source)
        entries = config.get("plugins")
        if entries is None:
            entries = [config]
        if not isinstance(entries, Iterable):
            raise PluginLoadError("Plugin config field 'plugins' must be iterable")
        return [self.load_spec(entry) for entry in entries]

    def load_entry_points(
        self,
        *,
        group: str | None = None,
        names: set[str] | None = None,
    ) -> list[PluginBundle]:
        """Load plugins from importlib.metadata entry points."""

        group = group or self.entry_point_group
        discovered = importlib.metadata.entry_points()
        if hasattr(discovered, "select"):
            entry_points = discovered.select(group=group)
        else:
            entry_points = discovered.get(group, [])  # pragma: no cover

        bundles: list[PluginBundle] = []
        for entry_point in entry_points:
            if names is not None and entry_point.name not in names:
                continue
            try:
                loaded = entry_point.load()
            except Exception as exc:
                raise PluginLoadError(
                    f"Failed to load entry point {entry_point.name}: {exc}"
                ) from exc
            bundles.append(self._normalize_loaded(entry_point.name, loaded))
        return bundles

    def load_many(
        self,
        *,
        specs: Iterable[PluginSpec | Mapping[str, Any]] = (),
        config_files: Iterable[str | Path] = (),
        include_entry_points: bool = False,
        entry_point_names: set[str] | None = None,
    ) -> list[PluginBundle]:
        """Load plugins from all configured sources."""

        bundles: list[PluginBundle] = []
        for spec in specs:
            bundles.append(self.load_spec(spec))
        for config_file in config_files:
            bundles.extend(self.load_config(config_file))
        if include_entry_points:
            bundles.extend(self.load_entry_points(names=entry_point_names))
        return bundles

    def _normalize_loaded(self, name: str, loaded: Any) -> PluginBundle:
        if isinstance(loaded, PluginBundle):
            return self._validate_bundle(loaded)
        if isinstance(loaded, Mapping):
            payload = dict(loaded)
            payload.setdefault("name", name)
            return self.load_spec(payload)
        if callable(loaded):
            try:
                produced = loaded()
            except TypeError:
                produced = loaded
            if produced is loaded:
                raise PluginLoadError(
                    f"Entry point {name} returned callable without plugin bundle"
                )
            return self._normalize_loaded(name, produced)
        raise PluginLoadError(f"Entry point {name} returned unsupported plugin value")

    def _materialize(self, raw: Any, expected_type: type, field_name: str) -> Any:
        obj = resolve_import(raw) if isinstance(raw, str) else raw
        if isinstance(obj, expected_type):
            return obj
        if isinstance(obj, type):
            try:
                obj = obj()
            except Exception as exc:
                raise PluginLoadError(
                    f"Failed to instantiate {field_name}: {exc}"
                ) from exc
        elif callable(obj):
            try:
                obj = obj()
            except Exception as exc:
                raise PluginLoadError(f"Failed to call {field_name}: {exc}") from exc
        if not isinstance(obj, expected_type):
            raise PluginLoadError(
                f"{field_name} must implement {expected_type.__name__}"
            )
        return obj

    def _validate_bundle(self, bundle: PluginBundle) -> PluginBundle:
        for field_name, value, expected in (
            ("task_suite", bundle.task_suite, TaskSuite),
            ("npc_provider", bundle.npc_provider, NPCProvider),
            ("evaluator", bundle.evaluator, Evaluator),
        ):
            if not isinstance(value, expected):
                raise PluginLoadError(f"{field_name} must implement {expected.__name__}")
        return bundle

    def _read_config(self, source: str | Path | Mapping[str, Any]) -> Mapping[str, Any]:
        if isinstance(source, Mapping):
            return source
        path = Path(source)
        try:
            if path.suffix.lower() == ".toml":
                with path.open("rb") as f:
                    return tomllib.load(f)
            return json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise PluginLoadError(f"Failed to read plugin config {path}: {exc}") from exc
        except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
            raise PluginLoadError(f"Failed to parse plugin config {path}: {exc}") from exc
