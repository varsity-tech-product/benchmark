"""Run bundle contract for QuantTutorBench.

A *run bundle* is the self-contained on-disk artifact a session produces:
it carries every input the evaluator needs to score the run later, off-line,
without touching the live server. See ``BUNDLE_SCHEMA.md`` for the full spec.

Layout::

    results/server/{task_id}/{persona_id}/{YYYYMMDD_HHMMSS}_{session_id[:8]}/
        manifest.json       <- this contract; pin version + list artifacts
        run_state.json      <- conversation + tool_logs + metadata
        run_state.md        <- human-readable rendering
        agent_files/        <- workspace snapshot at session completion

This module owns three things:

- ``BUNDLE_SCHEMA_VERSION`` — bumped on any breaking change to the layout
  or to ``run_state.json`` field semantics.
- ``write_manifest`` — called by the producer (``result_writer``) after the
  bundle's other files land.
- ``load_bundle`` — called by the consumer (offline evaluator) to reconstruct
  the input set ``server.eval.pipeline.evaluate_task`` requires.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

BUNDLE_SCHEMA_VERSION = "1.0.0"

MANIFEST_FILENAME = "manifest.json"
RUN_STATE_FILENAME = "run_state.json"
RUN_STATE_MD_FILENAME = "run_state.md"
AGENT_FILES_DIRNAME = "agent_files"


@dataclass(frozen=True)
class LoadedBundle:
    """Materialized bundle ready to feed ``evaluate_task``.

    Holds the manifest plus the four inputs ``evaluate_task`` reads from
    the bundle directly (``task`` and ``persona`` are looked up externally
    by ``task_id`` / ``persona_id`` and are not part of the bundle).

    ``tool_logs`` are rehydrated to ``ToolCallLog`` dataclass instances so
    downstream evaluators can use the attribute access (``log.name``,
    ``log.args``, ``log.result`` …) the in-session code path relies on.
    """

    bundle_dir: Path
    manifest: dict
    run_state: dict
    conversation: list[dict]
    tool_logs: list  # list[ToolCallLog]; deferred type to keep this module import-light
    distractor_names: list[str]
    workspace_path: str

    @property
    def task_id(self) -> str:
        return self.run_state.get("task_id", "")

    @property
    def persona_id(self) -> str:
        return self.run_state.get("persona_id", "")

    @property
    def session_id(self) -> str:
        return self.run_state.get("session_id", "")


def write_manifest(bundle_dir: Path, run_state: dict) -> Path:
    """Write ``manifest.json`` at the root of a bundle directory.

    The manifest is the bundle's contract: schema version + identity fields
    + a flat list of artifacts that consumers may rely on. Anything not
    listed here is implementation detail and may move without a version bump.
    """
    bundle_dir = Path(bundle_dir)
    artifacts: dict[str, str] = {"run_state": RUN_STATE_FILENAME}

    if (bundle_dir / RUN_STATE_MD_FILENAME).exists():
        artifacts["run_state_md"] = RUN_STATE_MD_FILENAME
    if (bundle_dir / AGENT_FILES_DIRNAME).is_dir():
        artifacts["agent_files"] = AGENT_FILES_DIRNAME + "/"

    manifest = {
        "bundle_schema_version": BUNDLE_SCHEMA_VERSION,
        "created_at": datetime.now().isoformat(),
        "task_id": run_state.get("task_id", ""),
        "persona_id": run_state.get("persona_id", ""),
        "session_id": run_state.get("session_id", ""),
        "run_id": run_state.get("run_id", ""),
        "session_status": run_state.get("session_status", ""),
        "termination_reason": run_state.get("termination_reason"),
        "artifacts": artifacts,
    }
    path = bundle_dir / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return path


def load_bundle(bundle_dir: Path | str) -> LoadedBundle:
    """Load a bundle from disk into the input set the evaluator consumes.

    Raises ``FileNotFoundError`` if the directory or required artifacts
    are missing, and ``ValueError`` if the manifest's schema version is
    not supported by this code.
    """
    bundle_dir = Path(bundle_dir)
    if not bundle_dir.is_dir():
        raise FileNotFoundError(f"Bundle directory not found: {bundle_dir}")

    manifest_path = bundle_dir / MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Bundle missing {MANIFEST_FILENAME}: {bundle_dir}"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    _check_schema(manifest.get("bundle_schema_version"))

    run_state_path = bundle_dir / RUN_STATE_FILENAME
    if not run_state_path.is_file():
        raise FileNotFoundError(
            f"Bundle missing {RUN_STATE_FILENAME}: {bundle_dir}"
        )
    run_state = json.loads(run_state_path.read_text(encoding="utf-8"))

    _check_artifacts_present(bundle_dir, manifest)

    workspace_path = bundle_dir / AGENT_FILES_DIRNAME

    return LoadedBundle(
        bundle_dir=bundle_dir,
        manifest=manifest,
        run_state=run_state,
        conversation=list(run_state.get("conversation", [])),
        tool_logs=_rehydrate_tool_logs(run_state.get("tool_logs", [])),
        distractor_names=list(run_state.get("distractor_names", [])),
        workspace_path=str(workspace_path),
    )


def _check_schema(version: str | None) -> None:
    if version is None:
        raise ValueError("Bundle manifest missing 'bundle_schema_version'")
    major = version.split(".", 1)[0]
    expected_major = BUNDLE_SCHEMA_VERSION.split(".", 1)[0]
    if major != expected_major:
        raise ValueError(
            f"Unsupported bundle schema {version}; "
            f"this build expects {expected_major}.x"
        )


def _check_artifacts_present(bundle_dir: Path, manifest: dict) -> None:
    """Every artifact the manifest declares must actually exist on disk.

    A bundle that lists ``agent_files/`` but lost it during an interrupted
    copy or archive transfer would otherwise score zero on code-eval
    silently — fail loudly at load instead.
    """
    for key, rel in (manifest.get("artifacts") or {}).items():
        target = bundle_dir / rel.rstrip("/")
        if not target.exists():
            raise FileNotFoundError(
                f"Bundle manifest declares artifact '{key}' at {rel!r} "
                f"but it is missing from {bundle_dir}"
            )


def _rehydrate_tool_logs(raw_logs: list) -> list:
    """Reconstruct ``ToolCallLog`` instances from the JSON-serialized form.

    Live sessions feed the evaluator dataclass instances; bundle JSON
    decodes to plain dicts. The evaluator path uses attribute access
    (``log.name``, ``log.args``, …), so dicts would raise ``AttributeError``
    at the first sub-evaluator. Rehydrate here so callers get a uniform
    shape regardless of source.
    """
    from server.core.proxy import ToolCallLog

    out: list = []
    for entry in raw_logs:
        if isinstance(entry, ToolCallLog):
            out.append(entry)
            continue
        if not isinstance(entry, dict):
            out.append(entry)
            continue
        out.append(
            ToolCallLog(
                name=entry.get("name", ""),
                args=entry.get("args") or {},
                call_id=entry.get("call_id", ""),
                result=entry.get("result", ""),
                timestamp=float(entry.get("timestamp", 0.0) or 0.0),
                duration_ms=float(entry.get("duration_ms", 0.0) or 0.0),
                success=bool(entry.get("success", True)),
                turn_index=int(entry.get("turn_index", 0) or 0),
                output_files=list(entry.get("output_files") or []),
            )
        )
    return out
