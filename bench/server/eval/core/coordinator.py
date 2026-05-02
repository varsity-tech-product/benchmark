"""Coordinator for the v6 evaluation pipeline."""

from __future__ import annotations

import concurrent.futures
import json
import logging
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from server.eval.contracts.output import EvalOutput, TrackResult
from server.eval.contracts.request import EvalRequest, resolve_result_dir
from server.eval.core.preflight import PreflightReport, run_preflight
from server.eval.judge_reliability import build_judge_reliability_metadata
from server.eval.inputs.enrichment import enrich_conversation_with_tools
from server.schemas import QuantTutorTask, StudentPersona
from server.storage.score_store import (
    summarize_score,
    update_score_run,
    write_score_files,
)

log = logging.getLogger(__name__)
INTERRUPT_GRACE_SECONDS = 5.0


def load_run_state(result_dir: Path) -> dict[str, Any]:
    path = Path(result_dir) / "run_state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def load_task_by_id(bench_root: Path, task_id: str | None):
    if not task_id:
        return None
    for json_path in (Path(bench_root) / "tasks").rglob(f"{task_id}.json"):
        return QuantTutorTask(**json.loads(json_path.read_text(encoding="utf-8")))
    return None


def load_persona_by_id(bench_root: Path, persona_id: str | None):
    if not persona_id:
        return None
    for json_path in (Path(bench_root) / "personas").rglob(f"{persona_id}.json"):
        return StudentPersona(**json.loads(json_path.read_text(encoding="utf-8")))
    return None


def coerce_tool_logs(tool_logs: list[Any] | None) -> list[Any]:
    out: list[Any] = []
    for log_item in tool_logs or []:
        if not isinstance(log_item, dict):
            out.append(log_item)
            continue
        data = dict(log_item)
        data.setdefault("args", {})
        data.setdefault("result", "")
        data.setdefault("success", True)
        data.setdefault("turn_index", 0)
        data.setdefault("duration_ms", 0.0)
        out.append(SimpleNamespace(**data))
    return out


def _mode_includes(eval_mode: str, track: str) -> bool:
    return eval_mode == "full" or eval_mode == track


def _check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise KeyboardInterrupt("Evaluation interrupted")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cost_by_model(payload: dict[str, Any] | None) -> dict[str, float]:
    if not isinstance(payload, dict):
        return {}
    by_model = (
        payload.get("_eval_cost_by_model") or payload.get("eval_cost_by_model") or {}
    )
    if not isinstance(by_model, dict):
        return {}
    out: dict[str, float] = {}
    for model, cost in by_model.items():
        try:
            out[str(model)] = round(float(cost), 6)
        except (TypeError, ValueError):
            continue
    return out


def _stage_costs_from_tracks(
    *tracks: TrackResult | None,
) -> dict[str, dict[str, float]]:
    stage_costs: dict[str, dict[str, float]] = {}

    def add(stage: str, payload: dict[str, Any] | None) -> None:
        clean = _cost_by_model(payload)
        if clean:
            stage_costs[stage] = clean

    qr, qp = tracks
    if qr and isinstance(qr.detail, dict):
        add("QR Result Judge", qr.detail.get("result_judge"))
    if qp and isinstance(qp.detail, dict):
        add("QP Task Planning", qp.detail.get("task_planning"))
        add("QP Problem Solving", qp.detail.get("problem_solving"))
        if not any(stage.startswith("QP ") for stage in stage_costs):
            add("QP Process", qp.detail)
    return stage_costs


def build_cost_payload(output: EvalOutput) -> dict[str, Any]:
    tracks = [t for t in (output.qr, output.qp) if t is not None]
    by_track = {t.track: round(t.eval_cost, 6) for t in tracks}
    by_model: dict[str, float] = {}
    for track in tracks:
        for model, cost in track.eval_cost_by_model.items():
            by_model[model] = round(by_model.get(model, 0.0) + cost, 6)
    return {
        "version": "2.0",
        "score_id": output.score_id,
        "eval_cost_usd": round(sum(by_track.values()), 6),
        "eval_cost_by_track": by_track,
        "eval_cost_by_model": by_model,
        "eval_cost_by_stage_model": _stage_costs_from_tracks(
            output.qr,
            output.qp,
        ),
    }


