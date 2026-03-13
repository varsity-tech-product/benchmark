"""Shared trace extraction utilities for QuantTutorBench.

Pure functions for extracting structured data from tool-call logs and
workspace files.  Used by both ``_save_run_state()`` (every run) and
``generate_reference.py`` (promote command).

Extracted from ``reference/generate_reference.py`` — logic unchanged.
"""

import json
import os

# ── Human-readable trace summary ────────────────────────────────


def _describe_action(tool_name: str, args: dict) -> str:
    """Generate a human-readable action description from a tool call."""
    if tool_name == "fetch_market_data":
        symbol = args.get("symbol", "?")
        period = args.get("period", "")
        return f"fetch {symbol} market data" + (f" ({period})" if period else "")
    if tool_name == "compute_indicator":
        indicator = args.get("indicator", "?")
        params = args.get("params", {})
        return f"compute {indicator}" + (f" {params}" if params else "")
    if tool_name == "run_backtest":
        strategy = args.get("strategy", "?")
        return f"run {strategy} backtest"
    if tool_name == "analyze_backtest_results":
        return "analyze backtest results"
    if tool_name == "compute_statistics":
        test = args.get("test", "?")
        return f"compute {test} statistic"
    if tool_name == "evaluate_signal":
        return "evaluate signal quality metrics"
    if tool_name == "plot_chart":
        return "create chart visualization"
    if tool_name == "shell_exec":
        cmd = args.get("command", "")
        if len(cmd) > 60:
            cmd = cmd[:57] + "..."
        return f"execute: {cmd}"
    if tool_name == "file_write":
        path = args.get("path", "?")
        return f"write file {path}"
    if tool_name == "file_read":
        path = args.get("path", "?")
        return f"read file {path}"
    if tool_name == "file_list":
        return "list directory"
    if tool_name == "search_docs":
        query = args.get("query", "?")
        return f'search docs for "{query}"'
    if tool_name == "get_environment_info":
        return "inspect environment"
    return f"call {tool_name}"


def _summarize_result(result: str, max_len: int = 150) -> str:
    """Truncate a tool result to a concise summary."""
    if not result:
        return ""
    try:
        data = json.loads(result)
        if isinstance(data, dict):
            summary_parts = []
            for k, v in list(data.items())[:6]:
                if isinstance(v, (int, float)):
                    summary_parts.append(f"{k}={v}")
                elif isinstance(v, str) and len(v) < 30:
                    summary_parts.append(f"{k}={v}")
                elif isinstance(v, list):
                    summary_parts.append(f"{k}=[{len(v)} items]")
                else:
                    summary_parts.append(k)
            return ", ".join(summary_parts)
    except (json.JSONDecodeError, TypeError):
        pass
    if len(result) > max_len:
        return result[: max_len - 3] + "..."
    return result


def build_trace_summary(tool_logs: list[dict]) -> list[dict]:
    """Build a human-readable trace summary from tool log dicts.

    Args:
        tool_logs: List of dicts with at least ``name``, ``args``,
            ``result`` keys (as produced by ``dataclasses.asdict(ToolCallLog)``).

    Returns:
        List of step dicts: ``{step, action, tool, result_summary}``.
    """
    summary = []
    for i, log in enumerate(tool_logs, 1):
        summary.append(
            {
                "step": i,
                "action": _describe_action(log["name"], log.get("args", {})),
                "tool": log["name"],
                "result_summary": _summarize_result(log.get("result", "")),
            }
        )
    return summary


# ── Key numerical results extraction ────────────────────────────


def extract_key_results(workspace_path: str, tool_logs: list[dict]) -> dict:
    """Extract key numerical results from workspace files and tool outputs.

    Scans two sources:
    1. JSON files in the workspace (e.g. backtest_metrics.json)
    2. Tool output strings from metric-producing tools

    Uses recursive extraction to capture nested structures (e.g.
    evaluate_signal's ``signal_metrics.ic_mean``).  Source 1 (workspace
    files = final saved state) takes precedence over Source 2 (tool
    output, possibly intermediate).

    Args:
        workspace_path: Path to the workspace/agent_files directory.
        tool_logs: List of tool log dicts (same format as build_trace_summary).

    Returns:
        Dict mapping dotted key paths to numeric values.
    """
    results: dict = {}

    def _collect_numeric_scalars(obj, prefix: str = ""):
        if isinstance(obj, dict):
            for key, value in obj.items():
                next_prefix = f"{prefix}.{key}" if prefix else str(key)
                _collect_numeric_scalars(value, next_prefix)
        elif isinstance(obj, list):
            for idx, value in enumerate(obj):
                _collect_numeric_scalars(value, f"{prefix}[{idx}]")
        elif isinstance(obj, (int, float)):
            results[prefix] = round(obj, 6) if isinstance(obj, float) else obj

    # --- Source 1: workspace JSON files (highest priority) ---
    if workspace_path and os.path.isdir(workspace_path):
        for fname in os.listdir(workspace_path):
            if not fname.endswith(".json"):
                continue
            fpath = os.path.join(workspace_path, fname)
            try:
                with open(fpath) as f:
                    data = json.load(f)
                _collect_numeric_scalars(data)
            except Exception:
                pass

    source1_snapshot = dict(results)

    # --- Source 2: tool output JSON ---
    _metric_tools = {
        "run_backtest",
        "analyze_backtest_results",
        "compute_statistics",
        "evaluate_signal",
    }
    for log in tool_logs:
        if log["name"] not in _metric_tools:
            continue
        try:
            data = json.loads(log.get("result", ""))
            if isinstance(data, dict):
                metrics = data.get("metrics", data)
                if isinstance(metrics, dict):
                    _collect_numeric_scalars(metrics)
        except (json.JSONDecodeError, TypeError):
            pass

    # Restore Source 1 values (workspace files = final state)
    results.update(source1_snapshot)

    return results
