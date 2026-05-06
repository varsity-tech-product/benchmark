"""Score-bound human review persistence for completed evaluations."""

from __future__ import annotations

import json
import re
import secrets
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from eval.programmatic.inter_rater_reliability import (
    compute_inter_rater_reliability,
)


HUMAN_REVIEW_SCHEMA_VERSION = "human_review_score_v1"
HUMAN_REVIEW_IRR_SCHEMA_VERSION = "human_review_irr_v1"
_LOCK = threading.RLock()


class HumanReviewStore:
    """JSON-backed annotations stored beside automated score artifacts."""

    def submit_review(
        self,
        result_dir: str | Path,
        score_id: str,
        user: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        result_dir = Path(result_dir)
        score_id = _safe_score_id(score_id)
        score_dir = self._score_dir(result_dir, score_id)
        self._require_completed_score(score_dir, score_id)

        record = self._normalize_record(score_id, user, payload)
        reviews_dir = score_dir / "human_reviews"
        reviews_dir.mkdir(parents=True, exist_ok=True)
        path = reviews_dir / f"{record['review_id']}.json"
        with _LOCK:
            _atomic_write_json(path, record)
        return record

    def list_reviews(
        self,
        result_dir: str | Path,
        score_id: str,
    ) -> list[dict[str, Any]]:
        reviews_dir = self._score_dir(Path(result_dir), _safe_score_id(score_id)) / (
            "human_reviews"
        )
        if not reviews_dir.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(reviews_dir.glob("*.json")):
            payload = _read_json(path)
            if isinstance(payload, dict) and payload.get("version"):
                records.append(payload)
        records.sort(key=lambda item: str(item.get("submitted_at") or ""))
        return records

    def latest_review_for_user(
        self,
        result_dir: str | Path,
        score_id: str,
        user: Any,
    ) -> dict[str, Any] | None:
        reviewer_id = _stable_reviewer_id(user)
        matches = [
            record
            for record in self.list_reviews(result_dir, score_id)
            if str(record.get("reviewer_id") or "") == reviewer_id
        ]
        return matches[-1] if matches else None

    def reviewer_has_review(
        self,
        result_dir: str | Path,
        score_id: str,
        user: Any,
    ) -> bool:
        return self.latest_review_for_user(result_dir, score_id, user) is not None

    def summary(
        self,
        result_dir: str | Path,
        score_id: str,
    ) -> dict[str, Any]:
        records = self.list_reviews(result_dir, score_id)
        reviewer_ids = {
            str(record.get("reviewer_id") or "")
            for record in records
            if record.get("reviewer_id")
        }
        return {
            "score_id": _safe_score_id(score_id),
            "review_count": len(records),
            "reviewer_count": len(reviewer_ids),
            "irr": compute_inter_rater_reliability(records),
        }

    def export_records_for_result(
        self,
        result_dir: str | Path,
    ) -> list[dict[str, Any]]:
        result_dir = Path(result_dir)
        eval_root = result_dir / "evaluations"
        if not eval_root.is_dir():
            return []

        exported: list[dict[str, Any]] = []
        for score_dir in sorted(eval_root.glob("score_*")):
            if not score_dir.is_dir():
                continue
            score_id = score_dir.name
            records = self.list_reviews(result_dir, score_id)
            exported.extend(records)
            if records:
                exported.append(
                    {
                        "version": HUMAN_REVIEW_IRR_SCHEMA_VERSION,
                        "record_type": "irr_summary",
                        "score_id": score_id,
                        "computed_at": _utc_now(),
                        "summary": compute_inter_rater_reliability(records),
                    }
                )
        return exported

    def _score_dir(self, result_dir: Path, score_id: str) -> Path:
        return result_dir / "evaluations" / score_id

    def _normalize_record(
        self,
        score_id: str,
        user: Any,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        criteria = _normalize_criteria(payload.get("criteria"))
        if not criteria:
            raise ValueError("At least one criterion score is required")

        now = _utc_now()
        return {
            "version": HUMAN_REVIEW_SCHEMA_VERSION,
            "record_type": "annotation",
            "review_id": f"hr_{secrets.token_hex(12)}",
            "score_id": score_id,
            "bundle_id": str(
                payload.get("bundle_id") or payload.get("session_id") or ""
            ),
            "session_id": str(
                payload.get("session_id") or payload.get("bundle_id") or ""
            ),
            "task_id": str(payload.get("task_id") or ""),
            "reviewer_id": _stable_reviewer_id(user),
            "github_username": user.github_login,
            "github_user_id": str(user.github_user_id or ""),
            "reviewer_role": user.role,
            "submitted_at": now,
            "criteria": criteria,
            "overall_comment": str(payload.get("overall_comment") or "").strip(),
        }

    def _require_completed_score(self, score_dir: Path, score_id: str) -> None:
        score = _read_json(score_dir / "score.json")
        if not isinstance(score, dict):
            raise FileNotFoundError(f"Score not found: {score_id}")
        status = str(score.get("score_status") or score.get("status") or "")
        if status and not status.startswith("completed"):
            raise ValueError(f"Score must be completed before review: {score_id}")


def export_human_reviews_for_result(result_dir: str | Path) -> list[dict[str, Any]]:
    return HumanReviewStore().export_records_for_result(result_dir)


def _normalize_criteria(value: Any) -> list[dict[str, Any]]:
    raw_items: list[Any]
    if isinstance(value, dict):
        raw_items = [
            dict(item, criterion_id=criterion_id)
            for criterion_id, item in value.items()
            if isinstance(item, dict)
        ]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    criteria: list[dict[str, Any]] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        criterion_id = str(raw.get("criterion_id") or "").strip()
        if not criterion_id:
            raise ValueError("criterion_id is required")
        try:
            score = int(raw.get("score"))
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Score for {criterion_id} must be an integer") from exc
        if score < 1 or score > 5:
            raise ValueError(f"Score for {criterion_id} must be between 1 and 5")
        justification = str(raw.get("justification") or "").strip()
        if not justification:
            raise ValueError(f"Justification for {criterion_id} is required")
        criteria.append(
            {
                "criterion_id": criterion_id,
                "score": score,
                "justification": justification,
                "evidence": str(raw.get("evidence") or "").strip(),
            }
        )
    return criteria


def _safe_score_id(value: str) -> str:
    score_id = str(value or "").strip()
    if not re.fullmatch(r"score_[A-Za-z0-9_.-]+", score_id):
        raise ValueError("Invalid score_id")
    return score_id


def _stable_reviewer_id(user: Any) -> str:
    return str(user.github_user_id or user.user_id or user.github_login or "").strip()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as tmp:
            json.dump(payload, tmp, indent=2, ensure_ascii=False)
            tmp.write("\n")
            tmp_path = Path(tmp.name)
        tmp_path.replace(path)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