def persist_eval_output(result_dir: Path, output: EvalOutput) -> dict[str, Any]:
    score_data = output.to_dict()
    cost_data = build_cost_payload(output)
    write_score_files(Path(result_dir), output.score_id, score_data, cost_data)
    update_score_run(
        Path(result_dir),
        output.score_id,
        status=output.score_status,
        overall_score=output.overall_score,
        completed_at=output.completed_at,
        error=output.error,
    )
    return summarize_score(score_data, cost_data)


class EvalCoordinator:
    """Run preflight, independent tracks, overall scoring, and persistence."""

    def __init__(self, *, bench_root: str | Path):
        self.bench_root = Path(bench_root)

    def run(
        self,
        *,
        request: EvalRequest,
        result_dir: Path,
        score_id: str,
        task: Any,
        persona: Any,
        run_state: dict[str, Any],
        created_at: str | None = None,
        conversation: list[dict[str, Any]] | None = None,
        tool_logs: list[Any] | None = None,
        distractor_names: list[str] | None = None,
        cancel_event: threading.Event | None = None,
        persist: bool = True,
    ) -> EvalOutput:
        started = time.time()
        result_dir = Path(result_dir)
        conversation = (
            conversation
            if conversation is not None
            else run_state.get("conversation", [])
        )
        raw_tool_logs = (
            tool_logs if tool_logs is not None else run_state.get("tool_logs", [])
        )
        raw_tool_logs = raw_tool_logs or []
        tool_logs = coerce_tool_logs(raw_tool_logs)
        workspace_path = str(
            run_state.get("workspace_path") or (result_dir / "agent_files")
        )
        distractor_names = (
            distractor_names
            if distractor_names is not None
            else list(run_state.get("distractor_names") or [])
        )

        preflight = run_preflight(
            request=request,
            result_dir=result_dir,
            run_state=run_state,
            task=task,
            persona=persona,
            conversation=conversation,
            tool_logs=raw_tool_logs,
            bench_root=self.bench_root,
        )
        if not preflight.ok:
            output = self._make_output(
                score_id=score_id,
                request=request,
                created_at=created_at,
                started=started,
                status="failed",
                error="; ".join(err["message"] for err in preflight.hard_errors),
                blocking_missing=preflight.hard_errors,
                preflight=preflight,
            )
            if persist:
                persist_eval_output(result_dir, output)
            return output

        _check_cancel(cancel_event)
        enriched = enrich_conversation_with_tools(conversation, tool_logs, mode="full")
        reference = self._load_reference(task, persona)
        track_results: dict[str, TrackResult] = {}
        track_cancel_events = {
            "qr": threading.Event(),
            "qp": threading.Event(),
        }

        def cancel_all() -> None:
            for event in track_cancel_events.values():
                event.set()

        track_fns: dict[str, Any] = {}
        if _mode_includes(request.eval_mode, "qr"):
            from server.eval.tracks import qr

            track_fns["qr"] = lambda: qr.evaluate(
                task=task,
                bench_root=self.bench_root,
                workspace_path=workspace_path,
                conversation=conversation,
                tool_logs=tool_logs,
                eval_model=request.eval_model,
                reference=reference,
                preflight=preflight,
                cancel_event=track_cancel_events["qr"],
            )
        if _mode_includes(request.eval_mode, "qp"):
            from server.eval.tracks import qp

            track_fns["qp"] = lambda: qp.evaluate(
                task=task,
                conversation=conversation,
                enriched_conversation=enriched,
                tool_logs=tool_logs,
                distractor_names=distractor_names,
                eval_model=request.eval_model,
                preflight=preflight,
                cancel_event=track_cancel_events["qp"],
            )
        def run_one(track: str, fn) -> TrackResult:
            track_started = time.time()
            result = fn()
            result.duration_seconds = round(time.time() - track_started, 2)
            return result

        if cancel_event is not None and cancel_event.is_set():
            output = self._output_from_tracks(
                score_id=score_id,
                request=request,
                created_at=created_at,
                started=started,
                preflight=preflight,
                interrupted=True,
                error="Evaluation interrupted",
            )
            if persist:
                persist_eval_output(result_dir, output)
            return output

        pool = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(track_fns)))
        interrupted = False
        future_to_track: dict[concurrent.futures.Future, str] = {}

        def collect_done(
            done_futures: set[concurrent.futures.Future],
            *,
            during_interrupt: bool = False,
        ) -> None:
            for fut in done_futures:
                track = future_to_track[fut]
                if track in track_results:
                    continue
                try:
                    track_results[track] = fut.result()
                except KeyboardInterrupt:
                    if not during_interrupt:
                        raise
                except Exception as exc:  # noqa: BLE001 - isolate failed track
                    log.exception("Evaluation track %s failed", track)
                    track_results[track] = self._track_failure(track, str(exc))

        try:
            future_to_track = {
                pool.submit(run_one, track, fn): track
                for track, fn in track_fns.items()
            }
            pending = set(future_to_track)
            while pending:
                _check_cancel(cancel_event)
                done, pending = concurrent.futures.wait(
                    pending,
                    timeout=0.2,
                    return_when=concurrent.futures.FIRST_COMPLETED,
                )
                collect_done(done)
        except KeyboardInterrupt as exc:
            if "Second SIGINT" in str(exc):
                interrupted = True
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            interrupted = True
            cancel_all()
            print(
                "Interrupted. Waiting for in-flight LLM calls (5s grace)...",
                file=sys.stderr,
            )
            done, _pending = concurrent.futures.wait(
                set(future_to_track)
                - {
                    fut
                    for fut, track in future_to_track.items()
                    if track in track_results
                },
                timeout=INTERRUPT_GRACE_SECONDS,
                return_when=concurrent.futures.ALL_COMPLETED,
            )
            collect_done(done, during_interrupt=True)
            output = self._output_from_tracks(
                score_id=score_id,
                request=request,
                created_at=created_at,
                started=started,
                preflight=preflight,
                qr=track_results.get("qr"),
                qp=track_results.get("qp"),
                interrupted=True,
                error="Evaluation interrupted",
            )
            if persist:
                persist_eval_output(result_dir, output)
            return output
        finally:
            pool.shutdown(wait=not interrupted, cancel_futures=interrupted)

        output = self._output_from_tracks(
            score_id=score_id,
            request=request,
            created_at=created_at,
            started=started,
            preflight=preflight,
            qr=track_results.get("qr"),
            qp=track_results.get("qp"),
        )
        if persist:
            persist_eval_output(result_dir, output)
        return output

    def _make_output(
        self,
        *,
        score_id: str,
        request: EvalRequest,
        created_at: str | None,
        started: float,
        status: str,
        error: str | None = None,
        blocking_missing: list[dict[str, Any]] | None = None,
        preflight: PreflightReport | None = None,
    ) -> EvalOutput:
        completed_at = _utc_now()
        return EvalOutput(
            score_id=score_id,
            score_status=status,
            qr=None,
            qp=None,
            overall_score=None,
            eval_mode=request.eval_mode,
            eval_model=request.eval_model,
            created_at=created_at or completed_at,
            completed_at=completed_at,
            duration_seconds=time.time() - started,
            judge_reliability=build_judge_reliability_metadata(request.eval_model),
            interrupted=(status == "interrupted"),
            blocking_missing=blocking_missing or [],
            error=error,
            preflight=preflight.to_dict() if preflight else {},
        )

    def _output_from_tracks(
        self,
        *,
        score_id: str,
        request: EvalRequest,
        created_at: str | None,
        started: float,
        preflight: PreflightReport,
        qr: TrackResult | None = None,
        qp: TrackResult | None = None,
        interrupted: bool = False,
        error: str | None = None,
    ) -> EvalOutput:
        from server.eval.core.scoring import compute_overall

        completed_at = _utc_now()
        overall = (
            None
            if interrupted
            else compute_overall(
                qr=qr,
                qp=qp,
                eval_mode=request.eval_mode,
            )
        )
        blockers = [
            item
            for track in (qr, qp)
            if track is not None
            for item in track.blocking_missing
        ]
        if interrupted:
            status = "interrupted"
        else:
            status = (
                "completed_scored"
                if overall is not None
                else "completed_not_computable"
            )
        return EvalOutput(
            score_id=score_id,
            score_status=status,
            qr=qr,
            qp=qp,
            overall_score=overall,
            eval_mode=request.eval_mode,
            eval_model=request.eval_model,
            created_at=created_at or completed_at,
            completed_at=completed_at,
            duration_seconds=time.time() - started,
            judge_reliability=build_judge_reliability_metadata(request.eval_model),
            interrupted=interrupted,
            blocking_missing=blockers,
            error=error,
            preflight=preflight.to_dict(),
        )

    @staticmethod
    def _track_failure(track: str, error: str) -> TrackResult:
        return TrackResult(
            track=track,
            score=None,
            status="failed",
            detail={},
            blocking_missing=[{"track": track, "dimension": track, "reason": error}],
            error=error,
        )

    @staticmethod
    def _load_reference(task: Any, persona: Any) -> dict[str, Any] | None:
        try:
            from server.eval.inputs.reference_store import ReferenceStore

            return ReferenceStore().load(task.task_id, persona.persona_id)
        except Exception:
            return None


