"""Human review storage and read model for archived session bundles."""

from __future__ import annotations

import json
import re
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from server.auth import UserContext


REVIEW_SCHEMA_VERSION = "human_review_opinions_v1"
SECTION_IDS = {
    "task_spec",
    "conversation",
    "tool_log",
    "workspace",
    "judge_eval",
    "overall",
}
SEVERITY_IDS = {"info", "concern", "blocker"}
TARGET_KEYS = {"turn_index", "tool_call_index", "file_path", "criterion_id"}


class ReviewStore:
    """JSON-on-disk opinion cards keyed by bundle and GitHub reviewer."""

    def __init__(self, bench_root: str | Path, indexer):
        self.bench_root = Path(bench_root)
        self.indexer = indexer
        self.review_root = self.bench_root / "experiments" / "human_review"
        self._lock = threading.Lock()

    def list_bundles(
        self,
        user: UserContext | None = None,
        *,
        include_all: bool = False,
        include_org: bool = False,
    ) -> list[dict[str, Any]]:
        bundles: list[dict[str, Any]] = []
        owner_user_id = None if include_all or user is None else user.user_id
        for item in self.indexer.list_results(
            owner_user_id=owner_user_id,
            user=user,
            include_all=include_all,
            include_org=include_org,
        ):
            bundle_id = str(item.get("session_id") or "")
            if not bundle_id:
                continue
            review_dir = self._bundle_dir(bundle_id)
            review_count = (
                len(list(review_dir.glob("*.json"))) if review_dir.is_dir() else 0
            )
            row = dict(item)
            row["bundle_id"] = bundle_id
            row["review_count"] = review_count
            row["reviewed_by_current_user"] = (
                self._review_file(bundle_id, user).exists() if user else False
            )
            bundles.append(row)
        return bundles

    def get_bundle(
        self,
        bundle_id: str,
        user: UserContext | None = None,
        *,
        include_all: bool = False,
        include_org: bool = False,
    ) -> dict[str, Any] | None:
        detail = self.indexer.get_detail(
            bundle_id,
            user=user,
            include_all=include_all,
            include_org=include_org,
        )
        if detail is None:
            return None

        canonical_id = str(detail.get("session_id") or bundle_id)
        workspace = self.indexer.get_workspace_index(
            canonical_id,
            user=user,
            include_all=include_all,
            include_org=include_org,
        )
        if workspace is None:
            workspace = {"session_id": canonical_id, "file_count": 0, "files": []}

        score_json = detail.get("score_json") if isinstance(detail, dict) else None
        return {
            "bundle_id": canonical_id,
            "detail": detail,
            "layers": {
                "task_spec": self._build_task_spec_layer(detail, score_json),
                "conversation": self._build_conversation_layer(detail),
                "tool_log": self._build_tool_log_layer(detail),
                "workspace": self._build_workspace_layer(detail, workspace),
                "judge_eval": self._build_judge_eval_layer(detail, score_json),
            },
            "review": self.load_review(canonical_id, user),
        }

    def load_review(
        self, bundle_id: str, user: UserContext | None = None
    ) -> dict[str, Any]:
        if user is None:
            return self._empty_review(bundle_id, user)

        path = self._review_file(bundle_id, user)
        if not path.exists():
            return self._empty_review(bundle_id, user)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return self._empty_review(bundle_id, user)
        if not isinstance(payload, dict):
            return self._empty_review(bundle_id, user)

        base = self._empty_review(bundle_id, user)
        base.update(payload)
        base["opinions"] = [
            item for item in payload.get("opinions", []) if isinstance(item, dict)
        ]
        return base

    def append_opinion(
        self,
        bundle_id: str,
        user: UserContext,
        raw_card: dict[str, Any],
        *,
        include_all: bool = False,
        include_org: bool = False,
    ) -> dict[str, Any]:
        canonical_id = self._canonical_bundle_id(
            bundle_id,
            user=user,
            include_all=include_all,
            include_org=include_org,
        )
        card = self._normalize_card(canonical_id, user, raw_card)
        with self._lock:
            review = self.load_review(canonical_id, user)
            now = _utc_now()
            if not review.get("created_at"):
                review["created_at"] = now
            review["updated_at"] = now
            review["github_username"] = user.github_login
            review["github_user_id"] = _stable_github_user_id(user)
            review["reviewer_id"] = _stable_github_user_id(user)
            review.setdefault("opinions", []).append(card)
            self._write_review(canonical_id, user, review)
            return review

    def replace_opinions(
        self,
        bundle_id: str,
        user: UserContext,
        raw_cards: list[Any],
        *,
        include_all: bool = False,
        include_org: bool = False,
    ) -> dict[str, Any]:
        canonical_id = self._canonical_bundle_id(
            bundle_id,
            user=user,
            include_all=include_all,
            include_org=include_org,
        )
        cards = [
            self._normalize_card(canonical_id, user, card)
            for card in raw_cards
            if isinstance(card, dict)
        ]
        with self._lock:
            review = self.load_review(canonical_id, user)
            now = _utc_now()
            review["updated_at"] = now
            review["github_username"] = user.github_login
            review["github_user_id"] = _stable_github_user_id(user)
            review["reviewer_id"] = _stable_github_user_id(user)
            review["opinions"] = cards
            self._write_review(canonical_id, user, review)
            return review

    def _empty_review(
        self, bundle_id: str, user: UserContext | None = None
    ) -> dict[str, Any]:
        github_user_id = _stable_github_user_id(user) if user else ""
        github_username = user.github_login if user else ""
        return {
            "version": REVIEW_SCHEMA_VERSION,
            "bundle_id": bundle_id,
            "sample_id": bundle_id,
            "github_username": github_username,
            "github_user_id": github_user_id,
            "reviewer_id": github_user_id,
            "created_at": "",
            "updated_at": "",
            "opinions": [],
        }

    def _normalize_card(
        self,
        bundle_id: str,
        user: UserContext,
        raw_card: dict[str, Any],
    ) -> dict[str, Any]:
        section = str(raw_card.get("section") or "").strip()
        if section not in SECTION_IDS:
            raise ValueError("Invalid section")

        severity = str(raw_card.get("severity") or "info").strip().lower()
        if severity not in SEVERITY_IDS:
            raise ValueError("Invalid severity")

        comment = str(raw_card.get("comment") or "").strip()
        if not comment:
            raise ValueError("Comment is required")

        now = _utc_now()
        card = {
            "opinion_id": str(
                raw_card.get("opinion_id") or f"op_{secrets.token_hex(8)}"
            ),
            "sample_id": bundle_id,
            "bundle_id": bundle_id,
            "section": section,
            "target": self._normalize_target(raw_card.get("target")),
            "severity": severity,
            "comment": comment,
            "tags": _normalize_tags(raw_card.get("tags")),
            "github_username": user.github_login,
            "github_user_id": _stable_github_user_id(user),
            "reviewer_id": _stable_github_user_id(user),
            "created_at": now,
            "timestamp": now,
            "label_version": "v1",
        }

        if section == "judge_eval":
            disagreement = self._normalize_judge_disagreement(
                raw_card.get("judge_disagreement")
            )
            if disagreement:
                card["judge_disagreement"] = disagreement

        return card

    def _normalize_target(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, dict):
            return {}

        target: dict[str, Any] = {}
        for key in TARGET_KEYS:
            if key not in value:
                continue
            raw = value.get(key)
            if key in {"turn_index", "tool_call_index"}:
                try:
                    index = int(raw)
                except (TypeError, ValueError):
                    continue
                if index >= 0:
                    target[key] = index
                continue
            text = str(raw or "").strip()
            if text:
                target[key] = text
        return target

    def _normalize_judge_disagreement(self, value: Any) -> dict[str, float]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, float] = {}
        for key in ("judge_score", "human_score"):
            raw = value.get(key)
            if raw in (None, ""):
                continue
            try:
                normalized[key] = float(raw)
            except (TypeError, ValueError):
                continue
        return normalized

    def _write_review(
        self, bundle_id: str, user: UserContext, payload: dict[str, Any]
    ) -> None:
        path = self._review_file(bundle_id, user)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        tmp.replace(path)

    def _review_file(self, bundle_id: str, user: UserContext | None) -> Path:
        reviewer = _safe_path_part(
            _stable_github_user_id(user) if user else "anonymous"
        )
        return self._bundle_dir(bundle_id) / f"{reviewer}.json"

    def _bundle_dir(self, bundle_id: str) -> Path:
        return self.review_root / _safe_path_part(bundle_id)

    def _canonical_bundle_id(
        self,
        bundle_id: str,
        *,
        user: UserContext | None = None,
        include_all: bool = False,
        include_org: bool = False,
    ) -> str:
        detail = self.indexer.get_detail(
            bundle_id,
            user=user,
            include_all=include_all,
            include_org=include_org,
        )
        if isinstance(detail, dict):
            canonical_id = str(detail.get("session_id") or "").strip()
            if canonical_id:
                return canonical_id
        return str(bundle_id or "")

    def _build_task_spec_layer(
        self, detail: dict[str, Any], score_json: Any
    ) -> dict[str, Any]:
        return {
            "task": {
                "task_id": str(detail.get("task_id") or ""),
                "description": str(detail.get("description") or ""),
                "category": str(detail.get("category") or ""),
                "difficulty": str(detail.get("difficulty") or ""),
                "requires_code": bool(detail.get("requires_code")),
                "max_turns": detail.get("max_turns"),
            },
            "persona": {
                "persona_id": str(detail.get("persona_id") or ""),
                "description": str(detail.get("persona_description") or ""),
                "knowledge_level": str(detail.get("persona_knowledge_level") or ""),
            },
            "judge_rubric": self._extract_judge_rubric(score_json),
        }

    def _build_conversation_layer(self, detail: dict[str, Any]) -> dict[str, Any]:
        turns = detail.get("conversation")
        return {"turns": turns if isinstance(turns, list) else []}

    def _build_tool_log_layer(self, detail: dict[str, Any]) -> dict[str, Any]:
        logs = detail.get("all_tool_logs") or detail.get("tool_logs")
        return {"tool_calls": logs if isinstance(logs, list) else []}

    def _build_workspace_layer(
        self, detail: dict[str, Any], workspace: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "tree": workspace.get("files", []),
            "file_count": workspace.get("file_count", 0),
            "top_extensions": workspace.get("top_extensions", []),
            "diffs": detail.get("workspace_diffs") or [],
            "stdout": str(detail.get("stdout") or ""),
            "stderr": str(detail.get("stderr") or ""),
        }

    def _build_judge_eval_layer(
        self, detail: dict[str, Any], score_json: Any
    ) -> dict[str, Any]:
        return {
            "status": str(detail.get("evaluation_status") or "pending"),
            "overall_score": detail.get("overall_score"),
            "rows": self._extract_judge_rows(score_json),
            "score_json": score_json if isinstance(score_json, dict) else None,
        }

    def _extract_judge_rubric(self, score_json: Any) -> dict[str, Any]:
        if not isinstance(score_json, dict):
            return {}
        reliability = score_json.get("judge_reliability")
        if not isinstance(reliability, dict):
            reliability = {}
        return {
            "score_id": str(score_json.get("score_id") or ""),
            "eval_model": str(score_json.get("eval_model") or ""),
            "eval_mode": str(score_json.get("eval_mode") or ""),
            "judge_validation_run": str(reliability.get("validation_run_id") or ""),
            "tracks": [
                {
                    "track": track,
                    "status": str((score_json.get(track) or {}).get("status") or ""),
                    "score": (score_json.get(track) or {}).get("score"),
                }
                for track in ("qr", "qp", "tutor")
                if isinstance(score_json.get(track), dict)
            ],
        }

    def _extract_judge_rows(self, score_json: Any) -> list[dict[str, Any]]:
        if not isinstance(score_json, dict):
            return []

        rows: list[dict[str, Any]] = []
        for track in ("qr", "qp", "tutor"):
            payload = score_json.get(track)
            if not isinstance(payload, dict):
                continue
            rows.append(
                {
                    "criterion_id": track,
                    "track": track,
                    "criterion": track,
                    "score": payload.get("score"),
                    "verdict": str(payload.get("status") or ""),
                    "reasoning": str(payload.get("error") or ""),
                    "evidence": [],
                }
            )
            detail = payload.get("detail")
            if isinstance(detail, dict):
                rows.extend(self._extract_detail_rows(track, detail))
        return rows

    def _extract_detail_rows(
        self, track: str, detail: dict[str, Any]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for key, value in detail.items():
            if isinstance(value, dict):
                row = self._detail_value_to_row(track, str(key), value)
                if row:
                    rows.append(row)
                continue
            if isinstance(value, list):
                for index, item in enumerate(value):
                    if isinstance(item, dict):
                        row = self._detail_value_to_row(
                            track, f"{key}_{index}", item
                        )
                        if row:
                            rows.append(row)
        return rows

    def _detail_value_to_row(
        self, track: str, key: str, value: dict[str, Any]
    ) -> dict[str, Any] | None:
        has_score = any(
            name in value for name in ("score", "human_score", "judge_score")
        )
        has_text = any(
            name in value for name in ("reason", "reasoning", "rationale", "error")
        )
        has_status = "status" in value or "verdict" in value
        if not (has_score or has_text or has_status):
            return None
        evidence = value.get("evidence")
        if not isinstance(evidence, list):
            evidence = value.get("evidence_spans")
        return {
            "criterion_id": f"{track}.{key}",
            "track": track,
            "criterion": key,
            "score": value.get("score"),
            "verdict": str(value.get("status") or value.get("verdict") or ""),
            "reasoning": str(
                value.get("reason")
                or value.get("reasoning")
                or value.get("rationale")
                or value.get("error")
                or ""
            ),
            "evidence": evidence if isinstance(evidence, list) else [],
        }


def _stable_github_user_id(user: UserContext | None) -> str:
    if user is None:
        return ""
    return str(user.github_user_id or user.user_id or user.github_login or "").strip()


def _safe_path_part(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", text)
    return cleaned.strip("._") or "unknown"


def _normalize_tags(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_tags = value.split(",")
    elif isinstance(value, list):
        raw_tags = value
    else:
        raw_tags = []
    tags: list[str] = []
    for item in raw_tags:
        text = str(item or "").strip()
        if text and text not in tags:
            tags.append(text)
    return tags[:20]


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
