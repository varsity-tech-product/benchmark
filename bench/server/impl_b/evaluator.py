"""Programmatic-only Impl B evaluator."""

from __future__ import annotations

import base64
import shutil
import tempfile
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from eval.contracts.output import EvalOutput, TrackResult
from eval.programmatic.l1_verifier import verify_l1
from eval.storage.score_store import allocate_score_run, load_index
from platform_api.contracts import (
    EvalItem,
    EvalSample,
    Evaluator,
    EvaluatorMetadata,
    FileArtifact,
    Score,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump(mode="json"))
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return str(value)


class ProgrammaticEvaluator(Evaluator):
    """Score Impl B outputs with the existing structured L1 verifier."""

    def __init__(self, bench_root: str | Path | None = None) -> None:
        self.bench_root = Path(bench_root) if bench_root else None

    def configure(
        self,
        *,
        bench_root: str | Path | None = None,
        eval_model: str | None = None,
    ) -> None:
        if bench_root is not None:
            self.bench_root = Path(bench_root)

    def evaluate(self, item: EvalItem, sample: EvalSample) -> Score:
        expected_outputs = item.payload.get("expected_outputs")
        if not isinstance(expected_outputs, list) or not expected_outputs:
            return Score(
                value=None,
                status="completed_not_computable",
                reason="Impl B task is missing expected_outputs",
                metrics={"programmatic": {"error": "expected_outputs missing"}},
            )

        workspace_path = str(sample.payload.get("workspace_path") or "").strip()
        temp_dir: tempfile.TemporaryDirectory[str] | None = None
        if not workspace_path and sample.files:
            temp_dir = tempfile.TemporaryDirectory(prefix="impl_b_eval_")
            workspace_path = temp_dir.name
            self._materialize_files(Path(workspace_path), sample.files)

        try:
            if not workspace_path:
                result = {
                    "score": None,
                    "error": "workspace_path missing",
                    "n_total": len(expected_outputs),
                    "n_passed": 0,
                    "per_spec": [],
                }
            else:
                result = verify_l1(workspace_path, expected_outputs)
        finally:
            if temp_dir is not None:
                temp_dir.cleanup()

        raw_score = result.get("score")
        score_value = (
            float(raw_score) if isinstance(raw_score, (int, float)) else None
        )
        status = (
            "completed_scored"
            if score_value is not None
            else "completed_not_computable"
        )
        summary = self._persist_if_requested(
            result=result,
            sample=sample,
            score_value=score_value,
            status=status,
        )
        metrics = {
            "programmatic": result,
            "summary": summary
            or {
                "score_status": status,
                "overall_score": score_value,
                "quant_result": score_value,
                "quant_process": None,
            },
        }
        return Score(
            value=score_value,
            status=status,
            metrics=metrics,
            reason=str(result.get("error") or ""),
            evidence=tuple(
                str(spec.get("path"))
                for spec in expected_outputs
                if isinstance(spec, dict) and spec.get("path")
            ),
            telemetry={"llm_judge_used": False},
        )

    def metadata(self) -> EvaluatorMetadata:
        return EvaluatorMetadata(
            evaluator_id="impl_b_programmatic",
            version="1.0",
            supported_tasks=frozenset(),
            required_bundle_fields=frozenset({"workspace_path", "expected_outputs"}),
            score_schema={
                "value": "fraction of expected output specs satisfied in [0, 1]",
                "metrics.programmatic": "raw l1_verifier output",
            },
            capabilities=frozenset({"programmatic_l1", "no_llm_judge"}),
            metadata={"llm_judge_used": False},
        )

    def _persist_if_requested(
        self,
        *,
        result: dict[str, Any],
        sample: EvalSample,
        score_value: float | None,
        status: str,
    ) -> dict[str, Any] | None:
        result_dir_value = sample.payload.get("result_dir")
        if not result_dir_value:
            return None

        result_dir = Path(str(result_dir_value))
        result_dir.mkdir(parents=True, exist_ok=True)
        score_id, created_at = self._prepare_score_run(
            result_dir=result_dir,
            requested_score_id=str(sample.payload.get("score_id") or sample.sample_id),
            eval_mode=str(sample.payload.get("eval_mode") or "programmatic"),
            eval_model=(
                str(sample.payload.get("eval_model"))
                if sample.payload.get("eval_model") is not None
                else None
            ),
        )
        now = _utc_now()
        qr = TrackResult(
            track="qr",
            score=score_value,
            status="success" if score_value is not None else "not_computable",
            detail={"programmatic": result},
            blocking_missing=[] if score_value is not None else [result],
        )
        output = EvalOutput(
            score_id=score_id,
            score_status=status,
            qr=qr,
            qp=None,
            overall_score=score_value,
            eval_mode=str(sample.payload.get("eval_mode") or "programmatic"),
            eval_model=(
                str(sample.payload.get("eval_model"))
                if sample.payload.get("eval_model") is not None
                else None
            ),
            created_at=created_at,
            completed_at=now,
            duration_seconds=0.0,
            judge_reliability={"mode": "programmatic_only", "llm_judge_used": False},
            blocking_missing=[] if score_value is not None else [result],
            preflight={},
        )
        from eval.core.coordinator import persist_eval_output

        return persist_eval_output(result_dir, output)

    @staticmethod
    def _prepare_score_run(
        *,
        result_dir: Path,
        requested_score_id: str,
        eval_mode: str,
        eval_model: str | None,
    ) -> tuple[str, str]:
        try:
            index = load_index(result_dir)
            entry = next(
                (
                    item
                    for item in index.get("scores", [])
                    if item.get("score_id") == requested_score_id
                ),
                {},
            )
            if entry:
                return (
                    str(entry.get("score_id")),
                    str(entry.get("created_at") or _utc_now()),
                )
            run, _created = allocate_score_run(
                result_dir,
                eval_mode=eval_mode,
                eval_model=eval_model,
            )
            return run.score_id, run.created_at
        except Exception:
            return requested_score_id, _utc_now()

    @staticmethod
    def _materialize_files(
        workspace: Path,
        files: Mapping[str, FileArtifact],
    ) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        for artifact in files.values():
            rel = Path(artifact.path)
            if rel.is_absolute() or ".." in rel.parts:
                continue
            target = workspace / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            content = artifact.content
            encoding = str(artifact.metadata.get("encoding") or "")
            if isinstance(content, bytes):
                target.write_bytes(content)
            elif encoding == "base64":
                target.write_bytes(base64.b64decode(str(content or "")))
            elif content is not None:
                target.write_text(str(content), encoding="utf-8")
            elif artifact.path:
                source = Path(artifact.path)
                if source.is_file():
                    shutil.copyfile(source, target)