def evaluate_tracks(
    *,
    task: Any,
    persona: Any,
    workspace_path: str,
    conversation: list[dict[str, Any]],
    tool_logs: list[Any],
    distractor_names: list[str],
    bench_root: str,
    eval_model: str,
    cancel_event=None,
    eval_mode: str = "full",
) -> dict[str, Any]:
    """Compatibility hook for old callers that only need raw eval_results."""

    result_dir = Path(workspace_path).parent
    request = EvalRequest(
        session_id="legacy",
        eval_mode=eval_mode,
        eval_model=eval_model,
    )
    run_state = {
        "conversation": conversation,
        "tool_logs": [
            vars(log) if hasattr(log, "__dict__") else log for log in (tool_logs or [])
        ],
        "distractor_names": distractor_names,
        "task_id": getattr(task, "task_id", ""),
        "persona_id": getattr(persona, "persona_id", ""),
        "workspace_path": workspace_path,
    }

    def _run_with_dir(eval_result_dir: Path) -> EvalOutput:
        return EvalCoordinator(bench_root=bench_root).run(
            request=request,
            result_dir=eval_result_dir,
            score_id="compatibility",
            task=task,
            persona=persona,
            run_state=run_state,
            created_at=_utc_now(),
            conversation=conversation,
            tool_logs=tool_logs,
            distractor_names=distractor_names,
            cancel_event=cancel_event,
            persist=False,
        )

    if (result_dir / "run_state.json").exists():
        result = _run_with_dir(result_dir)
    else:
        with tempfile.TemporaryDirectory(prefix="qtb_eval_") as tmp:
            temp_result_dir = Path(tmp)
            (temp_result_dir / "run_state.json").write_text(
                json.dumps(run_state, indent=2, default=str),
                encoding="utf-8",
            )
            result = _run_with_dir(temp_result_dir)

    if result.interrupted:
        raise KeyboardInterrupt(result.error or "Evaluation interrupted")
    if result.score_status == "failed":
        raise RuntimeError(result.error or "Evaluation preflight failed")
    return raw_results_from_output(result)


