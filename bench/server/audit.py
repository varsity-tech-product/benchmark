"""Append-only audit log helpers."""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_LOCK = threading.Lock()


def record_event(
    bench_root: str | Path,
    user: Any,
    action: str,
    *,
    run_id: str = "",
    session_id: str = "",
    task_id: str = "",
    request=None,
    success: bool = True,
    payload: dict | None = None,
) -> None:
    """Append one compact JSONL audit event."""
    try:
        root = Path(bench_root)
        path = root / "results" / "audit" / "events.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        user_data = _user_dict(user)
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "user_id": user_data.get("user_id", ""),
            "github_login": user_data.get("github_login", ""),
            "email": user_data.get("email", ""),
            "role": user_data.get("role", ""),
            "ip": _client_ip(request),
            "user_agent": _user_agent(request),
            "run_id": run_id,
            "session_id": session_id,
            "task_id": task_id,
            "action": action,
            "success": bool(success),
            "payload": payload or {},
        }
        line = json.dumps(event, ensure_ascii=False, default=str)
        with _LOCK:
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        return


def _user_dict(user: Any) -> dict:
    if user is None:
        return {}
    if is_dataclass(user):
        return asdict(user)
    if isinstance(user, dict):
        return user
    to_dict = getattr(user, "to_dict", None)
    if callable(to_dict):
        try:
            value = to_dict()
            if isinstance(value, dict):
                return value
        except Exception:
            return {}
    return {}


def _client_ip(request) -> str:
    if request is None:
        return ""
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    client = getattr(request, "client", None)
    return str(getattr(client, "host", "") or "")


def _user_agent(request) -> str:
    if request is None:
        return ""
    return str(request.headers.get("user-agent", ""))
