"""Append-only score run storage for server evaluations."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

try:  # pragma: no cover - fcntl is always present in the target POSIX env.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


INDEX_VERSION = "2.0"
_INDEX_THREAD_LOCK = threading.RLock()


@dataclass
class ScoreRun:
    score_id: str
    status: str
    eval_mode: str
    eval_model: str | None
    created_at: str
    completed_at: str | None = None
    overall_score: float | None = None
    score_path: str = ""
    cost_path: str = ""
    idempotency_key: str | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _eval_root(result_dir: Path) -> Path:
    return Path(result_dir) / "evaluations"


def _index_path(result_dir: Path) -> Path:
    return _eval_root(result_dir) / "index.json"


@contextmanager
def _locked_index(result_dir: Path):
    root = _eval_root(result_dir)
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".lock"
    with _INDEX_THREAD_LOCK:
        with lock_path.open("a+", encoding="utf-8") as lock_file:
            if fcntl is not None:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _empty_index() -> dict[str, Any]:
    return {
        "version": INDEX_VERSION,
        "next_score_number": 1,
        "latest_completed_score_id": None,
        "scores": [],
    }


def load_index(result_dir: Path) -> dict[str, Any]:
    path = _index_path(result_dir)
    if not path.exists():
        return _empty_index()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid score index JSON: {path}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Invalid score index shape: {path}")
    data.setdefault("version", INDEX_VERSION)
    data.setdefault("next_score_number", 1)
    data.setdefault("latest_completed_score_id", None)
    data.setdefault("scores", [])
    return data


def save_index(result_dir: Path, index: dict[str, Any]) -> None:
    root = _eval_root(result_dir)
    root.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(index, indent=2, default=str)
    tmp_path: Path | None = None
    with _INDEX_THREAD_LOCK:
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=root,
                prefix=".index.",
                suffix=".tmp",
                delete=False,
            ) as tmp:
                tmp.write(payload)
                tmp.flush()
                os.fsync(tmp.fileno())
                tmp_path = Path(tmp.name)
            tmp_path.replace(_index_path(result_dir))
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()


def _entry_matches_status(
    entry: dict[str, Any], status_filter: set[str] | None
) -> bool:
    if not status_filter:
        return True
    status = str(entry.get("status") or "")
    if status in status_filter:
        return True
    if "completed" in status_filter and status.startswith("completed"):
        return True
    return False


def allocate_score_run(
    result_dir: Path,
    *,
    eval_mode: str,
    eval_model: str | None,
    idempotency_key: str | None = None,
) -> tuple[ScoreRun, bool]:
    """Allocate a new score_n directory, or return the active running run.

    Returns (score_run, created_new).
    """

    result_dir = Path(result_dir)
    with _locked_index(result_dir):
        index = load_index(result_dir)
        for entry in index["scores"]:
            if entry.get("status") != "running":
                continue
            if idempotency_key and entry.get("idempotency_key") == idempotency_key:
                return ScoreRun(**entry), False
            return ScoreRun(**entry), False

        number = int(index.get("next_score_number") or 1)
        score_id = f"score_{number}"
        index["next_score_number"] = number + 1
        score_dir = _eval_root(result_dir) / score_id
        score_dir.mkdir(parents=True, exist_ok=False)

        run = ScoreRun(
            score_id=score_id,
            status="running",
            eval_mode=eval_mode,
            eval_model=eval_model,
            created_at=_utc_now(),
            score_path=f"{score_id}/score.json",
            cost_path=f"{score_id}/cost.json",
            idempotency_key=idempotency_key,
        )
        index["scores"].append(asdict(run))
        save_index(result_dir, index)
        return run, True


def update_score_run(
    result_dir: Path,
    score_id: str,
    *,
    status: str,
    overall_score: float | None = None,
    completed_at: str | None = None,
    error: str | None = None,
) -> None:
    with _locked_index(result_dir):
        index = load_index(result_dir)
        for entry in index["scores"]:
            if entry.get("score_id") != score_id:
                continue
            entry["status"] = status
            entry["completed_at"] = completed_at or _utc_now()
            entry["overall_score"] = overall_score
            if error:
                entry["error"] = error
            if status.startswith("completed"):
                index["latest_completed_score_id"] = score_id
            save_index(result_dir, index)
            return
        raise KeyError(f"Unknown score_id: {score_id}")


def write_score_files(
    result_dir: Path,
    score_id: str,
    score_data: dict[str, Any],
    cost_data: dict[str, Any],
) -> None:
    score_dir = _eval_root(result_dir) / score_id
    score_dir.mkdir(parents=True, exist_ok=True)
    (score_dir / "score.json").write_text(
        json.dumps(score_data, indent=2, default=str),
        encoding="utf-8",
    )
    (score_dir / "cost.json").write_text(
        json.dumps(cost_data, indent=2, default=str),
        encoding="utf-8",
    )


def _load_score_file(result_dir: Path, score_id: str) -> dict[str, Any] | None:
    path = _eval_root(result_dir) / score_id / "score.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid score JSON: {path}") from exc
    return data if isinstance(data, dict) else None


def _load_cost_file(result_dir: Path, score_id: str) -> dict[str, Any] | None:
    path = _eval_root(result_dir) / score_id / "cost.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid cost JSON: {path}") from exc
    return data if isinstance(data, dict) else None


def latest_score_id(index: dict[str, Any]) -> str | None:
    completed = index.get("latest_completed_score_id")
    if completed:
        return str(completed)
    scores = index.get("scores") or []
    if not scores:
        return None
    return str(scores[-1].get("score_id") or "")


def summarize_score(
    score_data: dict[str, Any], cost_data: dict[str, Any] | None = None
) -> dict[str, Any]:
    qr = score_data.get("qr") or {}
    qp = score_data.get("qp") or {}
    task_fields = _task_pass_fields(score_data.get("overall_score"))
    summary = {
        "score_id": score_data.get("score_id"),
        "score_status": score_data.get("score_status"),
        "quant_result": qr.get("score") if isinstance(qr, dict) else None,
        "quant_process": qp.get("score") if isinstance(qp, dict) else None,
        "overall": task_fields["task_score"],
        "overall_score": task_fields["task_score"],
        **task_fields,
        "judge_reliability": score_data.get("judge_reliability") or {},
        "blocking_missing": score_data.get("blocking_missing", []),
    }
    if cost_data:
        summary["eval_cost_usd"] = cost_data.get("eval_cost_usd", 0.0)
    return summary


SCORE_RESPONSE_SCHEMA_VERSION = "1.0"

# Public score_status enum per #131 D-3 — pinned for external clients.
# ``interrupted`` is the coordinator's status when an eval was cancelled
# mid-flight (see eval/core/coordinator.py); kept distinct from ``failed``
# so clients can tell user-cancellation from eval-crash.
PUBLIC_SCORE_STATUSES = frozenset(
    {
        "pending",
        "running",
        "completed_scored",
        "completed_not_computable",
        "failed",
        "interrupted",
    }
)

_QR_DIM_KEYS = ("result_judge", "code_eval", "programmatic")
_QP_DIM_KEYS = (
    "tool_usage",
    "action_economy",
    "code_lifecycle",
    "task_planning",
    "problem_solving",
)


def _coerce_dim_score(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return None
    return float(raw)


def _extract_dimension(
    name: str, track: str, container: dict[str, Any]
) -> dict[str, Any] | None:
    data = container.get(name)
    if not isinstance(data, dict):
        return None
    if "score" not in data and not data.get("status"):
        return None
    return {
        "name": name,
        "track": track,
        "score": _coerce_dim_score(data.get("score")),
        "status": data.get("status"),
    }


def _track_view(track_data: Any) -> dict[str, Any] | None:
    if not isinstance(track_data, dict):
        return None
    return {
        "score": _coerce_dim_score(track_data.get("score")),
        "status": track_data.get("status"),
        "blockers": list(track_data.get("blocking_missing") or []),
    }


def _task_pass_fields(overall_score: Any) -> dict[str, Any]:
    from eval.core.scoring import compute_task_pass, task_pass_threshold_metadata

    task_score = _coerce_dim_score(overall_score)
    return {
        "task_score": task_score,
        "task_pass": compute_task_pass(task_score),
        "task_pass_threshold": task_pass_threshold_metadata(),
    }


def _entry_with_task_pass_fields(entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    out.update(_task_pass_fields(out.get("overall_score")))
    return out


def build_v1_response(
    score_data: dict[str, Any],
    cost_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the v1 forward-compat score response from persisted score+cost data.

    Public top-level: ``task_score``, ``task_pass``, ``score_id``,
    ``score_status``, ``schema_version``. Everything else lives in
    ``detail`` so adding fields later is non-breaking.

    ``task_pass`` is computed from the calibrated pass threshold for every
    completed score with a numeric ``task_score``.
    """
    from eval.core.scoring import task_pass_threshold_metadata

    qr = score_data.get("qr") if isinstance(score_data.get("qr"), dict) else None
    qp = score_data.get("qp") if isinstance(score_data.get("qp"), dict) else None
    overall = _coerce_dim_score(score_data.get("overall_score"))
    task_fields = _task_pass_fields(overall)

    dimensions: list[dict[str, Any]] = []
    qr_detail = qr.get("detail") if qr and isinstance(qr.get("detail"), dict) else {}
    for name in _QR_DIM_KEYS:
        dim = _extract_dimension(name, "qr", qr_detail)
        if dim is not None:
            dimensions.append(dim)
    qp_detail = qp.get("detail") if qp and isinstance(qp.get("detail"), dict) else {}
    for name in _QP_DIM_KEYS:
        dim = _extract_dimension(name, "qp", qp_detail)
        if dim is not None:
            dimensions.append(dim)

    detail: dict[str, Any] = {
        "dimensions": dimensions,
        "tracks": {
            "qr": _track_view(qr),
            "qp": _track_view(qp),
        },
        "task_pass_threshold": task_pass_threshold_metadata(),
        "judge_reliability": score_data.get("judge_reliability") or {},
        "blocking_missing": score_data.get("blocking_missing", []),
    }
    if cost_data:
        detail["cost"] = {
            "eval_cost_usd": cost_data.get("eval_cost_usd"),
            "eval_cost_by_track": cost_data.get("eval_cost_by_track") or {},
            "eval_cost_by_model": cost_data.get("eval_cost_by_model") or {},
        }

    return {
        "schema_version": SCORE_RESPONSE_SCHEMA_VERSION,
        "score_id": score_data.get("score_id"),
        "score_status": score_data.get("score_status"),
        "task_score": task_fields["task_score"],
        "task_pass": task_fields["task_pass"],
        "detail": detail,
    }


