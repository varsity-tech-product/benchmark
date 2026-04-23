"""GitHub OAuth and cookie-backed user sessions for the web UI."""

from __future__ import annotations

import hmac
import hashlib
import json
import os
import secrets
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlencode

import requests
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response


SESSION_COOKIE = "qtb_session"
SESSION_TTL_SECONDS = 7 * 24 * 60 * 60
OAUTH_STATE_TTL_SECONDS = 10 * 60
GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_API_URL = "https://api.github.com"


@dataclass(frozen=True)
class UserContext:
    user_id: str
    github_login: str
    email: str
    display_name: str
    avatar_url: str
    role: Literal["admin", "user"] = "user"

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "UserContext":
        return cls(
            user_id=str(data.get("user_id") or ""),
            github_login=str(data.get("github_login") or ""),
            email=str(data.get("email") or ""),
            display_name=str(data.get("display_name") or ""),
            avatar_url=str(data.get("avatar_url") or ""),
            role="admin" if data.get("role") == "admin" else "user",
        )


class AuthStore:
    """Tiny JSON-backed store for sessions and OAuth state."""

    def __init__(self, bench_root: str | Path):
        root = Path(bench_root)
        self._dir = root / "results" / "auth"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._sessions_path = self._dir / "sessions.json"
        self._states_path = self._dir / "oauth_states.json"
        self._api_keys_path = self._dir / "api_keys.json"
        self._lock = threading.Lock()

    def create_session(self, user: UserContext) -> str:
        session_id = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            sessions = self._read_locked(self._sessions_path)
            sessions[session_id] = {
                "user": user.to_dict(),
                "created_at": now,
                "expires_at": now + SESSION_TTL_SECONDS,
            }
            self._write_locked(self._sessions_path, sessions)
        return session_id

    def get_session(self, session_id: str) -> UserContext | None:
        if not session_id:
            return None
        now = time.time()
        with self._lock:
            sessions = self._read_locked(self._sessions_path)
            record = sessions.get(session_id)
            if not isinstance(record, dict):
                return None
            if float(record.get("expires_at") or 0.0) < now:
                sessions.pop(session_id, None)
                self._write_locked(self._sessions_path, sessions)
                return None
            user = record.get("user")
            if not isinstance(user, dict):
                return None
            return UserContext.from_dict(user)

    def delete_session(self, session_id: str) -> None:
        if not session_id:
            return
        with self._lock:
            sessions = self._read_locked(self._sessions_path)
            if session_id in sessions:
                sessions.pop(session_id, None)
                self._write_locked(self._sessions_path, sessions)

    def rotate_api_key(self, user: UserContext) -> tuple[str, dict]:
        """Create a new user API key and revoke prior active keys for the user."""
        raw_key = f"qtbu_{secrets.token_urlsafe(32)}"
        key_hash = _hash_secret(raw_key)
        now = time.time()
        with self._lock:
            keys = self._read_locked(self._api_keys_path)
            for record in keys.values():
                if (
                    isinstance(record, dict)
                    and (record.get("user") or {}).get("user_id") == user.user_id
                    and not record.get("revoked_at")
                ):
                    record["revoked_at"] = now
            record = {
                "key_hint": raw_key[:14],
                "user": user.to_dict(),
                "created_at": now,
                "revoked_at": None,
            }
            keys[key_hash] = record
            self._write_locked(self._api_keys_path, keys)
        return raw_key, _api_key_public_record(record)

    def get_api_key_status(self, user: UserContext) -> dict:
        with self._lock:
            keys = self._read_locked(self._api_keys_path)
            active = [
                record
                for record in keys.values()
                if isinstance(record, dict)
                and (record.get("user") or {}).get("user_id") == user.user_id
                and not record.get("revoked_at")
            ]
        if not active:
            return {"has_key": False}
        active.sort(key=lambda item: float(item.get("created_at") or 0), reverse=True)
        payload = _api_key_public_record(active[0])
        payload["has_key"] = True
        return payload

    def revoke_api_key(self, user: UserContext) -> dict:
        revoked = False
        now = time.time()
        with self._lock:
            keys = self._read_locked(self._api_keys_path)
            for record in keys.values():
                if (
                    isinstance(record, dict)
                    and (record.get("user") or {}).get("user_id") == user.user_id
                    and not record.get("revoked_at")
                ):
                    record["revoked_at"] = now
                    revoked = True
            self._write_locked(self._api_keys_path, keys)
        return {"revoked": revoked, "has_key": False}

    def get_api_key_user(self, raw_key: str) -> UserContext | None:
        if not raw_key:
            return None
        key_hash = _hash_secret(raw_key)
        with self._lock:
            keys = self._read_locked(self._api_keys_path)
            record = keys.get(key_hash)
        if not isinstance(record, dict) or record.get("revoked_at"):
            return None
        user = record.get("user")
        if not isinstance(user, dict):
            return None
        return UserContext.from_dict(user)

    def create_state(self, next_url: str = "/") -> str:
        state = secrets.token_urlsafe(32)
        now = time.time()
        with self._lock:
            states = self._read_locked(self._states_path)
            states[state] = {
                "next": _sanitize_next_url(next_url),
                "created_at": now,
                "expires_at": now + OAUTH_STATE_TTL_SECONDS,
            }
            self._write_locked(self._states_path, states)
        return state

    def pop_state(self, state: str) -> str | None:
        if not state:
            return None
        now = time.time()
        with self._lock:
            states = self._read_locked(self._states_path)
            record = states.pop(state, None)
            self._write_locked(self._states_path, states)
        if not isinstance(record, dict):
            return None
        if float(record.get("expires_at") or 0.0) < now:
            return None
        return _sanitize_next_url(str(record.get("next") or "/"))

    def _read_locked(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_locked(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp.replace(path)


class AuthService:
    """Request helpers for the web UI auth boundary."""

    def __init__(self, bench_root: str | Path):
        self.bench_root = Path(bench_root)
        self.store = AuthStore(self.bench_root)

    @property
    def mode(self) -> str:
        return os.environ.get("QTB_AUTH_MODE", "disabled").strip().lower()

    @property
    def enabled(self) -> bool:
        return self.mode == "github"

    def get_current_user(self, request: Request) -> UserContext | None:
        if not self.enabled:
            return _local_dev_user()
        session_id = request.cookies.get(SESSION_COOKIE, "")
        return self.store.get_session(session_id)

    def require_user(self, request: Request) -> tuple[UserContext | None, JSONResponse | None]:
        user = self.get_current_user(request)
        if user is None:
            return None, JSONResponse(
                {"error": "Authentication required", "login_url": "/auth/login"},
                status_code=401,
            )
        return user, None

    def require_admin(
        self, request: Request
    ) -> tuple[UserContext | None, JSONResponse | None]:
        automation = self._automation_admin(request)
        if automation is not None:
            return automation, None
        user, err = self.require_user(request)
        if err is not None:
            return None, err
        if user and user.is_admin:
            return user, None
        return None, JSONResponse({"error": "Admin access required"}, status_code=403)

    def resolve_client_user(
        self, request: Request
    ) -> tuple[UserContext | None, JSONResponse | None]:
        """Resolve the owner for REST-created client runs.

        Browser-created runs use the GitHub cookie via ``/ui/runs``. External
        REST clients use API keys declared in ``QTB_CLIENT_API_KEYS``. Local
        development remains anonymous when auth and API keys are disabled.
        """
        cookie_user = self.get_current_user(request)
        if cookie_user is not None and cookie_user.user_id != "local-dev":
            return cookie_user, None

        api_keys = _client_api_keys()
        raw = _extract_bearer(request)
        stored_user = self.store.get_api_key_user(raw)
        if stored_user is not None:
            return stored_user, None
        if raw and raw in api_keys:
            return api_keys[raw], None

        auth_required = self.enabled or bool(api_keys) or _bool_env(
            "QTB_REQUIRE_CLIENT_AUTH", False
        )
        if not auth_required:
            return None, None

        if not raw:
            return None, JSONResponse(
                {"error": "Authorization: Bearer <client_api_key> required"},
                status_code=401,
            )
        return None, JSONResponse({"error": "Invalid client API key"}, status_code=401)

    def me_payload(self, request: Request) -> dict:
        user = self.get_current_user(request)
        return {
            "auth_mode": "github" if self.enabled else "disabled",
            "github_access_policy": self.github_access_policy(),
            "authenticated": user is not None,
            "user": user.to_dict() if user else None,
        }

    def github_access_policy(self) -> Literal["disabled", "public", "invite_only"]:
        if not self.enabled:
            return "disabled"
        if _bool_env("QTB_GITHUB_ALLOW_ALL", False):
            return "public"
        if any(self._github_allowlists()):
            return "invite_only"
        return "public"

    async def login(self, request: Request) -> Response:
        next_url = request.query_params.get("next") or "/"
        if not self.enabled:
            return RedirectResponse(_sanitize_next_url(next_url), status_code=302)

        client_id = os.environ.get("QTB_GITHUB_CLIENT_ID", "").strip()
        if not client_id:
            return JSONResponse(
                {"error": "QTB_GITHUB_CLIENT_ID is required"}, status_code=500
            )

        state = self.store.create_state(next_url)
        redirect_uri = self._callback_url(request)
        query = urlencode(
            {
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "scope": "read:user user:email read:org",
                "state": state,
            }
        )
        return RedirectResponse(f"{GITHUB_AUTHORIZE_URL}?{query}", status_code=302)

    async def callback(self, request: Request) -> Response:
        if not self.enabled:
            return RedirectResponse("/", status_code=302)

        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        next_url = self.store.pop_state(state)
        if not next_url:
            return JSONResponse({"error": "Invalid OAuth state"}, status_code=400)
        if not code:
            return JSONResponse({"error": "Missing OAuth code"}, status_code=400)

        token = self._exchange_code(code, self._callback_url(request))
        profile = self._fetch_profile(token)
        if not self._is_allowed(profile, token):
            return JSONResponse({"error": "GitHub account is outside allowlist"}, 403)

        user = self._build_user(profile)
        session_id = self.store.create_session(user)
        response = RedirectResponse(next_url, status_code=302)
        self._set_session_cookie(request, response, session_id)

        from server.audit import record_event

        record_event(
            self.bench_root,
            user,
            "auth.login",
            request=request,
            success=True,
            payload={"role": user.role},
        )
        return response

    async def logout(self, request: Request) -> Response:
        user = self.get_current_user(request)
        session_id = request.cookies.get(SESSION_COOKIE, "")
        self.store.delete_session(session_id)
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie(SESSION_COOKIE, path="/")

        from server.audit import record_event

        record_event(
            self.bench_root,
            user,
            "auth.logout",
            request=request,
            success=True,
        )
        return response

    def _automation_admin(self, request: Request) -> UserContext | None:
        expected = os.environ.get("QTB_ADMIN_TOKEN", "")
        if not expected:
            return None
        auth = request.headers.get("authorization", "")
        raw = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if raw and hmac.compare_digest(expected, raw):
            return UserContext(
                user_id="automation-admin",
                github_login="automation-admin",
                email="",
                display_name="Automation Admin",
                avatar_url="",
                role="admin",
            )
        return None

    def _exchange_code(self, code: str, redirect_uri: str) -> str:
        client_id = os.environ.get("QTB_GITHUB_CLIENT_ID", "").strip()
        client_secret = os.environ.get("QTB_GITHUB_CLIENT_SECRET", "").strip()
        response = requests.post(
            GITHUB_TOKEN_URL,
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=10,
        )
        response.raise_for_status()
        payload = response.json()
        token = str(payload.get("access_token") or "")
        if not token:
            raise PermissionError("GitHub OAuth token exchange failed")
        return token

    def _fetch_profile(self, token: str) -> dict:
        user = self._github_get(token, "/user")
        emails = self._github_get(token, "/user/emails")
        if isinstance(emails, list):
            primary = next(
                (
                    item
                    for item in emails
                    if item.get("primary") and item.get("verified")
                ),
                None,
            )
            if primary and primary.get("email"):
                user["email"] = primary["email"]
        return user

    def _github_get(self, token: str, path: str):
        response = requests.get(
            f"{GITHUB_API_URL}{path}",
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
            },
            timeout=10,
        )
        response.raise_for_status()
        return response.json()

    def _is_allowed(self, profile: dict, token: str) -> bool:
        if _bool_env("QTB_GITHUB_ALLOW_ALL", False):
            return True

        login = str(profile.get("login") or "")
        allowed_logins, allowed_orgs, allowed_teams = self._github_allowlists()
        if allowed_logins and login.lower() in allowed_logins:
            return True

        if not allowed_orgs and not allowed_teams and not allowed_logins:
            return True

        if allowed_orgs:
            orgs = self._github_get(token, "/user/orgs")
            org_logins = {
                str(org.get("login") or "").lower()
                for org in orgs
                if isinstance(org, dict)
            }
            if org_logins & allowed_orgs:
                return True

        if allowed_teams:
            teams = self._github_get(token, "/user/teams")
            for team in teams if isinstance(teams, list) else []:
                if not isinstance(team, dict):
                    continue
                slug = str(team.get("slug") or "").lower()
                org = team.get("organization") or {}
                org_login = str(org.get("login") or "").lower()
                if slug in allowed_teams or f"{org_login}/{slug}" in allowed_teams:
                    return True
        return False

    def _github_allowlists(self) -> tuple[set[str], set[str], set[str]]:
        return (
            _csv_env("QTB_GITHUB_ALLOWED_LOGINS"),
            _csv_env("QTB_GITHUB_ALLOWED_ORGS"),
            _csv_env("QTB_GITHUB_ALLOWED_TEAMS"),
        )

    def _build_user(self, profile: dict) -> UserContext:
        login = str(profile.get("login") or "")
        email = str(profile.get("email") or "")
        admin_logins = _csv_env("QTB_ADMIN_GITHUB_LOGINS")
        admin_emails = _csv_env("QTB_ADMIN_EMAILS")
        role: Literal["admin", "user"] = "admin" if (
            login.lower() in admin_logins or email.lower() in admin_emails
        ) else "user"
        return UserContext(
            user_id=f"github:{login.lower()}",
            github_login=login,
            email=email,
            display_name=str(profile.get("name") or login),
            avatar_url=str(profile.get("avatar_url") or ""),
            role=role,
        )

    def _callback_url(self, request: Request) -> str:
        public_base = os.environ.get("QTB_PUBLIC_BASE_URL", "").strip().rstrip("/")
        base = public_base or str(request.base_url).rstrip("/")
        return f"{base}/auth/callback"

    def _set_session_cookie(
        self, request: Request, response: Response, session_id: str
    ) -> None:
        public_base = os.environ.get("QTB_PUBLIC_BASE_URL", "").strip().lower()
        secure_env = os.environ.get("QTB_COOKIE_SECURE", "").strip().lower()
        secure = secure_env in {"1", "true", "yes"} or public_base.startswith("https:")
        if request.url.scheme == "https":
            secure = True
        response.set_cookie(
            SESSION_COOKIE,
            session_id,
            max_age=SESSION_TTL_SECONDS,
            path="/",
            httponly=True,
            secure=secure,
            samesite="lax",
        )


def _csv_env(name: str) -> set[str]:
    raw = os.environ.get(name, "")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def _client_api_keys() -> dict[str, UserContext]:
    """Parse ``QTB_CLIENT_API_KEYS`` into API-key owners.

    Format:
      ``key=user_id|github_login|email|role,key2=user_id2|login2``

    ``github_login``, ``email``, and ``role`` are optional. Use role ``admin``
    only for trusted automation.
    """
    raw = os.environ.get("QTB_CLIENT_API_KEYS", "").strip()
    if not raw:
        return {}
    users: dict[str, UserContext] = {}
    for entry in raw.split(","):
        item = entry.strip()
        if not item or "=" not in item:
            continue
        key, spec = item.split("=", 1)
        key = key.strip()
        parts = [part.strip() for part in spec.split("|")]
        user_id = parts[0] if parts and parts[0] else ""
        if not key or not user_id:
            continue
        github_login = parts[1] if len(parts) > 1 and parts[1] else user_id
        email = parts[2] if len(parts) > 2 else ""
        role = "admin" if len(parts) > 3 and parts[3].lower() == "admin" else "user"
        users[key] = UserContext(
            user_id=user_id,
            github_login=github_login,
            email=email,
            display_name=github_login or user_id,
            avatar_url="",
            role=role,
        )
    return users


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _api_key_public_record(record: dict) -> dict:
    return {
        "key_hint": str(record.get("key_hint") or ""),
        "created_at": float(record.get("created_at") or 0.0),
    }


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


def _bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


def _sanitize_next_url(next_url: str) -> str:
    value = (next_url or "/").strip()
    if not value.startswith("/") or value.startswith("//"):
        return "/"
    return value


def _local_dev_user() -> UserContext:
    return UserContext(
        user_id="local-dev",
        github_login="local-dev",
        email="",
        display_name="Local Dev",
        avatar_url="",
        role="admin",
    )
