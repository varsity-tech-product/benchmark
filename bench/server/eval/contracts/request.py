"""Evaluation request parsing and result-directory lookup."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


class EvalError(RuntimeError):
    """Raised when an evaluation request cannot be resolved."""


_VALID_MODES = {"full", "qr", "qp", "tutor"}
_TUTOR_DIM_ALIASES = {
    "D1": "D1_finance_adaptation",
    "D2": "D2_code_adaptation",
    "D3": "D3_pedagogical_method",
    "D4": "D4_instructional_accuracy",
    "D5": "D5_empathetic_response",
    "D6": "D6_safety_boundaries",
}


@dataclass(frozen=True)
class EvalRequest:
    session_id: str
    eval_mode: str = "full"
    tutor_dims: list[str] | None = None
    eval_model: str | None = None
    idempotency_key: str | None = None


@dataclass(frozen=True)
class ScoreQuery:
    session_id: str
    score_id: str | None = None
    score_ids: list[str] | None = None
    history: bool = False
    status_filter: list[str] | None = None


def normalize_eval_mode(mode: str | None) -> str:
    key = (mode or "full").strip().lower()
    if key not in _VALID_MODES:
        allowed = ", ".join(sorted(_VALID_MODES))
        raise EvalError(f"Invalid eval_mode {mode!r}. Expected one of: {allowed}")
    return key


def parse_tutor_dims(raw: str | list[str] | None) -> list[str] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, list):
        dims = raw
    else:
        dims = raw.split(",")
    out = [str(d).strip() for d in dims if str(d).strip()]
    return out or None


def normalize_tutor_dims(dims: list[str] | None) -> list[str] | None:
    if dims is None:
        return None
    return [_TUTOR_DIM_ALIASES.get(str(dim).strip(), str(dim).strip()) for dim in dims]


def parse_status_filter(raw: str | list[str] | None) -> list[str] | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, list):
        parts = raw
    else:
        parts = raw.split(",")
    out = [p.strip() for p in parts if str(p).strip()]
    return out or None


def _read_source(
    source: Mapping[str, Any] | object | None, key: str, default: Any = None
) -> Any:
    if source is None:
        return default
    if isinstance(source, Mapping):
        return source.get(key, default)
    return getattr(source, key, default)


def parse_eval_request(
    source: Mapping[str, Any] | object | None = None,
    *,
    session_id: str | None = None,
    eval_mode: str | None = None,
    tutor_dims: str | list[str] | None = None,
    eval_model: str | None = None,
    idempotency_key: str | None = None,
) -> EvalRequest:
    """Build the single request object shared by REST and CLI."""

    sid = session_id or _read_source(source, "session_id")
    if not sid:
        raise EvalError("session_id is required")

    mode = eval_mode
    if mode is None:
        mode = _read_source(source, "eval_mode", _read_source(source, "mode", "full"))

    dims = tutor_dims
    if dims is None:
        dims = _read_source(source, "tutor_dims")

    model = eval_model
    if model is None:
        model = _read_source(source, "eval_model")

    idem = idempotency_key
    if idem is None:
        idem = _read_source(source, "idempotency_key")

    return EvalRequest(
        session_id=str(sid).strip(),
        eval_mode=normalize_eval_mode(mode),
        tutor_dims=parse_tutor_dims(dims),
        eval_model=str(model).strip() if model else None,
        idempotency_key=str(idem).strip() if idem else None,
    )


def parse_score_query(
    source: Mapping[str, Any] | object | None = None,
    *,
    session_id: str | None = None,
    score_id: str | None = None,
    score_ids: str | list[str] | None = None,
    history: bool | None = None,
    status_filter: str | list[str] | None = None,
) -> ScoreQuery:
    """Build the shared score retrieval query object."""

    sid = session_id or _read_source(source, "session_id")
    if not sid:
        raise EvalError("session_id is required")

    selected = score_id
    if selected is None:
        selected = _read_source(source, "score_id")
    if selected is None:
        selected = _read_source(source, "score")

    ids = score_ids
    if ids is None:
        ids = _read_source(source, "score_ids")
    if ids is None:
        ids = _read_source(source, "scores")
    if isinstance(ids, str):
        ids = [part.strip() for part in ids.split(",") if part.strip()]

    hist = history
    if hist is None:
        hist = _read_source(source, "history", False)
    if isinstance(hist, str):
        hist = hist.strip().lower() in {"1", "true", "yes", "on"}

    status = status_filter
    if status is None:
        status = _read_source(source, "status_filter", _read_source(source, "status"))

    return ScoreQuery(
        session_id=str(sid).strip(),
        score_id=str(selected).strip() if selected else None,
        score_ids=ids or None,
        history=bool(hist),
        status_filter=parse_status_filter(status),
    )


def resolve_result_dir(session_id: str, results_root: Path) -> Path:
    """Locate a result directory by full session id or 12-char prefix."""

    if not session_id:
        raise EvalError("session_id is required")
    results_root = Path(results_root)
    if not results_root.is_dir():
        raise EvalError(f"No results root found: {results_root}")

    short_id = session_id[:12]
    candidates: list[Path] = []

    for task_dir in results_root.iterdir():
        if not task_dir.is_dir():
            continue
        for persona_dir in task_dir.iterdir():
            if not persona_dir.is_dir():
                continue
            for run_dir in persona_dir.iterdir():
                if not run_dir.is_dir() or not run_dir.name.endswith(f"_{short_id}"):
                    continue
                sid_file = run_dir / ".session_id"
                if not sid_file.exists():
                    continue
                stored_sid = sid_file.read_text(encoding="utf-8").strip()
                if stored_sid.startswith(session_id):
                    candidates.append(run_dir)

    if not candidates:
        raise EvalError(f"No result found for session {short_id}")
    if len(candidates) > 1:
        raise EvalError(f"Ambiguous: {len(candidates)} results match {short_id}")
    return candidates[0]
