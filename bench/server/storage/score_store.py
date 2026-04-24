"""Append-only score run storage for server evaluations."""

from __future__ import annotations

import json
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


@dataclass
class ScoreRun:
    score_id: str
    status: str
    eval_mode: str
    eval_model: str | None
    tutor_dims: list[str] | None
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
    _index_path(result_dir).write_text(
        json.dumps(index, indent=2, default=str),
        encoding="utf-8",
    )


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
    tutor_dims: list[str] | None = None,
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
            tutor_dims=tutor_dims,
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
    tutor = score_data.get("tutor") or {}
    summary = {
        "score_id": score_data.get("score_id"),
        "score_status": score_data.get("score_status"),
        "quant_result": qr.get("score") if isinstance(qr, dict) else None,
        "quant_process": qp.get("score") if isinstance(qp, dict) else None,
        "tutor_score": tutor.get("score") if isinstance(tutor, dict) else None,
        "overall": score_data.get("overall_score"),
        "overall_score": score_data.get("overall_score"),
        "judge_reliability": score_data.get("judge_reliability") or {},
        "blocking_missing": score_data.get("blocking_missing", []),
    }
    if cost_data:
        summary["eval_cost_usd"] = cost_data.get("eval_cost_usd", 0.0)
    return summary


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
            entry
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
                        "score": score,
                        "cost": cost,
                    }
                )
            elif entry:
                status = str(entry.get("status") or "pending")
                out.append(
                    {
                        "score_id": sid,
                        "status": status,
                        "score_status": status,
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
