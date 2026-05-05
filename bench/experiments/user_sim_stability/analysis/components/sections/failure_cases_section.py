"""§D — Failure case studies + cross-tab failure taxonomy, full section Component."""

from __future__ import annotations

from experiments.user_sim_stability.analysis.components.base import (
    Component,
    _html,
)
from experiments.user_sim_stability.analysis.components.tables.failure_taxonomy import (
    FailureTaxonomy,
)


class FailureCasesSection(Component):
    """Methodology Appendix §D — failure case studies.

    Renders the curated case-study cards (preferred) or the picker
    candidates (fallback) at the top, then the full failure-taxonomy
    cross-tab tables, then the S2 numeric-events insight box. Phase 3
    populates the cards from ``failure_cases_curated.json`` if it exists,
    otherwise from ``failure_cases_candidates.json`` written by the
    standalone ``failure_case_picker``.
    """

    name = "failure_cases_section"

    def __init__(
        self,
        taxonomy_stats: dict | None,
        cases: list[dict] | None,
        cases_source: str = "missing",
    ):
        self.tax = taxonomy_stats or {}
        self.cases = cases or []
        self.cases_source = cases_source
        self._inner = FailureTaxonomy(taxonomy_stats)

    def render_html(self) -> str:
        taxonomy = self.tax
        numeric = taxonomy.get("d3_numeric") or {}
        tables_html = self._inner.render_html()
        cards_block = self._render_case_cards()
        return f"""<h2>D. Failure Case Studies</h2>
<div class="rubric">
<strong>Definition:</strong> Failure taxonomy explains how persona behavior fails across generated outputs.<br>
<strong>Context:</strong> judge failure_types, dominant_failure_type, failure_evidence, metadata persona/task/model/phase/rubric, and S2 per-turn numeric fields.<br>
<strong>Fields:</strong> knowledge_leak, under_competence, emotional_mismatch, generic_user_behavior, co_teacher_drift, task_forgetting, persona_contract_contradiction.<br>
<strong>Aggregation:</strong> counts by type, persona, task, model, phase, and rubric; severity is estimated as 5 minus the primary score when available.
</div>
{cards_block}
<h3>Failure-taxonomy Cross-tab</h3>
{tables_html}
<div class="insight">
<strong>S2 numeric events:</strong> knowledge leak = {numeric.get('knowledge_leak_events', 0)}, co-teacher drift = {numeric.get('co_teacher_drift_events', 0)}.
</div>"""

    def render_csv(self) -> bytes | None:
        # The taxonomy CSV is the most useful aggregate dump; per-case
        # detail lives in failure_cases_curated.json / candidates.json
        # already, so we re-export the taxonomy here.
        return self._inner.render_csv()

    def _render_case_cards(self) -> str:
        if self.cases_source == "missing" or not self.cases:
            return (
                '<div class="insight">'
                "<strong>Failure case studies — pending.</strong> "
                "Run <code>python -m experiments.user_sim_stability.analysis."
                "failure_case_picker</code> to generate "
                "<code>failure_cases_candidates.json</code>, then curate the "
                "subset you want to keep into "
                "<code>failure_cases_curated.json</code>. Re-run "
                "<code>cli report</code> to render."
                "</div>"
            )
        if self.cases_source == "candidates":
            label = (
                '<div class="insight">'
                "<strong>Picker candidates (uncurated):</strong> the cards "
                "below are the picker's auto-selected representatives. They "
                "have not yet been reviewed; create "
                "<code>failure_cases_curated.json</code> with your final "
                "subset to replace this view."
                "</div>"
            )
        else:
            label = (
                '<div class="insight">'
                "<strong>Curated failure case studies:</strong> "
                "human-reviewed subset of representative failures."
                "</div>"
            )
        cards: list[str] = []
        for idx, case in enumerate(self.cases, start=1):
            cards.append(self._render_one_card(idx, case))
        grid = '<div class="example-grid">' + "".join(cards) + "</div>"
        return label + grid

    @staticmethod
    def _artifact_links(case: dict) -> str:
        """Return a small inline list of `<a>` links to the raw judge output
        and the underlying conversation. Paths are relative to the report
        HTML at ``results/<run>/report/stability_report.html``; broken in
        paper-export contexts (acceptable — the paper version cites the
        eval_id text instead).

        eval_id → conversation source mapping:
          - ``S1`` records carry a trailing ``__turnN`` (one judge call per
            turn); the conversation file has no such suffix, so we strip it
            after stripping the leading dim prefix.
          - ``S3/S2/S4/S5`` records: strip the leading ``<DIM>__`` only.
          - ``control__...`` records: the eval_id IS the placebo conversation
            filename; we additionally surface a sibling persona-side link.
        """
        eval_id = case.get("eval_id") or ""
        if not eval_id:
            return ""
        judge_href = f"../judge_outputs/{eval_id}.json"
        parts = eval_id.split("__", 1)
        conv_links: list[tuple[str, str]] = []
        if len(parts) == 2 and parts[0] in ("S1", "S3", "S2", "S4", "S5"):
            source = parts[1]
            if parts[0] == "S1":
                # Strip trailing __turnN if present.
                tail_idx = source.rfind("__turn")
                if tail_idx > 0 and source[tail_idx + len("__turn") :].isdigit():
                    source = source[:tail_idx]
            conv_links.append((f"../conversations/{source}.json", "conversation"))
        elif parts and parts[0] == "control":
            conv_links.append(
                (f"../conversations/{eval_id}.json", "placebo conversation")
            )
            tail = eval_id[len("control__") :]
            conv_links.append(
                (f"../conversations/live__{tail}__tt0.json", "persona conversation")
            )
        else:
            conv_links.append((f"../conversations/{eval_id}.json", "conversation"))
        link_html = f'<a href="{_html(judge_href)}">judge output</a>' + "".join(
            f' · <a href="{_html(href)}">{_html(label)}</a>'
            for href, label in conv_links
        )
        return f'<div class="example-links">Artifacts: {link_html}</div>'

    @staticmethod
    def _render_one_card(idx: int, case: dict) -> str:
        severity = case.get("severity")
        sev_text = f"{severity:.2f}" if isinstance(severity, (int, float)) else "n/a"
        sev_class = ""
        if isinstance(severity, (int, float)) and severity <= 1.0:
            sev_class = " low"
        ft = case.get("failure_type") or "failure"
        evidence = (case.get("judge_evidence") or "").strip()
        if not evidence:
            evidence = "No judge evidence recorded."
        excerpt = (case.get("transcript_excerpt") or "").strip()
        excerpt_html = (
            f'<div class="example-evidence" style="white-space:pre-wrap">{_html(excerpt)}</div>'
            if excerpt
            else ""
        )
        reason = (case.get("selection_reason") or "").strip()
        links_html = FailureCasesSection._artifact_links(case)
        return (
            f'<div class="example-card{" priority" if idx <= 3 else ""}">'
            '<div class="example-head">'
            f'<div class="example-rank">#{idx}</div>'
            '<div class="example-chips">'
            f'<span class="chip chip-dim">{_html(case.get("dimension"))}</span>'
            f'<span class="chip chip-failure">{_html(ft)}</span>'
            f'<span class="chip chip-severity{sev_class}">severity {sev_text}</span>'
            "</div>"
            "</div>"
            '<dl class="example-meta">'
            f'<dt>Persona</dt><dd>{_html(case.get("persona_id"))}</dd>'
            f'<dt>Task</dt><dd>{_html(case.get("task_id"))}</dd>'
            f'<dt>User</dt><dd>{_html(case.get("model"))}</dd>'
            "</dl>"
            f'<div class="example-evidence">{_html(evidence)}</div>'
            f"{excerpt_html}"
            f"{links_html}"
            f'<div class="example-footer">{_html(case.get("eval_id"))}'
            f'{(" · " + _html(reason)) if reason else ""}</div>'
            "</div>"
        )
