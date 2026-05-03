"""RunService — protocol-agnostic business logic for Run operations.

All REST handlers call this service. No HTTP/MCP concerns here.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from uuid import uuid4

from .catalog import TaskCatalog
from .models import TERMINAL_STATUSES, RunAssignment, RunStatus
from .store import RunStore
from server.quota import QuotaExceeded, max_active_runs_per_user, quota_subject_enabled

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _token_expired(expires_at: str) -> bool:
    if not expires_at:
        return False
    try:
        exp = datetime.fromisoformat(expires_at)
        return datetime.now(timezone.utc) > exp
    except ValueError:
        return False


class RunService:
    """Protocol-agnostic Run business logic."""

    def __init__(self, catalog: TaskCatalog, store: RunStore):
        self._catalog = catalog
        self._store = store

    @property
    def catalog(self) -> TaskCatalog:
        return self._catalog

    def create_run(
        self,
        task: str,
        mode: str = "agent",
        persona_policy: str = "auto",
        token_ttl_minutes: int = 30,
        owner_user_id: str = "",
        owner_github_login: str = "",
        owner_email: str = "",
        visibility: str = "private",
    ) -> tuple[RunAssignment, str, str]:
        """Create a RunAssignment + generate both tokens.

        Returns ``(assignment, raw_run_token, raw_control_token)``.
        Neither raw token is persisted — only their SHA-256 hashes. Both
        are surfaced once here and must be stored by the caller.
        """
        entry = self._catalog.resolve(task)
        if entry is None:
            raise ValueError(f"Unknown task: {task}")

        owner_user_id = str(owner_user_id or "")
        if quota_subject_enabled(owner_user_id):
            limit = max_active_runs_per_user()
            active_count = self._count_active_runs(owner_user_id)
            if limit > 0 and active_count >= limit:
                raise QuotaExceeded(
                    f"User has reached the active run limit ({limit})"
                )

        raw_token = f"qtb_{secrets.token_urlsafe(24)}"
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        token_hint = raw_token[:12]

        raw_control_token = f"qtc_{secrets.token_urlsafe(24)}"
        control_token_hash = hashlib.sha256(raw_control_token.encode()).hexdigest()
        control_token_hint = raw_control_token[:12]

        now = _now_iso()
        expires_at = ""
        if token_ttl_minutes > 0:
            exp = datetime.now(timezone.utc) + timedelta(minutes=token_ttl_minutes)
            expires_at = exp.isoformat()

        assignment = RunAssignment(
            run_id=f"run_{uuid4().hex}",
            mode=mode,
            public_task_label=entry.public_label,
            task_id=entry.task_id,
            status=RunStatus.WAITING,
            token_hint=token_hint,
            token_hash=token_hash,
            token_expires_at=expires_at,
            control_token_hint=control_token_hint,
            control_token_hash=control_token_hash,
            owner_user_id=owner_user_id,
            owner_github_login=str(owner_github_login or ""),
            owner_email=str(owner_email or ""),
            visibility=visibility if visibility in ("private", "org") else "private",
            persona_policy=persona_policy,
            created_at=now,
            updated_at=now,
        )
        self._store.save(assignment)
        logger.info(
            "Run created: %s task=%s token=%s... control=%s...",
            assignment.run_id,
            entry.public_label,
            token_hint,
            control_token_hint,
        )
        return assignment, raw_token, raw_control_token

    def create_and_claim(
        self,
        task: str,
        client_info: Optional[dict] = None,
        mode: str = "agent",
        persona_policy: str = "auto",
        owner_user_id: str = "",
        owner_github_login: str = "",
        owner_email: str = "",
        visibility: str = "private",
    ) -> tuple[RunAssignment, str, str]:
        """Create + immediately claim. For client-initiated runs.

        Returns ``(assignment, raw_run_token, raw_control_token)`` with
        status already CLAIMED.
        """
        assignment, raw_token, raw_control_token = self.create_run(
            task=task,
            mode=mode,
            persona_policy=persona_policy,
            owner_user_id=owner_user_id,
            owner_github_login=owner_github_login,
            owner_email=owner_email,
            visibility=visibility,
        )
        now = _now_iso()
        assignment.status = RunStatus.CLAIMED
        assignment.client_info = client_info
        assignment.claimed_at = now
        assignment.updated_at = now
        self._store.save(assignment)
        logger.info("Run auto-claimed: %s", assignment.run_id)
        return assignment, raw_token, raw_control_token

    def claim_run(
        self,
        raw_token: str,
        client_info: Optional[dict] = None,
    ) -> RunAssignment:
        """Client claims a run with token. WAITING -> CLAIMED.

        Raises ValueError on invalid/expired token or wrong state.
        Thread-safe: RunStore.save uses internal lock.
        """
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        assignment = self._store.find_by_token_hash(token_hash, token_type="run")

        if assignment is None:
            raise ValueError("Invalid token")

        if _token_expired(assignment.token_expires_at):
            raise ValueError("Token expired")

        if assignment.status != RunStatus.WAITING:
            raise ValueError(
                f"Run {assignment.run_id} is in '{assignment.status.value}' state, "
                f"expected 'waiting'"
            )

        now = _now_iso()
        assignment.status = RunStatus.CLAIMED
        assignment.client_info = client_info
        assignment.claimed_at = now
        assignment.updated_at = now
        self._store.save(assignment)
        logger.info("Run claimed: %s by %s", assignment.run_id, client_info)
        return assignment

    def bind_session(self, run_id: str, session_id: str) -> RunAssignment:
        """Bind a session to a run. CLAIMED -> ACTIVE.

        On store-level failure, rolls back to CLAIMED with no session bound so
        the client can retry, then re-raises.
        """
        assignment = self._get_or_raise(run_id)
        if assignment.status != RunStatus.CLAIMED:
            raise ValueError(
                f"Run {run_id} is '{assignment.status.value}', expected 'claimed'"
            )
        prev_status = assignment.status
        prev_session_id = assignment.session_id
        prev_updated_at = assignment.updated_at
        try:
            assignment.status = RunStatus.ACTIVE
            assignment.session_id = session_id
            assignment.updated_at = _now_iso()
            self._store.save(assignment)
        except Exception:
            assignment.status = prev_status
            assignment.session_id = prev_session_id
            assignment.updated_at = prev_updated_at
            try:
                self._store.save(assignment)
            except Exception:
                logger.exception(
                    "Run %s bind rollback failed to persist — in-memory state restored",
                    run_id,
                )
            raise
        logger.info("Run active: %s session=%s", run_id, session_id[:8])
        return assignment

    def mark_completed(
        self, run_id: str, result_dir: Optional[str] = None
    ) -> RunAssignment:
        """Session completed. ACTIVE -> COMPLETED.

        Idempotent if called again with the same ``result_dir`` on an already-
        COMPLETED run. Rejects any other terminal state (FAILED/CANCELLED) and
        any non-ACTIVE active state.
        """
        assignment = self._get_or_raise(run_id)
        if assignment.status in TERMINAL_STATUSES:
            if (
                assignment.status == RunStatus.COMPLETED
                and assignment.result_dir == result_dir
            ):
                return assignment
            raise ValueError(
                f"Cannot complete run {run_id} in terminal state "
                f"'{assignment.status.value}'"
            )
        if assignment.status != RunStatus.ACTIVE:
            raise ValueError(
                f"Cannot complete run {run_id} in state "
                f"'{assignment.status.value}' (expected 'active')"
            )
        now = _now_iso()
        assignment.status = RunStatus.COMPLETED
        assignment.result_dir = result_dir
        assignment.completed_at = now
        assignment.updated_at = now
        self._store.save(assignment)
        logger.info("Run completed: %s", run_id)
        return assignment

    def mark_failed(self, run_id: str, error: str) -> RunAssignment:
        """Execution failed. Any non-terminal -> FAILED."""
        assignment = self._get_or_raise(run_id)
        if assignment.status in TERMINAL_STATUSES:
            logger.warning(
                "mark_failed on %s in terminal '%s' — skipping",
                run_id,
                assignment.status.value,
            )
            return assignment
        assignment.status = RunStatus.FAILED
        assignment.error = error
        assignment.updated_at = _now_iso()
        self._store.save(assignment)
        logger.info("Run failed: %s error=%s", run_id, error)
        return assignment

    def cancel_run(self, run_id: str) -> RunAssignment:
        """Cancel a run. Any non-terminal -> CANCELLED.

        Note: session cleanup is handled by BenchSessionManager, not here.
        This method only updates run status.
        """
        assignment = self._get_or_raise(run_id)
        if assignment.status in TERMINAL_STATUSES:
            raise ValueError(
                f"Run {run_id} is already in terminal state '{assignment.status.value}'"
            )
        assignment.status = RunStatus.CANCELLED
        assignment.updated_at = _now_iso()
        self._store.save(assignment)
        logger.info("Run cancelled: %s", run_id)
        return assignment

    def reset_for_retry(self, run_id: str) -> RunAssignment:
        """Reset a COMPLETED run back to CLAIMED so a fresh session can bind.

        Used by the public retry endpoint when the prior session terminated
        with an infrastructure-class failure. Clears the run-level pointers
        to the failed session (session_id, result_dir, error, completed_at,
        eval_status); the underlying bundle on disk is left untouched so the
        failure receipt is preserved. ``claimed_at`` is refreshed so the
        idle-claim sweeper does not retire the run between reset and the
        follow-up bind_session.
        """
        assignment = self._get_or_raise(run_id)
        if assignment.status != RunStatus.COMPLETED:
            raise ValueError(
                f"Run {run_id} is '{assignment.status.value}', "
                f"only 'completed' runs can be reset for retry"
            )
        now = _now_iso()
        assignment.status = RunStatus.CLAIMED
        assignment.session_id = None
        assignment.result_dir = None
        assignment.error = None
        assignment.completed_at = None
        assignment.eval_status = "pending"
        assignment.claimed_at = now
        assignment.updated_at = now
        self._store.save(assignment)
        logger.info("Run reset for retry: %s", run_id)
        return assignment

    def restore_after_failed_retry(
        self,
        run_id: str,
        *,
        session_id: Optional[str],
        result_dir: Optional[str],
        completed_at: Optional[str],
        eval_status: str,
        error: Optional[str],
    ) -> RunAssignment:
        """Roll a CLAIMED retry back to its prior COMPLETED state.

        Used by the retry endpoint when ``reset_for_retry`` succeeded but
        the follow-up session register failed (e.g. transient sandbox
        startup error). Re-attaches the original failure receipt so the
        run remains pointing at the failed bundle and stays eligible for
        another retry.
        """
        assignment = self._get_or_raise(run_id)
        if assignment.status != RunStatus.CLAIMED:
            raise ValueError(
                f"Run {run_id} is '{assignment.status.value}', "
                f"expected 'claimed' for retry rollback"
            )
        assignment.status = RunStatus.COMPLETED
        assignment.session_id = session_id
        assignment.result_dir = result_dir
        assignment.completed_at = completed_at
        assignment.eval_status = eval_status
        assignment.error = error
        assignment.updated_at = _now_iso()
        self._store.save(assignment)
        logger.info("Run %s restored after failed retry attempt", run_id)
        return assignment

    def update_eval_status(self, run_id: str, eval_status: str) -> None:
        """Mirror SessionState._eval_status into RunAssignment."""
        assignment = self._store.get(run_id)
        if assignment is None:
            return
        assignment.eval_status = eval_status
        assignment.updated_at = _now_iso()
        self._store.save(assignment)

    def get_run(self, run_id: str) -> Optional[RunAssignment]:
        return self._store.get(run_id)

    def list_runs(
        self,
        status: Optional[str] = None,
        task: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        include_all: bool = False,
        include_org: bool = False,
    ) -> list[RunAssignment]:
        status_enum = RunStatus(status) if status else None
        runs = self._store.list_runs(status=status_enum)
        if task:
            runs = [r for r in runs if r.public_task_label == task]
        if owner_user_id is not None and not include_all:
            owner = str(owner_user_id or "")
            if include_org:
                runs = [
                    r
                    for r in runs
                    if r.owner_user_id == owner
                    or (r.visibility == "org" and bool(r.owner_user_id))
                ]
            else:
                runs = [r for r in runs if r.owner_user_id == owner]
        return runs

    def get_run_for_user(self, run_id: str, user) -> Optional[RunAssignment]:
        assignment = self._store.get(run_id)
        if assignment is None:
            return None
        if self._run_visible_to_user(assignment, user):
            return assignment
        return None

    def assert_run_owner_or_admin(self, run_id: str, user) -> RunAssignment:
        assignment = self._get_or_raise(run_id)
        if not self._run_visible_to_user(assignment, user):
            raise PermissionError("Run access denied")
        return assignment

    def resolve_token(
        self, raw_token: str, *, allow_expired_bound: bool = False
    ) -> Optional[RunAssignment]:
        """Find a run by raw run_token. Does NOT change state."""
        token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        assignment = self._store.find_by_token_hash(token_hash, token_type="run")
        if assignment is None:
            return None
        if _token_expired(assignment.token_expires_at) and not (
            allow_expired_bound and bool(assignment.session_id)
        ):
            return None
        return assignment

    def verify_control_token(
        self, run_id: str, raw_control_token: str
    ) -> RunAssignment:
        """Return the assignment iff the control token matches.

        Raises PermissionError on any mismatch or missing stored hash.
        """
        assignment = self._get_or_raise(run_id)
        expected = assignment.control_token_hash
        if not expected:
            raise PermissionError("Run has no control token")
        actual = hashlib.sha256(raw_control_token.encode()).hexdigest()
        if not hmac.compare_digest(expected, actual):
            raise PermissionError("Invalid control token")
        return assignment

    def _get_or_raise(self, run_id: str) -> RunAssignment:
        assignment = self._store.get(run_id)
        if assignment is None:
            raise ValueError(f"Run not found: {run_id}")
        return assignment

    def _count_active_runs(self, owner_user_id: str) -> int:
        return sum(
            1
            for run in self._store.list_runs()
            if run.owner_user_id == owner_user_id and run.status not in TERMINAL_STATUSES
        )

    def _run_visible_to_user(self, assignment: RunAssignment, user) -> bool:
        if user is None:
            return False
        if bool(getattr(user, "is_admin", False)):
            return True
        return bool(
            assignment.owner_user_id
            and assignment.owner_user_id == str(getattr(user, "user_id", "") or "")
        )
