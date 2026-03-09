"""Cost Report Generator for QuantTutorBench.

Produces a standalone Markdown file with a detailed cost breakdown:
Conversation Cost (Agent + Simulator), Evaluation Cost (stage × model),
and a Summary table.

Usage:
    from evaluation.cost_report import generate_cost_report
    md = generate_cost_report(task_result)
    Path("cost.md").write_text(md)
"""

from datetime import datetime
from typing import Optional


def generate_cost_report(
    result,
    task_id: Optional[str] = None,
    persona_id: Optional[str] = None,
) -> str:
    """Generate a comprehensive cost breakdown Markdown report.

    Args:
        result: TaskResult object (from schemas.py).
        task_id: Override task_id (uses result.task_id if None).
        persona_id: Override persona_id (uses result.persona_id if None).

    Returns:
        Complete Markdown string.
    """
    tid = task_id or getattr(result, "task_id", "unknown")
    pid = persona_id or getattr(result, "persona_id", "unknown")
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    duration = getattr(result, "duration_seconds", 0.0)

    tu = getattr(result, "token_usage", {})
    agent_info = tu.get("agent", {})
    sim_info = tu.get("simulator", {})
    eval_info = tu.get("eval", {})

    lines: list[str] = []
    _a = lines.append

    # ── Header ──
    _a(f"# Cost Report: {tid} / {pid}\n")
    _a(f"**Timestamp**: {ts} | **Duration**: {duration:.1f}s\n")

    # ── Conversation Cost ──
    _a("## Conversation Cost\n")

    agent_in = agent_info.get("input_tokens", 0)
    agent_out = agent_info.get("output_tokens", 0)
    agent_cost = agent_info.get("cost_usd", 0)
    agent_model = agent_info.get("model", "unknown")
    sim_cost = sim_info.get("cost_usd", 0)
    sim_model = sim_info.get("model", "unknown")

    conv_headers = ["Role", "Model", "In Tokens", "Out Tokens", "Cost"]
    conv_rows = [
        ["Agent", agent_model, f"{agent_in:,}", f"{agent_out:,}", f"${agent_cost:.4f}"],
    ]
    if sim_cost and sim_cost > 0:
        conv_rows.append(["Simulator", sim_model, "—", "—", f"${sim_cost:.4f}"])
    else:
        conv_rows.append(["Simulator", sim_model, "—", "—", "—"])
    _padded_table(lines, conv_headers, conv_rows)

    # ── Evaluation Cost ──
    by_stage_model = eval_info.get("by_stage_model", {})

    if by_stage_model:
        _a("## Evaluation Cost\n")
        _section_eval_table(lines, by_stage_model)
    else:
        # Fallback: show flat by_model if by_stage_model not available
        by_model = eval_info.get("by_model", {})
        _a("## Evaluation Cost\n")
        if by_model:
            _a("| Model | Cost |")
            _a("|-------|------|")
            for m in sorted(by_model):
                _a(f"| {_short(m)} | ${by_model[m]:.4f} |")
            _a(f"| **Total** | **${eval_info.get('cost_usd', 0):.4f}** |")
        else:
            _a(f"- **Total eval cost**: ${eval_info.get('cost_usd', 0):.4f}")
        _a("")

    # ── Summary ──
    _a("## Summary\n")
    total_cost = tu.get("total", {}).get("cost_usd", 0)
    eval_total = eval_info.get("cost_usd", 0)
    sum_headers = ["Category", "Cost"]
    sum_rows = [
        ["Agent", f"${agent_cost:.4f}"],
    ]
    if sim_cost and sim_cost > 0:
        sum_rows.append(["Simulator", f"${sim_cost:.4f}"])
    else:
        sum_rows.append(["Simulator", "—"])
    sum_rows.append(["Evaluation", f"${eval_total:.4f}"])
    sum_rows.append(["**Total**", f"**${total_cost:.4f}**"])
    _padded_table(lines, sum_headers, sum_rows)

    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────


def _short(model_name: str) -> str:
    """Shorten OpenRouter model name for column headers."""
    return model_name.split("/")[-1] if "/" in model_name else model_name


def _padded_table(lines: list[str], headers: list[str], rows: list[list[str]]):
    """Append a Markdown table with dynamically padded columns."""
    n_cols = len(headers)
    col_widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            col_widths[i] = max(col_widths[i], len(cell))

    def _row(cells: list[str]) -> str:
        parts = [f" {cells[i]:<{col_widths[i]}} " for i in range(n_cols)]
        return "|" + "|".join(parts) + "|"

    lines.append(_row(headers))
    lines.append("|" + "|".join("-" * (col_widths[i] + 2) for i in range(n_cols)) + "|")
    for row in rows:
        lines.append(_row(row))
    lines.append("")


def _section_eval_table(lines: list[str], by_stage_model: dict):
    """Build stage × model evaluation cost table with aligned columns."""
    _a = lines.append

    # Collect all model names across all stages
    all_models: list[str] = []
    seen: set[str] = set()
    for stage_costs in by_stage_model.values():
        for m in stage_costs:
            if m not in seen:
                all_models.append(m)
                seen.add(m)

    # Pre-compute all cell values to determine column widths
    model_totals: dict[str, float] = {m: 0.0 for m in all_models}
    grand_total = 0.0
    data_rows: list[list[str]] = []

    for stage, costs in by_stage_model.items():
        cells = [stage]
        stage_total = 0.0
        for m in all_models:
            c = costs.get(m, 0.0)
            model_totals[m] += c
            stage_total += c
            cells.append(f"${c:.4f}")
        grand_total += stage_total
        cells.append(f"${stage_total:.4f}")
        data_rows.append(cells)

    # Subtotal row
    sub_cells = ["**Subtotal**"]
    for m in all_models:
        sub_cells.append(f"**${model_totals[m]:.4f}**")
    sub_cells.append(f"**${grand_total:.4f}**")

    # Header labels
    headers = ["Stage"] + [_short(m) for m in all_models] + ["Total"]

    # Compute column widths (max across header, data rows, subtotal)
    n_cols = len(headers)
    col_widths = [len(h) for h in headers]
    for row_cells in data_rows:
        for i, cell in enumerate(row_cells):
            col_widths[i] = max(col_widths[i], len(cell))
    for i, cell in enumerate(sub_cells):
        col_widths[i] = max(col_widths[i], len(cell))

    # Build rows with padding
    def _row(cells: list[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            parts.append(f" {cell:<{col_widths[i]}} ")
        return "|" + "|".join(parts) + "|"

    _a(_row(headers))
    _a("|" + "|".join("-" * (col_widths[i] + 2) for i in range(n_cols)) + "|")
    for row_cells in data_rows:
        _a(_row(row_cells))
    _a(_row(sub_cells))
    _a("")
