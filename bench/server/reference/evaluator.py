"""Reference Evaluator adapter for Layer 1 and Layer 2 tasks."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from eval.contracts.output import EvalOutput, TrackResult
from eval.contracts.schemas import QuantTutorTask, UserPersona
from eval.judge_reliability import build_judge_reliability_metadata
from eval.storage.score_store import allocate_score_run, load_index
from platform_api.contracts import (
    EvalItem,
    EvalSample,
    Evaluator,
    EvaluatorMetadata,
    Score,
)
from server.reference import knowledge_qa, l1_verifier


def _default_bench_root() -> Path:
    env_root = os.environ.get("QTB_BENCH_ROOT", "").strip()
    if env_root:
        return Path(env_root)
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if hasattr(value, "__dict__"):
        return {str(key): _jsonable(item) for key, item in vars(value).items()}
    return str(value)


def _task_from_item(item: EvalItem) -> QuantTutorTask:
    raw = item.payload.get("quant_tutor_task")
    if isinstance(raw, QuantTutorTask):
        return raw
    if isinstance(raw, Mapping):
        return QuantTutorTask(**raw)
    raise ValueError("EvalItem payload is missing quant_tutor_task")


def _persona_from_payload(payload: Mapping[str, Any]) -> UserPersona | None:
    raw = payload.get("persona")
    if isinstance(raw, UserPersona):
        return raw
    if isinstance(raw, Mapping):
        return UserPersona(**raw)
    return None


def _transcript_as_conversation(sample: EvalSample) -> list[dict[str, Any]]:
    return [
        {
            "role": message.role,
            "content": message.content,
            **({"ts": message.ts} if message.ts is not None else {}),
            **({"metadata": dict(message.metadata)} if message.metadata else {}),
        }
        for message in sample.transcript
    ]


def _latest_assistant_output(sample: EvalSample) -> str:
    for message in reversed(sample.transcript):
        if message.role == "assistant":
            return message.content
    value = sample.payload.get("actual_output")
    return str(value or "")


def _tool_logs(sample: EvalSample) -> list[Any]:
    return [_jsonable(log) for log in sample.tool_logs]


def _score_status(value: float | None) -> str:
    return "completed_scored" if value is not None else "completed_not_computable"


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def _task_layer(task: QuantTutorTask) -> str:
    return _enum_value(getattr(task, "layer", ""))


class ReferenceEvaluator(Evaluator):
    """Composite reference evaluator for platform plugin execution."""

    def __init__(
        self,
        bench_root: str | Path | None = None,
        eval_model: str | None = None,
    ) -> None:
        self.bench_root = Path(bench_root) if bench_root else _default_bench_root()
        self.eval_model = eval_model

    def configure(
        self,
        *,
        bench_root: str | Path | None = None,
        eval_model: str | None = None,
    ) -> None:
        if bench_root is not None:
            self.bench_root = Path(bench_root)
        if eval_model is not None:
            self.eval_model = eval_model

    def evaluate(self, item: EvalItem, sample: EvalSample) -> Score:
        task = _task_from_item(item)
        task_type = str(item.task_type or _enum_value(task.task_type))
        layer = _task_layer(task)
        eval_model = str(sample.payload.get("eval_model") or self.eval_model or "")

        if layer == "L0" or task_type in {"knowledge_qa", "l0"}:
            result = knowledge_qa.evaluate(
                question=task.question or task.description,
                reference_answer=task.reference_answer or "",
                actual_output=_latest_assistant_output(sample),
                context=task.context,
            )
            return self._score_single_track(
                track="knowledge_qa",
                result=result,
                sample=sample,
                eval_model=eval_model,
            )

        if layer == "L1" or task_type in {"agent_execution", "single_turn"}:
            result = l1_verifier.evaluate(
                task=task,
                actual_output=_latest_assistant_output(sample),
                workspace_path=str(sample.payload.get("workspace_path") or ""),
                eval_model=eval_model,
            )
            return self._score_single_track(
                track="layer1",
                result=result,
                sample=sample,
                eval_model=eval_model,
            )

        return self._score_layer2(
            item=item,
            sample=sample,
            task=task,
            eval_model=eval_model or None,
        )

    def metadata(self) -> EvaluatorMetadata:
        return EvaluatorMetadata(
            evaluator_id="reference",
            version="1.0",
            supported_tasks=frozenset(),
            required_bundle_fields=frozenset(
                {
                    "conversation",
                    "tool_logs",
                    "workspace_path",
                    "result_dir",
                    "persona",
                }
            ),
            score_schema={
                "value": "overall score in [0, 1]",
                "metrics": "per-track evaluator output",
            },
            capabilities=frozenset({"knowledge_qa", "layer1", "qr", "qp"}),
        )

    def _score_single_track(
        self,
        *,
        track: str,
        result: dict[str, Any],
        sample: EvalSample,
        eval_model: str | None,
    ) -> Score:
        value = result.get("score")
        score_value = float(value) if value is not None else None
        status = _score_status(score_value)
        summary = self._persist_single_track(
            track=track,
            result=result,
            sample=sample,
            eval_model=eval_model,
            score_value=score_value,
            status=status,
        )
        return Score(
            value=score_value,
            status=status,
            metrics={track: result, "summary": summary},
            reason=str(result.get("reason") or ""),
            evidence=tuple(str(item) for item in result.get("evidence", []) or ()),
        )

    def _persist_single_track(
        self,
        *,
        track: str,
        result: dict[str, Any],
        sample: EvalSample,
        eval_model: str | None,
        score_value: float | None,
        status: str,
    ) -> dict[str, Any]:
        result_dir_value = sample.payload.get("result_dir")
        if not result_dir_value:
            return {
                "score_status": status,
                "overall_score": score_value,
                track: score_value,
            }

        result_dir = Path(str(result_dir_value))
        result_dir.mkdir(parents=True, exist_ok=True)
        if not (result_dir / "run_state.json").exists():
            (result_dir / "run_state.json").write_text(
                json.dumps(
                    {
                        "task_id": sample.task_id,
                        "conversation": _transcript_as_conversation(sample),
                        "tool_logs": _tool_logs(sample),
                    },
                    indent=2,
                    default=str,
                ),
                encoding="utf-8",
            )

        requested_score_id = str(sample.payload.get("score_id") or sample.sample_id)
        score_id, created_at = self._prepare_score_run(
            result_dir=result_dir,
            requested_score_id=requested_score_id,
            eval_mode=track,
            eval_model=eval_model,
        )
        now = _utc_now()
        track_result = TrackResult(
            track=track,
            score=score_value,
            status="success" if score_value is not None else "not_computable",
            detail=result,
            blocking_missing=[] if score_value is not None else [result],
        )
        output = EvalOutput(
            score_id=score_id,
            score_status=status,
            qr=track_result,
            qp=None,
            overall_score=score_value,
            eval_mode=track,
            eval_model=eval_model,
            created_at=created_at,
            completed_at=now,
            duration_seconds=0.0,
            judge_reliability=build_judge_reliability_metadata(eval_model),
            blocking_missing=track_result.blocking_missing,
            preflight={},
        )
        from eval.core.coordinator import persist_eval_output

        return persist_eval_output(result_dir, output)

    def _score_layer2(
        self,
        *,
        item: EvalItem,
        sample: EvalSample,
        task: QuantTutorTask,
        eval_model: str | None,
    ) -> Score:
        persona = _persona_from_payload(sample.payload)
        if persona is None:
            raise ValueError("Layer 2 reference evaluation requires persona")

        result_dir_value = sample.payload.get("result_dir")
        result_dir = Path(str(result_dir_value)) if result_dir_value else None
        requested_score_id = str(sample.payload.get("score_id") or sample.sample_id)
        eval_mode = str(sample.payload.get("eval_mode") or "full")
        conversation = _transcript_as_conversation(sample)
        tool_logs = _tool_logs(sample)
        run_state = {
            "conversation": conversation,
            "tool_logs": tool_logs,
            "distractor_names": list(sample.payload.get("distractor_names") or []),
            "task_id": task.task_id,
            "persona_id": persona.persona_id,
            "workspace_path": sample.payload.get("workspace_path") or "",
        }

        if result_dir is None:
            from eval.core.coordinator import evaluate_tracks

            metrics = evaluate_tracks(
                task=task,
                persona=persona,
                workspace_path=str(run_state["workspace_path"] or "."),
                conversation=conversation,
                tool_logs=tool_logs,
                distractor_names=list(run_state["distractor_names"]),
                bench_root=str(self.bench_root),
                eval_model=eval_model or "",
                eval_mode=eval_mode,
            )
            value = metrics.get("overall_score")
            if value is None:
                from eval.core.scoring import compute_overall

                value = compute_overall(
                    {"score": metrics.get("quant_result")},
                    {"score": metrics.get("quant_process")},
                    eval_mode=eval_mode,
                )
                metrics["overall_score"] = value
            return Score(
                value=float(value) if value is not None else None,
                status="scored" if value is not None else "not_computable",
                metrics=metrics,
            )

        from server.storage.eval_writer import run_evaluation

        score_id, _created_at = self._prepare_score_run(
            result_dir=result_dir,
            requested_score_id=requested_score_id,
            eval_mode=eval_mode,
            eval_model=eval_model,
        )
        summary = run_evaluation(
            task=task,
            persona=persona,
            result_dir=result_dir,
            conversation=conversation,
            tool_logs=tool_logs,
            distractor_names=list(run_state["distractor_names"]),
            bench_root=str(self.bench_root),
            eval_model=eval_model or "",
            eval_mode=eval_mode,
            score_id=score_id,
        )
        value = summary.get("overall_score", summary.get("overall"))
        return Score(
            value=float(value) if value is not None else None,
            status=str(summary.get("score_status") or "scored"),
            metrics={"summary": summary},
        )

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
