"""Shared helpers for S-series research eval scripts."""

from __future__ import annotations

import json
import os

from common.shared_utils import (
    collect_artifact_text,
    conversation_text,
    has_any,
    has_metric_numbers,
    read_text_excerpt,
    workspace_files,
    workspace_has_csv_columns,
)

__all__ = [
    "collect_artifact_text",
    "conversation_text",
    "has_any",
    "has_metric_numbers",
    "workspace_has_csv_columns",
]


def _read_text_excerpt(path: str, max_chars: int = 12000) -> str:
    """S-series wrapper: smaller budget to preserve late-file signal defs."""
    return read_text_excerpt(path, max_chars=max_chars)


def collect_evidence_text(
    workspace_path: str,
    tool_logs: list | None = None,
    conversation: list | None = None,
) -> str:
    """Collect tool, workspace, and conversation evidence as a single string.

    Unlike ``collect_artifact_text`` (which excludes conversation), this
    includes both conversation prose and workspace files.
    """
    parts: list[str] = []

    for log in tool_logs or []:
        parts.append(str(getattr(log, "name", "")))
        parts.append(str(getattr(log, "args", {})))
        parts.append(str(getattr(log, "result", "") or ""))

    for turn in conversation or []:
        parts.append(str(turn.get("role", "")))
        parts.append(str(turn.get("content", "")))

    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if not fname.endswith((".py", ".json", ".txt", ".md", ".csv", ".log")):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    parts.append(os.path.relpath(fpath, workspace_path))
                    parts.append(_read_text_excerpt(fpath))
                except (IOError, UnicodeDecodeError):
                    pass

    return "\n".join(parts).lower()


def has_regex(text: str, patterns: list[str]) -> bool:
    """S-series wrapper: includes re.MULTILINE for line-anchored patterns."""
    from common.shared_utils import has_regex as _base

    return _base(text, patterns, multiline=True)


def count_keyword_groups(text: str, keyword_groups: list[list[str]]) -> int:
    """Count how many conceptual groups appear in the evidence."""
    return sum(1 for group in keyword_groups if has_any(text, group))


def has_signal_definition(text: str) -> bool:
    """Detect whether a formal signal definition exists in code/data artifacts.

    Broadened to accept descriptive column names commonly used by agents
    (e.g., 'close_gt_sma200', 'regime', 'crossover') in addition to
    the canonical 'signal' column name.
    """
    patterns = [
        # Canonical: column literally named "signal"
        r"df\[[\"']signal[\"']\]\s*=",
        r"[a-z_][a-z0-9_]*\[[\"']signal[\"']\]\s*=",
        # Canonical: known signal variable names
        r"\b(?:alpha_signal|trend_signal|reversion_signal|microstructure_signal|cross_asset_signal|composite_signal)\s*=",
        # CSV header containing "signal"
        r"(?:^|,)\s*signal\s*(?:,|$)",
        # Broad: any column with "signal" as substring
        r"\[[\"'][a-z_]*signal[a-z_]*[\"']\]\s*=",
        # Broad: comparison-derived columns (close_gt_sma, price_above_ma, etc.)
        r"\[[\"'][a-z_]*(?:_gt_|_above_|_below_|_cross_)[a-z_]*[\"']\]\s*=",
        # Broad: common signal-related column names
        r"\[[\"'](?:regime|crossover|position|indicator|breakout)[\"']\]\s*=",
    ]
    return has_regex(text, patterns)


def has_metric_evidence(text: str) -> bool:
    """Detect signal-evaluation metrics or the new evaluate_signal tool."""
    metric_terms = [
        "evaluate_signal",
        "information coefficient",
        "ic decay",
        "ic_ir",
        "spearman",
        "quantile",
        "turnover",
        "hit rate",
        "signal_evaluation",
    ]
    return has_any(text, metric_terms)


def has_pnl_evidence(text: str) -> bool:
    """Detect rough PnL or return diagnostics."""
    pnl_terms = [
        "sharpe",
        "total return",
        "annualized return",
        "strategy_return",
        "rough pnl",
        "max drawdown",
        "cumulative return",
        "equity",
        "pnl",
    ]
    return has_any(text, pnl_terms)


