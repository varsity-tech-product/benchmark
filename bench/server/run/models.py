"""Run data models — RunAssignment and RunStatus."""

from dataclasses import asdict, dataclass
from enum import Enum
from typing import Optional


class RunStatus(str, Enum):
    """Run lifecycle states."""

    WAITING = "waiting"  # created, waiting for client claim
    CLAIMED = "claimed"  # client claimed, not yet connected
    ACTIVE = "active"  # session registered and running
    COMPLETED = "completed"  # session completed normally
    FAILED = "failed"  # error (client crash / server error / timeout)
    CANCELLED = "cancelled"  # user cancelled


# Terminal states — no further transitions allowed.
TERMINAL_STATUSES = frozenset(
    {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}
)


@dataclass
class RunAssignment:
    """One benchmark run assignment."""

    run_id: str  # "run_{uuid4().hex}"
    mode: str  # "agent"
    public_task_label: str  # "D01"
    task_id: str  # "D01_load_inspect_ohlcv" (internal)
    status: RunStatus

    # Token
    token_hint: str = ""  # first 12 chars for logs
    token_hash: str = ""  # SHA-256 hex digest
    token_expires_at: str = ""  # ISO 8601, empty = no expiry

    # Binding (set after claim)
    client_info: Optional[dict] = None
    session_id: Optional[str] = None

    # Persona
    persona_policy: str = "auto"
    persona_id: Optional[str] = None

    # Result
    result_dir: Optional[str] = None
    error: Optional[str] = None

    # Evaluation (mirrors SessionState._eval_status)
    eval_status: str = "pending"

    # Timestamps (ISO 8601)
    created_at: str = ""
    updated_at: str = ""
    claimed_at: Optional[str] = None
    completed_at: Optional[str] = None

    def to_dict(self) -> dict:
        """Serialize for JSON storage."""
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "RunAssignment":
        """Deserialize from JSON storage."""
        d = dict(d)  # shallow copy
        d["status"] = RunStatus(d["status"])
        return cls(**d)

    def public_dict(self) -> dict:
        """Safe subset for UI/client responses. No token_hash, no task_id."""
        return {
            "run_id": self.run_id,
            "mode": self.mode,
            "public_task_label": self.public_task_label,
            "status": self.status.value,
            "session_id": self.session_id,
            "persona_id": self.persona_id,
            "eval_status": self.eval_status,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "claimed_at": self.claimed_at,
            "completed_at": self.completed_at,
        }
