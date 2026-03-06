"""Shared helpers for S-series research eval scripts."""

from __future__ import annotations

import csv
import json
import os
import re


def _read_text_excerpt(path: str, max_chars: int = 12000) -> str:
    """Read a bounded excerpt from a text file.

    For large files, keep both the head and the tail so late-file signal
    definitions do not disappear entirely.
    """
    with open(path) as f:
        content = f.read()
    if len(content) <= max_chars:
        return content
    head = content[: max_chars // 2]
    tail = content[-(max_chars // 2) :]
    return head + "\n...\n" + tail


def collect_evidence_text(
    workspace_path: str,
    tool_logs: list | None = None,
    conversation: list | None = None,
) -> str:
    """Collect tool, workspace, and conversation evidence as a single string."""
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
                rel_path = os.path.relpath(fpath, workspace_path)
                try:
                    parts.append(rel_path)
                    parts.append(_read_text_excerpt(fpath))
                except (IOError, UnicodeDecodeError):
                    pass

    return "\n".join(parts).lower()


def collect_artifact_text(
    workspace_path: str,
    tool_logs: list | None = None,
) -> str:
    """Collect only executable artifacts: workspace files + tool traces.

    Excludes assistant conversation so evals cannot be satisfied by prose
    alone. This is the primary evidence source for programmatic S-series checks.
    """
    parts: list[str] = []

    for log in tool_logs or []:
        parts.append(str(getattr(log, "name", "")))
        parts.append(str(getattr(log, "args", {})))
        parts.append(str(getattr(log, "result", "") or ""))

    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if not fname.endswith((".py", ".json", ".txt", ".md", ".csv", ".log")):
                    continue
                fpath = os.path.join(root, fname)
                rel_path = os.path.relpath(fpath, workspace_path)
                try:
                    parts.append(rel_path)
                    parts.append(_read_text_excerpt(fpath))
                except (IOError, UnicodeDecodeError):
                    pass

    return "\n".join(parts).lower()


def conversation_text(conversation: list | None = None, role: str | None = None) -> str:
    """Collect conversation text, optionally filtered by role."""
    snippets = []
    for turn in conversation or []:
        if role is not None and turn.get("role") != role:
            continue
        snippets.append(str(turn.get("content", "")))
    return "\n".join(snippets).lower()


def has_any(text: str, keywords: list[str]) -> bool:
    """Return True when any keyword is present as a substring."""
    return any(keyword.lower() in text for keyword in keywords)


def has_regex(text: str, patterns: list[str]) -> bool:
    """Return True when any regex pattern matches."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def count_keyword_groups(text: str, keyword_groups: list[list[str]]) -> int:
    """Count how many conceptual groups appear in the evidence."""
    return sum(1 for group in keyword_groups if has_any(text, group))


def has_signal_definition(text: str) -> bool:
    """Detect whether a formal signal definition exists in code/data artifacts."""
    patterns = [
        r"df\[[\"']signal[\"']\]\s*=",
        r"[a-z_][a-z0-9_]*\[[\"']signal[\"']\]\s*=",
        r"\b(?:alpha_signal|trend_signal|reversion_signal|microstructure_signal|cross_asset_signal|composite_signal)\s*=",
        r"(?:^|,)\s*signal\s*(?:,|$)",
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


def workspace_files(
    workspace_path: str,
    *,
    suffixes: tuple[str, ...] | None = None,
) -> list[str]:
    """Return workspace files matching the requested suffixes."""
    files: list[str] = []
    if not workspace_path or not os.path.isdir(workspace_path):
        return files

    for root, _, names in os.walk(workspace_path):
        for fname in sorted(names):
            if suffixes and not fname.endswith(suffixes):
                continue
            files.append(os.path.join(root, fname))
    return files


def workspace_csv_headers(workspace_path: str) -> list[tuple[str, list[str]]]:
    """Return CSV header rows for workspace CSV artifacts."""
    headers: list[tuple[str, list[str]]] = []
    for fpath in workspace_files(workspace_path, suffixes=(".csv",)):
        try:
            with open(fpath, newline="") as fh:
                reader = csv.reader(fh)
                row = next(reader, [])
            if row:
                headers.append(
                    (
                        os.path.basename(fpath),
                        [str(col).strip().lower() for col in row if str(col).strip()],
                    )
                )
        except (IOError, UnicodeDecodeError, StopIteration, csv.Error):
            continue
    return headers


def workspace_has_csv_columns(workspace_path: str, required_columns: list[str]) -> bool:
    """Return True if any workspace CSV contains all required columns."""
    required = {col.lower() for col in required_columns}
    for _, header in workspace_csv_headers(workspace_path):
        if required.issubset(set(header)):
            return True
    return False


def tool_called(tool_logs: list | None, tool_name: str) -> bool:
    """Return True if a tool was called successfully or unsuccessfully."""
    return any(getattr(log, "name", "") == tool_name for log in tool_logs or [])


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


def has_metric_numbers(text: str, keyword_groups: list[list[str]]) -> bool:
    """Return True when at least two metric groups have nearby numeric values."""
    matched_groups = 0
    for group in keyword_groups:
        if any(
            re.search(
                rf"{re.escape(keyword)}[^0-9\-]{{0,40}}-?\d+(?:\.\d+)?",
                text,
                re.IGNORECASE,
            )
            for keyword in group
        ):
            matched_groups += 1
    return matched_groups >= 2
