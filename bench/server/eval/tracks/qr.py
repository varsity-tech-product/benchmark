"""QR track: programmatic result checks, code eval, LLM result judge, blending."""

from __future__ import annotations

import importlib.util
import inspect
import threading
from pathlib import Path
from typing import Any

from server.eval.contracts.output import TrackResult
from server.eval.tracks.common import (
    check_cancel,
    cost_by_model_from,
    eval_cost_from,
    preflight_blockers,
    track_status,
)


def _category_value(task: Any) -> str:
    category = getattr(task, "category", "")
    return getattr(category, "value", category) or ""


def _programmatic_eval(
    *,
    task: Any,
    bench_root: Path,
    workspace_path: str,
    conversation: list[dict[str, Any]],
    tool_logs: list[Any],
) -> tuple[dict[str, Any], str | None]:
    gt = getattr(task, "ground_truth", None)
    quant_validation = getattr(gt, "quant_validation", None) if gt else None
    eval_script = getattr(quant_validation, "eval_script", None)
    if not eval_script:
        return (
            {
                "score": None,
                "status": "skipped",
                "required_for_track_score": False,
                "skip_reason": "quant_validation_missing",
            },
            None,
        )

    script_path = bench_root / eval_script
    if not script_path.exists():
        return (
            {
                "score": None,
                "status": "failed",
                "required_for_track_score": True,
                "error": f"eval_script not found: {script_path}",
            },
            f"eval_script not found: {script_path}",
        )

    try:
        spec = importlib.util.spec_from_file_location("eval_module", str(script_path))
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Could not load eval_script: {script_path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        sig = inspect.signature(module.evaluate)
        kwargs: dict[str, Any] = {}
        if "data_files" in sig.parameters:
            kwargs["data_files"] = (
                task.environment.data_files if task.environment else []
            )
        result = module.evaluate(workspace_path, tool_logs, conversation, **kwargs)
        if not isinstance(result, dict):
            raise RuntimeError("eval_script.evaluate() must return a dict")
        result.setdefault("status", "success")
        result.setdefault("required_for_track_score", True)
        return result, None
    except Exception as exc:  # noqa: BLE001 - surface as blocking dimension failure
        return (
            {
                "score": None,
                "status": "failed",
                "required_for_track_score": True,
                "error": str(exc),
            },
            str(exc),
        )


def _code_eval(
    *,
    task: Any,
    workspace_path: str,
    tool_logs: list[Any],
    reference: dict[str, Any] | None,
) -> tuple[dict[str, Any], str | None]:
    if reference is None:
        return (
            {
                "score": None,
                "status": "skipped",
                "applicable": False,
                "required_for_track_score": False,
                "skip_reason": "reference_unavailable",
                "reason": "No reference data is available; code evaluation skipped",
            },
            None,
        )
    try:
        from server.eval.programmatic.code_eval import evaluate_code_combined

        sandbox_img = task.environment.sandbox_image if task.environment else ""
        result = evaluate_code_combined(
            workspace_path=workspace_path,
            tool_logs=tool_logs,
            reference=reference,
            task_requires_code=task.requires_code,
            is_lean_task="lean" in sandbox_img,
        )
        result.setdefault("status", "success")
        result.setdefault("required_for_track_score", True)
        return result, None
    except Exception as exc:  # noqa: BLE001 - code eval failure blocks QR track score
        return (
            {
                "score": None,
                "status": "failed",
                "applicable": True,
                "required_for_track_score": True,
                "error": str(exc),
            },
            str(exc),
        )


def _result_judge(
    *,
    task: Any,
    workspace_path: str,
    tool_logs: list[Any],
    conversation: list[dict[str, Any]],
    eval_model: str | None,
    reference: dict[str, Any] | None,
    cancel_event: threading.Event | None,
) -> dict[str, Any]:
    from server.eval.judges.result_judge import evaluate_result_quality

    try:
        result = evaluate_result_quality(
            task_description=task.description,
            category=_category_value(task),
            workspace_path=workspace_path,
            tool_logs=tool_logs,
            conversation=conversation,
            model=eval_model,
            reference=reference,
            required_capabilities=(
                task.ground_truth.required_capabilities if task.ground_truth else None
            ),
            abort_event=cancel_event,
        )
        result.setdefault(
            "status", "success" if result.get("score") is not None else "failed"
        )
        result.setdefault("required_for_track_score", True)
        return result
    except Exception as exc:  # noqa: BLE001 - QR can still report other components
        return {
            "score": None,
            "status": "failed",
            "required_for_track_score": True,
            "error": str(exc),
            "reason": str(exc),
            "evidence": [],
        }


def _blend_qr(detail: dict[str, Any]) -> float | None:
    judge = (
        detail.get("result_judge")
        if isinstance(detail.get("result_judge"), dict)
        else {}
    )
    judge_score = judge.get("score")
    if judge_score is None:
        return None

    programmatic = detail.get("programmatic") or {}
    programmatic_score = programmatic.get("score")
    code_eval = (
        detail.get("code_eval") if isinstance(detail.get("code_eval"), dict) else {}
    )
    code_score = code_eval.get("score")
    code_applicable = bool(code_eval.get("applicable")) and code_score is not None

    if programmatic_score is None:
        if code_applicable:
            detail["blend_weights"] = {
                "programmatic": None,
                "code_eval": 0.30,
                "result_judge": 0.70,
            }
            return round(0.30 * float(code_score) + 0.70 * float(judge_score), 4)
        detail["blend_weights"] = {
            "programmatic": None,
            "code_eval": None,
            "result_judge": 1.0,
        }
        return round(float(judge_score), 4)

    try:
        programmatic_f = float(programmatic_score)
        judge_f = float(judge_score)
    except (TypeError, ValueError):
        return None

    if code_applicable:
        detail["blend_weights"] = {
            "programmatic": 0.30,
            "code_eval": 0.30,
            "result_judge": 0.40,
        }
        return round(
            0.30 * programmatic_f + 0.30 * float(code_score) + 0.40 * judge_f, 4
        )

    detail["blend_weights"] = {
        "programmatic": 0.40,
        "code_eval": None,
        "result_judge": 0.60,
    }
    return round(0.40 * programmatic_f + 0.60 * judge_f, 4)


def evaluate(
    *,
    task: Any,
    bench_root: Path,
    workspace_path: str,
    conversation: list[dict[str, Any]],
    tool_logs: list[Any],
    eval_model: str | None,
    reference: dict[str, Any] | None,
    preflight: Any = None,
    cancel_event: threading.Event | None = None,
) -> TrackResult:
    blockers: list[dict[str, Any]] = list(preflight_blockers(preflight, "qr"))
    model_unavailable = any(
        blocker.get("code") == "eval_model_unavailable" for blocker in blockers
    )

    programmatic, programmatic_error = _programmatic_eval(
        task=task,
        bench_root=bench_root,
        workspace_path=workspace_path,
        conversation=conversation,
        tool_logs=tool_logs,
    )
    if programmatic_error:
        blockers.append(
            {"track": "qr", "dimension": "programmatic", "reason": programmatic_error}
        )
    check_cancel(cancel_event)

    code_eval, code_error = _code_eval(
        task=task,
        workspace_path=workspace_path,
        tool_logs=tool_logs,
        reference=reference,
    )
    if code_error:
        blockers.append({"track": "qr", "dimension": "code_eval", "reason": code_error})
    check_cancel(cancel_event)

    if model_unavailable:
        result_judge = {
            "score": None,
            "status": "failed",
            "required_for_track_score": True,
            "reason": "Evaluation model is unavailable",
            "evidence": [],
        }
    else:
        result_judge = _result_judge(
            task=task,
            workspace_path=workspace_path,
            tool_logs=tool_logs,
            conversation=conversation,
            eval_model=eval_model,
            reference=reference,
            cancel_event=cancel_event,
        )
    if result_judge.get("score") is None:
        blockers.append(
            {
                "track": "qr",
                "dimension": "result_judge",
                "reason": result_judge.get("error")
                or result_judge.get("reason")
                or "No score",
            }
        )

    detail: dict[str, Any] = {
        "programmatic": programmatic,
        "code_eval": code_eval,
        "result_judge": result_judge,
    }
    score = None if blockers else _blend_qr(detail)
    return TrackResult(
        track="qr",
        score=score,
        status=track_status(score, blockers),
        detail=detail,
        blocking_missing=blockers,
        eval_cost=eval_cost_from(result_judge),
        eval_cost_by_model=cost_by_model_from(result_judge),
    )
