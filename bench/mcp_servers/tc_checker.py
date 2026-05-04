"""Termination criteria checker for QuantTutorBench.

Checks whether the conversation has met all learning objectives
via incremental LLM calls on the latest exchange only.
No DeepEval dependency -- used by TutoringSession inside send_message.

This is the single authoritative implementation of TC checking.
The orchestrator's _EfficientSimulator delegates to this module.
"""

import json
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)


class TCChecker:
    """Incremental termination criteria checker.

    Maintains a coverage bitmap of TC items. After each agent message,
    checks whether newly-covered items bring coverage to 100%.

    Uses direct OpenRouter LLM calls (no DeepEval dependency).
    """

    def __init__(
        self,
        tc_items: list[str],
        model: str = "anthropic/claude-sonnet-4-6",
        temperature: float = 0.0,
    ):
        self.tc_items = tc_items
        self.covered = [False] * len(tc_items)
        self.model = model
        self.temperature = temperature
        self._client = None
        self._TC_TRUNC = 3000
        self._CODE_BLOCK_RE = re.compile(r"```[^\n]*\n(.*?)```", re.DOTALL)

    @property
    def client(self):
        if self._client is None:
            try:
                import openai
            except ImportError:
                raise ImportError("openai package required. pip install openai")
            api_key = os.environ.get("OPENROUTER_API_KEY", "")
            if not api_key:
                raise RuntimeError("OPENROUTER_API_KEY not set.")
            self._client = openai.OpenAI(
                api_key=api_key,
                base_url="https://openrouter.ai/api/v1",
            )
        return self._client

    @property
    def all_covered(self) -> bool:
        return all(self.covered)

    @property
    def coverage_summary(self) -> dict:
        return {
            "total": len(self.tc_items),
            "covered": sum(self.covered),
            "items": [
                {"text": tc, "covered": self.covered[i]}
                for i, tc in enumerate(self.tc_items)
            ],
        }

    def check(self, conversation: list[dict[str, str]]) -> bool:
        """Check if the latest exchange covers any new TC items.

        Args:
            conversation: Full conversation so far.

        Returns:
            True if ALL TC items are now covered (conversation should end).
        """
        if self.all_covered:
            return True

        # Get the latest exchange (last user + tutor message)
        recent = conversation[-2:] if len(conversation) >= 2 else conversation
        if not recent:
            return False

        # Multi-pass check (head, tail, code blocks)
        newly = self._multi_pass_check(recent)
        for idx in newly:
            if 0 <= idx < len(self.covered):
                self.covered[idx] = True

        return self.all_covered

    def _multi_pass_check(self, recent: list[dict]) -> list[int]:
        """Three-pass strategy for long messages: head, tail, code blocks."""
        needs_tail = any(len(t["content"]) > self._TC_TRUNC for t in recent)

        # Pass 1: head
        head_json = json.dumps(
            [{"role": t["role"], "content": t["content"][:self._TC_TRUNC]} for t in recent],
            ensure_ascii=False,
        )
        newly = self._call_checker(head_json)

        if not needs_tail or self._all_covered_after(newly):
            return newly

        # Pass 2: tail
        tail_json = json.dumps(
            [
                {
                    "role": t["role"],
                    "content": (
                        t["content"][-self._TC_TRUNC:]
                        if len(t["content"]) > self._TC_TRUNC
                        else t["content"]
                    ),
                }
                for t in recent
            ],
            ensure_ascii=False,
        )
        newly_tail = self._call_checker(tail_json)
        seen = set(newly)
        for idx in newly_tail:
            if idx not in seen:
                newly.append(idx)
                seen.add(idx)

        if self._all_covered_after(newly):
            return newly

        # Pass 3: code blocks
        has_code_tc = any(
            "code" in self.tc_items[i].lower()
            for i, c in enumerate(self.covered)
            if not c and i not in seen
        )
        if not has_code_tc:
            return newly

        code_snippets = []
        for t in recent:
            if t["role"] != "assistant":
                continue
            for m in self._CODE_BLOCK_RE.finditer(t["content"]):
                start = max(0, m.start() - 200)
                end = min(len(t["content"]), m.end() + 200)
                code_snippets.append(t["content"][start:end])

        if code_snippets:
            code_content = "\n---\n".join(code_snippets)[:self._TC_TRUNC]
            code_json = json.dumps(
                [{"role": "assistant", "content": code_content}],
                ensure_ascii=False,
            )
            newly_code = self._call_checker(code_json)
            for idx in newly_code:
                if idx not in seen:
                    newly.append(idx)
                    seen.add(idx)

        return newly

    def _all_covered_after(self, newly: list[int]) -> bool:
        for i, c in enumerate(self.covered):
            if not c and i not in newly:
                return False
        return True

    def _call_checker(self, exchange_json: str) -> list[int]:
        """Single checker LLM call. Returns list of newly-covered indices (0-based)."""
        uncovered_lines = []
        covered_lines = []
        for i, tc in enumerate(self.tc_items):
            if self.covered[i]:
                covered_lines.append(f"  {i + 1}. [COVERED] {tc}")
            else:
                uncovered_lines.append(f"  {i + 1}. [NOT COVERED] {tc}")

        prompt = (
            "You are tracking a tutoring session's progress against "
            "specific learning objectives.\n\n"
            "Current status:\n"
            + "\n".join(covered_lines + uncovered_lines)
            + "\n\n"
            "Latest exchange:\n"
            + exchange_json
            + "\n\n"
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
    _INCREMENTAL_CATEGORIES = {"strategy", "backtest", "implementation", "debug"}
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
