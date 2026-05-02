"""Termination criteria checker for QuantAgentBench.

Per #122: agent-trace based. The check is purely programmatic — TC is
met when every tool listed in the task's ``expected_mcp_tools`` has been
invoked successfully at least once. Content-topic coverage (whether the
agent's chat explained X, Y, Z) moves to post-hoc scoring via the QR
judge — it does not gate session termination.

This removes the standalone TC LLM dependency that the v6 stack relied on.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)


_TC_ITEM_RE = re.compile(r"\(\s*\d+\s*\)\s*([^\(]+?)(?=(?:\(\s*\d+\s*\))|$)", re.DOTALL)
_TC_INCREMENTAL_CATEGORIES = frozenset(
    {"strategy", "backtest", "implementation", "debug", "data_analysis", "end_to_end"}
)


def parse_tc_items(
    termination_criteria: str | None,
    category: str | None = None,
    persona_id: str | None = None,
) -> list[str] | None:
    """Parse a numbered ``(1) ... (2) ...`` TC string into discrete items.

    Returns ``None`` when ``category`` is supplied and falls outside the
    incremental-TC set (matches the v6 behaviour the call sites still rely on).
    Returns the raw items unchanged when the string has no numbered prefix.
    """
    del persona_id  # accepted for call-site compatibility; unused
    if category is not None and category not in _TC_INCREMENTAL_CATEGORIES:
        return None
    if not termination_criteria:
        return []
    text = str(termination_criteria).strip()
    matches = _TC_ITEM_RE.findall(text)
    if matches:
        return [m.strip() for m in matches if m.strip()]
    return [text]


class TCChecker:
    """Programmatic TC checker keyed off the required tool chain.

    The checker holds two parallel views:

    - ``tc_items`` — the descriptive content phrases parsed from
      ``termination_criteria`` (kept for display and diagnostics; not
      used to gate completion).
    - ``required_tools`` — a set of tool names the agent must invoke at
      least once. TC is met when ``required_tools`` is fully covered.

    When a task lists no ``required_tools`` (i.e. its expected tool chain
    is empty), the checker reports "covered" immediately so the session
    advances on max-turns / timeout without waiting on an LLM judgement.
    """

    def __init__(
        self,
        tc_items: list[str],
        required_tools: list[str] | None = None,
    ):
        self.tc_items = list(tc_items or [])
        self.required_tools: set[str] = {
            str(t).strip() for t in (required_tools or []) if str(t).strip()
        }
        self.invoked_tools: set[str] = set()
        self.history: list[dict[str, Any]] = []
        # Cost tracking is preserved for downstream readers but stays at zero;
        # this checker performs no LLM calls.
        self.total_calls: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost: float = 0.0

    @property
    def all_covered(self) -> bool:
        if not self.required_tools:
            return True
        return self.required_tools.issubset(self.invoked_tools)

    @property
    def coverage_summary(self) -> dict:
        covered_tools = sorted(self.required_tools & self.invoked_tools)
        return {
            "total": len(self.required_tools),
            "covered": len(covered_tools),
            "covered_tools": covered_tools,
            "missing_tools": sorted(self.required_tools - self.invoked_tools),
            "items": [
                {"text": tc, "covered": self.all_covered}
                for tc in self.tc_items
            ],
        }

    @property
    def debug_history(self) -> list[dict[str, Any]]:
        return list(self.history)

    @property
    def stalled_turns(self) -> int:
        stalled = 0
        for entry in reversed(self.history):
            if entry.get("newly_invoked_tools"):
                break
            stalled += 1
        return stalled

    def check(
        self,
        conversation: list[dict[str, str]] | None = None,
        turn_evidence: Optional[dict] = None,
        turn_index: Optional[int] = None,
        tool_logs: Optional[list] = None,
    ) -> bool:
        """Update the invoked-tool set from the session's full tool log and return
        whether TC is met.

        Prefers ``tool_logs`` (the proxy's raw log), since
        ``build_turn_evidence`` strips ``NON_SUBSTANTIVE_TOOLS`` and truncates
        long turns to a head-plus-tail preview — both of which would hide tools
        the TC contract still requires (e.g. ``get_environment_info``,
        bulk-shell-call turns). ``conversation`` and ``turn_evidence`` are
        accepted for legacy callers but only consulted when ``tool_logs`` is
        absent, in which case coverage is best-effort.
        """
        before = set(self.invoked_tools)

        if tool_logs is not None:
            for raw in tool_logs:
                log = raw if isinstance(raw, dict) else getattr(raw, "__dict__", {})
                if not log:
                    continue
                if not bool(log.get("success", True)):
                    continue
                name = str(log.get("name", "")).strip()
                if name:
                    self.invoked_tools.add(name)
        elif turn_evidence:
            for call in turn_evidence.get("calls") or []:
                tool = str(call.get("tool", "")).strip()
                if not tool:
                    continue
                if not call.get("success", True):
                    continue
                self.invoked_tools.add(tool)

        newly = sorted(self.invoked_tools - before)
        self.history.append(
            {
                "turn_index": turn_index,
                "newly_invoked_tools": newly,
                "invoked_tools_total": sorted(self.invoked_tools),
                "missing_tools": sorted(self.required_tools - self.invoked_tools),
            }
        )
        return self.all_covered
