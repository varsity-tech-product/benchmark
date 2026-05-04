"""Multi-judge 5-view comparison tables Component (cross-judge invariance)."""

from __future__ import annotations

from collections import defaultdict

from experiments.user_sim_stability.analysis.components.base import (
    Component,
    _html,
    _score_class,
    booktabs_table,
    csv_bytes,
    tex_escape,
)
from experiments.user_sim_stability.core.numerics import safe_mean, safe_std
from experiments.user_sim_stability.core.rubrics import (
    DIMENSION_TO_FILE,
    primary_score_field,
)

_MULTI_VIEW_COLUMNS = ["sonnet", "gpt54", "gemini", "panel_3"]
_MULTI_VIEW_LABELS = {
    "sonnet": "Sonnet",
    "gpt54": "GPT-5.4",
    "gemini": "Gemini",
    "panel_3": "Panel-3 mean",
}
# Paper-side: only the three persona-stability dimensions ship to the appendix.
_TEX_DIMENSIONS = ("S1", "S3", "S2")
_MULTI_GROUP_KEY = {
    "S1": ("model", "User model"),
    "S3": ("model", "User model"),
    "S2": ("model", "User model"),
    "control": ("persona_id", "Persona"),
    "S5": ("persona_id", "Persona"),
    "S4": ("persona_id", "Persona"),
}


class MultiJudgeView(Component):
    name = "multi_judge_view"

    def __init__(self, multi: dict | None):
        self.multi = multi
        self._score_cache: dict[tuple[str, str], dict] = {}

    def render_html(self) -> str:
        if not self.multi:
            return ""
        header_cells = "".join(
            f"<th>{_MULTI_VIEW_LABELS[v]}</th>" for v in _MULTI_VIEW_COLUMNS
        )
        dim_tables: list[str] = []
        for dim in DIMENSION_TO_FILE:
            group_key, group_label = _MULTI_GROUP_KEY.get(dim, ("model", ""))
            grouped = self._score_by(dim, group_key)
            if not grouped:
                continue
            row_html = "".join(self._row_html(g, grouped[g]) for g in sorted(grouped))
            dim_tables.append(
                f"<h3>{_html(dim)} — by {_html(group_label)}</h3>"
                f'<table class="multi-view">'
                f"<tr><th>{_html(group_label)}</th>{header_cells}</tr>"
                f"{row_html}"
                "</table>"
            )
        return "".join(dim_tables)

    def render_csv(self) -> bytes:
        rows: list[list[object]] = [["dimension", "group", "view", "mean", "std", "n"]]
        if not self.multi:
            return csv_bytes(rows)
        for dim in DIMENSION_TO_FILE:
            group_key, _ = _MULTI_GROUP_KEY.get(dim, ("model", ""))
            grouped = self._score_by(dim, group_key)
            for group in sorted(grouped):
                for view, cell in grouped[group].items():
                    rows.append(
                        [
                            dim,
                            group,
                            view,
                            cell.get("mean"),
                            cell.get("std"),
                            cell.get("n"),
                        ]
                    )
        return csv_bytes(rows)

    def _row_html(self, group: str, view_scores: dict) -> str:
        cells = []
        for v in _MULTI_VIEW_COLUMNS:
            cell = view_scores.get(v)
            if not cell or cell.get("mean") is None:
                cells.append("<td>—</td>")
            else:
                mean_ = cell["mean"]
                std_ = cell.get("std") or 0.0
                css = _score_class(mean_)
                cells.append(
                    f'<td class="{css}">{mean_:.2f} '
                    f'<span class="muted">±{std_:.2f}</span></td>'
                )
        return f"<tr><td>{_html(group)}</td>" + "".join(cells) + "</tr>"

    def _score_by(self, dimension: str, group_key: str) -> dict:
        cache_key = (dimension, group_key)
        cached = self._score_cache.get(cache_key)
        if cached is not None:
            return cached
        result = self._compute_score_by(dimension, group_key)
        self._score_cache[cache_key] = result
        return result

    def _compute_score_by(self, dimension: str, group_key: str) -> dict:
        if not self.multi:
            return {}
        dim = (self.multi.get("dimensions") or {}).get(dimension)
        if not dim:
            return {}
        try:
            field = primary_score_field(dimension)
        except KeyError:
            return {}
        grouped: dict[str, dict[str, list[float]]] = defaultdict(
            lambda: defaultdict(list)
        )
        for row in dim.get("per_eval", []):
            # S2 inputs include both live and control conversations; the
            # paper-side S2 view aggregates only live items so it lines up
            # with d3_drift / ranking_table (which read all_evaluations.json
            # and route control out into its own bucket).
            if dimension == "S2":
                eid = str(row.get("eval_id") or "")
                if "__control__" in eid:
                    continue
            meta_group = self._extract_group(row, group_key)
            if meta_group is None:
                continue
            for view in ("sonnet", "gpt54", "gemini"):
                v = (row["scores_by_judge"].get(view) or {}).get(field)
                if isinstance(v, (int, float)):
                    grouped[meta_group][view].append(float(v))
            v = (row["aggregates"].get("panel_3") or {}).get(field)
            if isinstance(v, (int, float)):
                grouped[meta_group]["panel_3"].append(float(v))
        out = {}
        for g, views in grouped.items():
            out[g] = {}
            for view, values in views.items():
                out[g][view] = {
                    "mean": safe_mean(values) or 0.0,
                    "std": safe_std(values) or 0.0,
                    "n": len(values),
                }
        return out

    @staticmethod
    def _extract_group(row: dict, group_key: str) -> str | None:
        meta = row.get("metadata") or {}
        value = meta.get(group_key)
        if not value:
            return None
        return str(value)

    def render_tex(self) -> str | None:
        """Concatenate one booktabs table per persona-stability dimension
        (S1, S3, S2) into a single tex snippet. Each dim is preceded by a
        centered ``\\textbf{}`` header so the snippet remains a single
        ``\\input{}``-able unit. Cell format is the compact ``mean (std)``
        so the table fits a single-column NeurIPS textwidth."""
        if not self.multi:
            return None
        view_headers = [_MULTI_VIEW_LABELS[v] for v in _MULTI_VIEW_COLUMNS]
        sections: list[str] = []
        for dim in _TEX_DIMENSIONS:
            group_key, group_label = _MULTI_GROUP_KEY.get(dim, ("model", ""))
            grouped = self._score_by(dim, group_key)
            if not grouped:
                continue
            rows: list[list[object]] = []
            for group in sorted(grouped):
                row = [tex_escape(group)]
                for view in _MULTI_VIEW_COLUMNS:
                    cell = grouped[group].get(view)
                    if not cell or cell.get("mean") is None:
                        row.append("--")
                    else:
                        mean = cell["mean"]
                        std = cell.get("std") or 0.0
                        row.append(f"{mean:.2f} ({std:.2f})")
                rows.append(row)
            header = f"\\par\\smallskip\\textbf{{{dim} -- by {tex_escape(group_label)}}}\\par"
            col_align = "l" + "r" * len(_MULTI_VIEW_COLUMNS)
            tbl = booktabs_table(
                [tex_escape(group_label)] + [tex_escape(h) for h in view_headers],
                col_align,
                rows,
                escape=False,
            )
            sections.append(header + "\n" + tbl)
        if not sections:
            return None
        return "\n\n".join(sections)