def tool_called_with_method(
    tool_logs: list | None,
    tool_name: str,
    methods: list[str],
) -> bool:
    """Return True if a tool was called with one of the target method values."""
    allowed = {method.upper() for method in methods}
    for log in tool_logs or []:
        if getattr(log, "name", "") != tool_name:
            continue
        method = str(getattr(log, "args", {}).get("method", "")).upper()
        if method in allowed:
            return True
    return False


def _load_workspace_json_records(
    workspace_path: str,
    *,
    suffixes: tuple[str, ...] | None = None,
) -> list[dict]:
    """Load JSON records from workspace files."""
    records: list[dict] = []
    for fpath in workspace_files(workspace_path, suffixes=(".json",)):
        if suffixes and not os.path.basename(fpath).endswith(suffixes):
            continue
        try:
            with open(fpath) as fh:
                payload = json.load(fh)
        except (IOError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            records.append(
                {
                    "source": os.path.basename(fpath),
                    "data": payload,
                }
            )
    return records


def _load_tool_json_records(
    tool_logs: list | None,
    *,
    tool_names: tuple[str, ...] | None = None,
) -> list[dict]:
    """Load JSON records from tool results."""
    records: list[dict] = []
    for log in tool_logs or []:
        name = getattr(log, "name", "")
        if tool_names and name not in tool_names:
            continue
        result = getattr(log, "result", "")
        try:
            payload = json.loads(result)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            records.append({"source": name, "data": payload})
    return records


def collect_signal_evaluation_records(
    workspace_path: str,
    tool_logs: list | None = None,
) -> list[dict]:
    """Collect structured signal-evaluation payloads from files and tools."""
    records = _load_workspace_json_records(
        workspace_path,
        suffixes=("_signal_evaluation.json",),
    )
    records.extend(_load_tool_json_records(tool_logs, tool_names=("evaluate_signal",)))
    return [
        record
        for record in records
        if isinstance(record.get("data"), dict)
        and "signal_metrics" in record["data"]
        and "rough_pnl" in record["data"]
    ]


def collect_performance_metric_records(
    workspace_path: str,
    tool_logs: list | None = None,
) -> list[dict]:
    """Collect structured backtest/performance metric payloads."""
    records = _load_workspace_json_records(
        workspace_path,
        suffixes=("_analysis.json", "_metrics.json"),
    )
    records.extend(_load_tool_json_records(tool_logs))
    return [
        record
        for record in records
        if isinstance(record.get("data"), dict)
        and any(
            key in record["data"]
            for key in ("sharpe_ratio", "annual_return", "total_return", "max_drawdown")
        )
    ]


def signal_eval_has_quality_metrics(record: dict) -> bool:
    """Return True if a signal-evaluation payload contains non-trivial metrics."""
    payload = record.get("data", {})
    signal_metrics = payload.get("signal_metrics", {})
    quantile_analysis = payload.get("quantile_analysis", {})
    if not isinstance(signal_metrics, dict) or not isinstance(quantile_analysis, dict):
        return False
    return (
        isinstance(signal_metrics.get("turnover"), (int, float))
        and isinstance(signal_metrics.get("ic_mean"), (int, float))
        and isinstance(quantile_analysis.get("long_short_spread"), (int, float))
    )


def signal_eval_has_pnl(record: dict, *, min_observations: int = 10) -> bool:
    """Return True if a signal evaluation contains rough PnL diagnostics."""
    rough_pnl = record.get("data", {}).get("rough_pnl", {})
    if not isinstance(rough_pnl, dict):
        return False
    return (
        isinstance(rough_pnl.get("annualized_sharpe"), (int, float))
        and isinstance(rough_pnl.get("max_drawdown"), (int, float))
        and int(rough_pnl.get("num_observations", 0) or 0) >= min_observations
    )


def count_records_with_sources(records: list[dict]) -> int:
    """Count records by distinct source name."""
    return len({record.get("source", "") for record in records if record.get("source")})
