"""§C — Data-quality audit, full section Component."""

from __future__ import annotations

from experiments.user_sim_stability.analysis.components.base import (
    Component,
    _html,
)
from experiments.user_sim_stability.analysis.components.tables.data_quality_audit import (
    DataQualityAudit,
)


class DataQualityAuditSection(Component):
    """Methodology Appendix §C — full block.

    Renders the rubric block, an explicit list of currently-failing checks
    (the plan calls this the "explicit known-staleness list"), the overall
    pass/fail status line, and the inner audit table.
    """

    name = "data_quality_audit_section"

    def __init__(self, audit: dict | None):
        self.audit = audit or {}
        self._inner = DataQualityAudit(audit)

    def render_html(self) -> str:
        audit = self.audit
        audit_table = self._inner.render_html()
        checks = audit.get("checks") or []
        failing = [c for c in checks if not c.get("ok")]
        if failing:
            items = "".join(
                "<li>"
                f"<code>{_html(c.get('name'))}</code>: "
                f"{_html(c.get('message'))}"
                "</li>"
                for c in failing
            )
            staleness_block = (
                '<div class="insight">'
                "<strong>Known data-staleness items currently failing:</strong>"
                f"<ul>{items}</ul>"
                "</div>"
            )
        else:
            staleness_block = (
                '<div class="insight">'
                "<strong>All audit checks pass.</strong> No known data-staleness items."
                "</div>"
            )
        return f"""<h2>C. Data Quality Audit</h2>
<div class="rubric">
<strong>Definition:</strong> Data quality audit checks whether artifacts satisfy the no-fallback run contract.<br>
<strong>Context:</strong> conversations, judge inputs, judge outputs, snapshots, human status, model metadata, and report artifacts.<br>
<strong>Fields:</strong> check name, pass/fail status, diagnostic message.<br>
<strong>Aggregation:</strong> run passes only when every required validation check passes.
</div>
<p>Overall audit status: <code>{'pass' if audit.get('ok') else 'fail_or_not_run'}</code></p>
{staleness_block}
{audit_table}"""

    def render_csv(self) -> bytes | None:
        return self._inner.render_csv()
