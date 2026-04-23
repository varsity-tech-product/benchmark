"""Termination criteria checker for QuantTutorBench.

Checks whether the conversation has met all learning objectives
via incremental LLM calls on the latest exchange only.
No DeepEval dependency -- used by TutoringSession inside send_message.

This is the single authoritative implementation of TC checking.
The orchestrator's _EfficientSimulator delegates to this module.
"""

import json
import logging
import re
from typing import Any, Optional

from server.config.llm_config import TC_CHECKER_MODEL, create_openrouter_sync_client
from server.core.tc_evidence import serialize_turn_evidence

logger = logging.getLogger(__name__)


_INCREMENTAL_CATEGORIES = frozenset(
    {"strategy", "backtest", "implementation", "debug", "data_analysis", "end_to_end"}
)


class TCChecker:
    """Incremental termination criteria checker.

    Maintains a coverage bitmap of TC items. After each agent message,
    checks whether newly-covered items bring coverage to 100%.

    Uses direct OpenRouter LLM calls (no DeepEval dependency).
    """

    def __init__(
        self,
        tc_items: list[str],
        model: str = TC_CHECKER_MODEL,
        temperature: float = 0.0,
    ):
        self.tc_items = tc_items
        self.covered = [False] * len(tc_items)
        self.model = model
        self.temperature = temperature
        self._client = None
        self._TC_TRUNC = 3000
        self._CODE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)
        # Cost tracking
        self.total_calls: int = 0
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_cost: float = 0.0
        self.history: list[dict[str, Any]] = []

    @property
    def client(self):
        if self._client is None:
            self._client = create_openrouter_sync_client()
        return self._client

    @property
    def all_covered(self) -> bool:
        return all(self.covered)

    @property
    def coverage_summary(self) -> dict:
        return {
            "total": len(self.tc_items),
            "covered": sum(self.covered),
            "covered_indices": [
                i + 1 for i, covered in enumerate(self.covered) if covered
            ],
            "items": [
                {"text": tc, "covered": self.covered[i]}
                for i, tc in enumerate(self.tc_items)
            ],
        }

    @property
    def debug_history(self) -> list[dict[str, Any]]:
        return list(self.history)

    @property
    def stalled_turns(self) -> int:
        stalled = 0
        for item in reversed(self.history):
            if item.get("newly_covered_indices"):
                break
            stalled += 1
        return stalled

    def check(
        self,
        conversation: list[dict[str, str]],
        turn_evidence: Optional[dict] = None,
        turn_index: Optional[int] = None,
    ) -> bool:
        """Check if the latest exchange covers any new TC items.

        Args:
            conversation: Full conversation so far.
            turn_evidence: Server-side normalized evidence for the latest tutor turn.
            turn_index: Tutor turn index corresponding to the latest exchange.

        Returns:
            True if ALL TC items are now covered (conversation should end).
        """
        if self.all_covered:
            return True

        # Get the latest exchange (last student + tutor message)
        recent = conversation[-2:] if len(conversation) >= 2 else conversation
        if not recent:
            return False

        # Multi-pass check (head, tail, code blocks)
        covered_before = [i + 1 for i, covered in enumerate(self.covered) if covered]
        newly, debug_info = self._multi_pass_check(recent, turn_evidence)
        for idx in newly:
            if 0 <= idx < len(self.covered):
                self.covered[idx] = True
        covered_after = [i + 1 for i, covered in enumerate(self.covered) if covered]

        assistant_text = next(
            (t["content"] for t in reversed(recent) if t["role"] == "assistant"),
            "",
        )
        student_text = next(
            (t["content"] for t in recent if t["role"] == "user"),
            "",
        )
        self.history.append(
            {
                "turn_index": turn_index,
                "covered_before_indices": covered_before,
                "newly_covered_indices": [i + 1 for i in newly],
                "covered_after_indices": covered_after,
                "passes_used": debug_info["passes_used"],
                "assistant_chars": len(assistant_text),
                "student_chars": len(student_text),
                "assistant_preview": assistant_text[:240],
                "student_preview": student_text[:180],
                "evidence_tool_count": (turn_evidence or {}).get(
                    "substantive_tool_count", 0
                ),
                "evidence_failed_tool_count": (turn_evidence or {}).get(
                    "failed_tool_count", 0
                ),
                "evidence_calls_truncated": (turn_evidence or {}).get(
                    "calls_truncated", False
                ),
            }
        )

        return self.all_covered

    def _multi_pass_check(
        self,
        recent: list[dict],
        turn_evidence: Optional[dict] = None,
    ) -> tuple[list[int], dict[str, Any]]:
        """Three-pass strategy for long messages: head, tail, code blocks."""
        needs_tail = any(len(t["content"]) > self._TC_TRUNC for t in recent)
        evidence_text = None
        if turn_evidence and turn_evidence.get("substantive_tool_count", 0) > 0:
            evidence_text = serialize_turn_evidence(turn_evidence)
        passes_used: list[str] = []

        # Pass 1: head
        head_json = json.dumps(
            [
                {"role": t["role"], "content": t["content"][: self._TC_TRUNC]}
                for t in recent
            ],
            ensure_ascii=False,
        )
        newly = self._call_checker(head_json, evidence_text=evidence_text)
        passes_used.append("head")

        if not needs_tail or self._all_covered_after(newly):
            return newly, {"passes_used": passes_used}

        # Pass 2: tail
        tail_json = json.dumps(
            [
                {
                    "role": t["role"],
                    "content": (
                        t["content"][-self._TC_TRUNC :]
                        if len(t["content"]) > self._TC_TRUNC
                        else t["content"]
                    ),
                }
                for t in recent
            ],
            ensure_ascii=False,
        )
        newly_tail = self._call_checker(tail_json, evidence_text=evidence_text)
        passes_used.append("tail")
        seen = set(newly)
        for idx in newly_tail:
            if idx not in seen:
                newly.append(idx)
                seen.add(idx)

        if self._all_covered_after(newly):
            return newly, {"passes_used": passes_used}

        # Pass 3: code blocks
        has_code_tc = any(
            "code" in self.tc_items[i].lower()
            for i, c in enumerate(self.covered)
            if not c and i not in seen
        )
        if not has_code_tc:
            return newly, {"passes_used": passes_used}

        code_snippets = []
        for t in recent:
            if t["role"] != "assistant":
                continue
            for m in self._CODE_BLOCK_RE.finditer(t["content"]):
                start = max(0, m.start() - 200)
                end = min(len(t["content"]), m.end() + 200)
                code_snippets.append(t["content"][start:end])

        if code_snippets:
            code_content = "\n---\n".join(code_snippets)[: self._TC_TRUNC]
            code_json = json.dumps(
                [{"role": "assistant", "content": code_content}],
                ensure_ascii=False,
            )
            newly_code = self._call_checker(code_json, evidence_text=evidence_text)
            passes_used.append("code")
            for idx in newly_code:
                if idx not in seen:
                    newly.append(idx)
                    seen.add(idx)

        return newly, {"passes_used": passes_used}

    def _all_covered_after(self, newly: list[int]) -> bool:
        for i, c in enumerate(self.covered):
            if not c and i not in newly:
                return False
        return True

    def _call_checker(
        self,
        exchange_json: str,
        evidence_text: Optional[str] = None,
    ) -> list[int]:
        """Single checker LLM call. Returns list of newly-covered indices (0-based)."""
        uncovered_lines = []
        covered_lines = []
        for i, tc in enumerate(self.tc_items):
            if self.covered[i]:
                covered_lines.append(f"  {i + 1}. [COVERED] {tc}")
            else:
                uncovered_lines.append(f"  {i + 1}. [NOT COVERED] {tc}")

        evidence_block = ""
        if evidence_text:
            evidence_block = (
                "\n\nServer-side execution evidence for the tutor's latest reply:\n"
                f"{evidence_text}\n\n"
                "Use this evidence only to interpret the SAME latest exchange. "
                "Treat a criterion as covered only if the tutor's reply and/or "
                "this evidence demonstrates it concretely."
            )

        prompt = (
            "You are tracking a tutoring session's progress against "
            "specific learning objectives.\n\n"
            "Current status:\n" + "\n".join(covered_lines + uncovered_lines) + "\n\n"
            "Latest exchange:\n" + exchange_json + evidence_block + "\n\n"
            "Which NOT-YET-COVERED items (if any) were demonstrated with "
            "computational evidence (actual numbers, code execution, or "
            "concrete analysis) in this exchange? "
            'Return ONLY a JSON object: {"newly_covered": [1, 3]} '
            'or {"newly_covered": []} if none were covered.'
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=self.temperature,
                max_tokens=256,
            )
            # Track cost
            self.total_calls += 1
            usage = getattr(response, "usage", None)
            if usage:
                inp = getattr(usage, "prompt_tokens", 0) or 0
                out = getattr(usage, "completion_tokens", 0) or 0
                self.total_input_tokens += inp
                self.total_output_tokens += out
                # Estimate cost (pricing for the configured TC model)
                from server.config.pricing import _resolve_pricing

                rate = _resolve_pricing(self.model)
                if rate:
                    self.total_cost += inp * rate[0] + out * rate[1]

            text = response.choices[0].message.content or ""
            match = re.search(r"\{.*\}", text, re.DOTALL)
            if not match:
                return []
            data = json.loads(match.group())
            raw = data.get("newly_covered", [])
            # Convert 1-based to 0-based, filter valid uncovered indices
            return [
                int(x) - 1
                for x in raw
                if isinstance(x, (int, float))
                and 1 <= int(x) <= len(self.tc_items)
                and not self.covered[int(x) - 1]
            ]
        except Exception as exc:
            logger.warning("TC checker call failed: %s", exc)
            return []


def parse_tc_items(
    termination_criteria,
    category: str,
    persona_id: Optional[str] = None,
) -> Optional[list[str]]:
    """Parse numbered TC items from termination_criteria text.

    Returns None if the category doesn't use incremental checking
    or TC text can't be parsed.
    """
    if category not in _INCREMENTAL_CATEGORIES:
        return None

    tc = termination_criteria
    if not tc:
        return None

    # Support per-persona TC (dict keyed by persona_id)
    if isinstance(tc, dict):
        if persona_id and persona_id in tc:
            tc = tc[persona_id]
        else:
            tc = next(iter(tc.values()), None)
            if not tc:
                return None

    if not isinstance(tc, str):
        return None

    items = re.findall(
        r"(?<![A-Za-z])\(\d+\)\s*(.+?)(?=(?<![A-Za-z])\(\d+\)|Once all|$)",
        tc,
        re.DOTALL,
    )
    items = [item.strip().rstrip(".") for item in items if item.strip()]
    if len(items) < 2:
        return None
    return items