def get_scores_payload(
    result_dir: Path,
    *,
    history: bool = False,
    score_id: str | None = None,
    score_ids: Iterable[str] | None = None,
    status_filter: Iterable[str] | None = None,
) -> dict[str, Any]:
    index = load_index(result_dir)
    scores = list(index.get("scores") or [])
    status_set = set(status_filter or [])

    if history:
        entries = [
            _entry_with_task_pass_fields(entry)
            for entry in scores
            if _entry_matches_status(entry, status_set if status_set else None)
        ]
        return {"status": "history", "scores": entries, "evaluations": entries}

    if score_ids:
        out = []
        missing = []
        entries_by_id = {str(entry.get("score_id")): entry for entry in scores}
        for sid in score_ids:
            entry = entries_by_id.get(str(sid))
            score = _load_score_file(result_dir, sid)
            if score:
                cost = _load_cost_file(result_dir, sid)
                task_fields = _task_pass_fields(score.get("overall_score"))
                status = str(
                    (entry or {}).get("status") or score.get("score_status") or ""
                )
                out.append(
                    {
                        "score_id": sid,
                        "status": (
                            "completed" if status.startswith("completed") else status
                        ),
                        "score_status": score.get("score_status", status),
                        "overall_score": task_fields["task_score"],
                        **task_fields,
                        "scores": summarize_score(score, cost),
                        "score": score,
                        "cost": cost,
                    }
                )
            elif entry:
                status = str(entry.get("status") or "pending")
                task_fields = _task_pass_fields(entry.get("overall_score"))
                out.append(
                    {
                        "score_id": sid,
                        "status": status,
                        "score_status": status,
                        "overall_score": task_fields["task_score"],
                        **task_fields,
                        "score": None,
                        "cost": None,
                        "index": entry,
                    }
                )
            else:
                missing.append(sid)
        if missing:
            return {
                "status": "not_found",
                "error": f"Score not found: {', '.join(missing)}",
                "scores": out,
                "missing": missing,
            }
        if not out:
            return {"status": "pending", "scores": []}
        if any(item.get("status") == "running" for item in out):
            status = "running"
        elif all(str(item.get("status", "")).startswith("completed") for item in out):
            status = "completed"
        else:
            status = "partial"
        return {"status": status, "scores": out}

    selected = score_id
    if selected in (None, "", "latest"):
        selected = latest_score_id(index)
    if not selected:
        return {"status": "pending"}

    entry = next((e for e in scores if e.get("score_id") == selected), None)
    if not entry:
        return {"status": "not_found", "error": f"Score not found: {selected}"}

    if entry.get("status") == "running":
        return {"status": "running", "score_id": selected, "score_status": "running"}
    if entry.get("status") == "failed":
        return {
            "status": "failed",
            "score_id": selected,
            "score_status": "failed",
            "error": entry.get("error") or "Evaluation failed",
        }

    score = _load_score_file(result_dir, selected)
    cost = _load_cost_file(result_dir, selected)
    if not score:
        return {"status": str(entry.get("status") or "pending"), "score_id": selected}
    return {
        "status": (
            "completed"
            if str(entry.get("status", "")).startswith("completed")
            else entry.get("status")
        ),
        "score_id": selected,
        "score_status": score.get("score_status", entry.get("status")),
        "scores": summarize_score(score, cost),
        "score": score,
        "cost": cost,
    }
