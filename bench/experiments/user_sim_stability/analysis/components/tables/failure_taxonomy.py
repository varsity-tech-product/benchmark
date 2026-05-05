"""Failure-taxonomy tables and example cards Component."""

from __future__ import annotations

from experiments.user_sim_stability.analysis.components.base import (
    Component,
    _html,
    booktabs_table,
    csv_bytes,
)


class FailureTaxonomy(Component):
    """Renders the failure-type / model / persona / task tables and example cards."""

    name = "failure_taxonomy"

    def __init__(self, taxonomy_stats: dict | None):
        self.tax = taxonomy_stats or {}

    @staticmethod
    def _axis_rows(taxonomy_block: dict) -> str:
        return "".join(
            f"<tr><td>{key}</td>"
            f"<td>{', '.join(f'{k}: {v}' for k, v in failures.items())}</td></tr>"
            for key, failures in sorted(taxonomy_block.items())
        )

    def render_html(self) -> str:
        taxonomy = self.tax
        by_type = taxonomy.get("by_type", {})
        rows = "".join(
            f"<tr><td>{failure_type}</td>"
            f"<td>{count}</td>"
            f"<td>{taxonomy.get('severity', {}).get(failure_type, {}).get('mean', 0):.2f}</td>"
            f"<td>{taxonomy.get('recommendations', {}).get(failure_type, '')}</td></tr>"
            for failure_type, count in sorted(by_type.items())
        )
        if not rows:
            rows = "<tr><td colspan='4'>No emitted failure taxonomy labels.</td></tr>"
        model_rows = self._axis_rows(taxonomy.get("by_model", {}))
        persona_rows = self._axis_rows(taxonomy.get("by_persona", {}))
        task_rows = self._axis_rows(taxonomy.get("by_task", {}))
        model_fallback = "<tr><td colspan='2'>No model-level failure labels.</td></tr>"
        persona_fallback = (
            "<tr><td colspan='2'>No persona-level failure labels.</td></tr>"
        )
        task_fallback = "<tr><td colspan='2'>No task-level failure labels.</td></tr>"
        examples = self._failure_example_cards(taxonomy.get("top_examples", []))
        return (
            "<table><tr><th>Failure Type</th><th>Count</th><th>Mean Severity</th>"
            f"<th>Recommended Action</th></tr>{rows}</table>\n"
            "<h3>Dominant by Model</h3>"
            f"<table><tr><th>Model</th><th>Failures</th></tr>{model_rows or model_fallback}</table>\n"
            "<h3>Dominant by Persona</h3>"
            f"<table><tr><th>Persona</th><th>Failures</th></tr>{persona_rows or persona_fallback}</table>\n"
            "<h3>Dominant by Task</h3>"
            f"<table><tr><th>Task</th><th>Failures</th></tr>{task_rows or task_fallback}</table>\n"
            f"<h3>Top Examples</h3>{examples}"
        )

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [
            ["axis", "key", "failure_type", "count", "mean_severity"]
        ]
        tax = self.tax
        severity = tax.get("severity", {})
        for failure_type, count in sorted(tax.get("by_type", {}).items()):
            mean_sev = severity.get(failure_type, {}).get("mean")
            rows.append(["overall", "", failure_type, count, mean_sev])
        for axis_name, key_label in (
            ("by_dimension", "dimension"),
            ("by_model", "model"),
            ("by_persona", "persona_id"),
            ("by_task", "task_id"),
            ("by_phase", "phase"),
            ("by_rubric", "rubric"),
        ):
            block = tax.get(axis_name, {})
            for key, failures in sorted(block.items()):
                for failure_type, count in sorted(failures.items()):
                    rows.append([key_label, key, failure_type, count, ""])
        return csv_bytes(rows)

    def render_tex(self) -> str:
        """Emit the headline by-type table only (failure type × count × severity).

        The cross-tab axes (by_model / by_persona / by_task) are also useful
        but inflate paper-export beyond a single table; users can pull the
        CSV if they need the full breakdown.
        """
        tax = self.tax
        severity = tax.get("severity") or {}
        recommendations = tax.get("recommendations") or {}
        rows: list[list[object]] = []
        for ft, count in sorted(tax.get("by_type", {}).items()):
            mean = (severity.get(ft, {}) or {}).get("mean", 0)
            rec = recommendations.get(ft, "")
            rows.append([ft, count, f"{mean:.2f}", rec])
        if not rows:
            rows = [["No emitted failure taxonomy labels.", "", "", ""]]
        return booktabs_table(
            ["Failure type", "Count", "Mean severity", "Recommended action"],
            "lrrp{6cm}",
            rows,
        )

    @staticmethod
    def _failure_example_cards(examples: list[dict]) -> str:
        if not examples:
            return "<p>No failure examples available.</p>"

        cards = []
        for rank, item in enumerate(examples[:6], start=1):
            severity = item.get("severity")
            severity_value = (
                float(severity) if isinstance(severity, (int, float)) else None
            )
            severity_text = (
                f"{severity_value:.1f}" if severity_value is not None else "n/a"
            )
            severity_class = (
                " low" if severity_value is not None and severity_value <= 1.5 else ""
            )
            priority = " priority" if rank <= 3 else ""
            failure_label = (
                item.get("failure_type")
                or item.get("dominant_failure_type")
                or "failure"
            )
            evidence_raw = item.get("failure_evidence") or ""
            evidence_class = "" if evidence_raw.strip() else " empty"
            evidence_text = evidence_raw.strip() or "No judge evidence provided."
            cards.append(
                f'<div class="example-card{priority}">'
                '<div class="example-head">'
                f'<div class="example-rank">#{rank}</div>'
                '<div class="example-chips">'
                f'<span class="chip chip-dim">{_html(item.get("dimension"))}</span>'
                f'<span class="chip chip-failure">{_html(failure_label)}</span>'
                f'<span class="chip chip-severity{severity_class}">severity {severity_text}</span>'
                "</div>"
                "</div>"
                '<dl class="example-meta">'
                f'<dt>Persona</dt><dd>{_html(item.get("persona_id"))}</dd>'
                f'<dt>Task</dt><dd>{_html(item.get("task_id"))}</dd>'
                f'<dt>User</dt><dd>{_html(item.get("model"))}</dd>'
                "</dl>"
                f'<div class="example-evidence{evidence_class}">{_html(evidence_text)}</div>'
                f'<div class="example-footer">{_html(item.get("eval_id"))}</div>'
                "</div>"
            )
        return '<div class="example-grid">' + "".join(cards) + "</div>"