def raw_results_from_output(output: EvalOutput) -> dict[str, Any]:
    """Adapt structured output to the older flat eval_results shape."""

    out: dict[str, Any] = {"preflight": output.preflight or {}}
    if output.qr:
        qr_detail = output.qr.detail or {}
        programmatic = qr_detail.get("programmatic") or {}
        out["quant_result"] = output.qr.score
        out["quant_result_programmatic"] = programmatic.get("score")
        out["eval_script_detail"] = programmatic.get("detail", programmatic)
        out["code_eval"] = qr_detail.get("code_eval") or {}
        out["result_judge"] = qr_detail.get("result_judge") or {}
        out["qr_blend_weights"] = qr_detail.get("blend_weights")
        if output.qr.error:
            out["qr_track_error"] = output.qr.error
    if output.qp:
        out["quant_process"] = output.qp.score
        out["process_metrics"] = output.qp.detail or {}
        if output.qp.error:
            out["qp_track_error"] = output.qp.error
    return out


def resolve_eval_context(
    *,
    request: EvalRequest,
    bench_root: str | Path,
    results_root: str | Path,
) -> tuple[Path, Any, Any, dict[str, Any]]:
    """Resolve session id into the files and objects needed by the coordinator."""

    bench_root = Path(bench_root)
    result_dir = resolve_result_dir(request.session_id, Path(results_root))
    run_state = load_run_state(result_dir)
    task = load_task_by_id(bench_root, run_state.get("task_id"))
    persona = load_persona_by_id(bench_root, run_state.get("persona_id"))
    return result_dir, task, persona, run_state
