"""§B — Judge Configuration, full section Component."""

from __future__ import annotations

from experiments.student_sim_stability.analysis.components.base import Component
from experiments.student_sim_stability.analysis.components.tables.judge_configuration import (
    JudgeConfiguration,
)


class JudgeConfigurationSection(Component):
    """Methodology Appendix §B — full block (rubric + status line + by-dimension table)."""

    name = "judge_configuration_section"

    def __init__(self, metadata: dict, agreement: dict):
        self.metadata = metadata or {}
        self.agreement = agreement or {}
        self._inner = JudgeConfiguration(
            (self.agreement.get("agreement_metrics") or {}).get("by_dimension") or {}
        )

    def render_html(self) -> str:
        metadata = self.metadata
        agreement = self.agreement
        config_table = self._inner.render_html()
        return f"""<h2>B. Judge Configuration</h2>
<div class="rubric">
<strong>Definition:</strong> Judge reliability status records the primary judge and optional judge panel agreement.<br>
<strong>Context:</strong> primary judge outputs, by-model judge output directories, input hashes, and rubric metadata.<br>
<strong>Fields:</strong> judge_model, judge_temperature, input_sha256, rubric_id, rubric_version.<br>
<strong>Aggregation:</strong> agreement uses score range, score standard deviation, and within-one-point rate across judge models.
</div>
<p>Primary judge: <code>{metadata.get('primary_judge', 'unknown')}</code>; panel: <code>{', '.join(metadata.get('judge_models', []))}</code>; status: <code>{agreement.get('multi_judge_status', 'not_run')}</code>.</p>
{config_table}"""

    def render_csv(self) -> bytes | None:
        return self._inner.render_csv()
