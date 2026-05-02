"""Preflight validation for evaluation runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from server.config.llm_config import has_openrouter_api_key
from eval.contracts.request import EvalRequest


@dataclass
class PreflightReport:
    hard_errors: list[dict[str, Any]] = field(default_factory=list)
    track_blockers: dict[str, list[dict[str, Any]]] = field(
        default_factory=lambda: {"qr": [], "qp": []}
    )
    skipped_dependencies: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.hard_errors

    def to_dict(self) -> dict[str, Any]:
        return {
            "hard_errors": self.hard_errors,
            "track_blockers": self.track_blockers,
            "skipped_dependencies": self.skipped_dependencies,
        }


def _err(code: str, message: str, **extra: Any) -> dict[str, Any]:
    out = {"code": code, "message": message}
    out.update(extra)
    return out


def _mode_includes(eval_mode: str, track: str) -> bool:
    return eval_mode == "full" or eval_mode == track


def _conversation_has_roles(conversation: list[dict[str, Any]]) -> bool:
    roles = {
        str(turn.get("role", "")) for turn in conversation if isinstance(turn, dict)
    }
    return "user" in roles and "assistant" in roles


def _validate_tool_logs(tool_logs: list[Any]) -> list[dict[str, Any]]:
    errors: list[dict[str, Any]] = []
    required = ("name", "args", "success", "turn_index")
    for idx, log in enumerate(tool_logs):
        if isinstance(log, dict):
            missing = [key for key in required if key not in log]
        else:
            missing = [key for key in required if not hasattr(log, key)]
        if missing:
            errors.append(
                _err(
                    "invalid_tool_log",
                    f"tool_logs[{idx}] is missing required fields: {', '.join(missing)}",
                    index=idx,
                    missing=missing,
                )
            )
    return errors


def run_preflight(
    *,
    request: EvalRequest,
    result_dir: Path,
    run_state: dict[str, Any],
    task: Any,
    persona: Any,
    conversation: list[dict[str, Any]],
    tool_logs: list[Any],
    bench_root: Path,
) -> PreflightReport:
    """Validate inputs before any evaluator track runs."""

    report = PreflightReport()

    if not result_dir.exists():
        report.hard_errors.append(
            _err("result_dir_missing", f"Result directory does not exist: {result_dir}")
        )
    if not (result_dir / "run_state.json").exists():
        report.hard_errors.append(
            _err("run_state_missing", f"run_state.json not found under {result_dir}")
        )
    if not isinstance(run_state, dict):
        report.hard_errors.append(
            _err("run_state_invalid", "run_state is not a JSON object")
        )
    if task is None:
        report.hard_errors.append(_err("task_missing", "Task JSON could not be loaded"))
    if persona is None:
        report.hard_errors.append(
            _err("persona_missing", "Persona JSON could not be loaded")
        )

    if not conversation:
        report.hard_errors.append(
            _err("conversation_empty", "Conversation is empty; cannot evaluate")
        )
    elif not _conversation_has_roles(conversation):
        report.hard_errors.append(
            _err(
                "conversation_incomplete",
                "Conversation must contain at least one user turn and one assistant turn",
            )
        )

    tool_log_errors = _validate_tool_logs(tool_logs)
    if tool_log_errors and _mode_includes(request.eval_mode, "qp"):
        report.track_blockers["qp"].extend(tool_log_errors)

    gt = getattr(task, "ground_truth", None)
    if _mode_includes(request.eval_mode, "qr"):
        quant_validation = getattr(gt, "quant_validation", None) if gt else None
        eval_script = getattr(quant_validation, "eval_script", None)
        if not eval_script:
            report.skipped_dependencies.append(
                _err(
                    "quant_validation_missing",
                    "Task has no quant_validation eval_script; programmatic QR is skipped",
                    track="qr",
                    dependency="programmatic",
                )
            )
        else:
            script_path = bench_root / eval_script
            if not script_path.exists():
                report.track_blockers["qr"].append(
                    _err(
                        "eval_script_missing",
                        f"quant_validation eval_script not found: {script_path}",
                        track="qr",
                        dependency="programmatic",
                    )
                )

    if request.eval_mode in ("full", "qr", "qp"):
        try:
            from eval.judges.runtime.model_resolver import require_ewan_model

            require_ewan_model(request.eval_model, purpose="evaluation")
        except Exception as exc:
            for track in ("qr", "qp"):
                if _mode_includes(request.eval_mode, track):
                    report.track_blockers[track].append(
                        _err(
                            "eval_model_unavailable",
                            str(exc),
                            track=track,
                            eval_model=request.eval_model,
                            missing_api_key=not has_openrouter_api_key(),
                        )
                    )

    return report
