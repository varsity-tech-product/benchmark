"""Shared helpers for B-series backtest-engine eval scripts."""

from __future__ import annotations

import ast
import csv
import os
import re


def _read_text_excerpt(path: str, max_chars: int = 16000) -> str:
    """Read a bounded excerpt from a text file."""
    with open(path) as fh:
        content = fh.read()
    if len(content) <= max_chars:
        return content
    head = content[: max_chars // 2]
    tail = content[-(max_chars // 2) :]
    return head + "\n...\n" + tail


def collect_artifact_text(
    workspace_path: str,
    tool_logs: list | None = None,
) -> str:
    """Collect workspace artifacts and tool traces, excluding conversation prose."""
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
                try:
                    parts.append(os.path.relpath(fpath, workspace_path))
                    parts.append(_read_text_excerpt(fpath))
                except (IOError, UnicodeDecodeError):
                    continue

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


def workspace_files(
    workspace_path: str,
    *,
    suffixes: tuple[str, ...] | None = None,
) -> list[str]:
    """Return workspace files optionally filtered by suffix."""
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
                row = next(csv.reader(fh), [])
        except (IOError, UnicodeDecodeError, StopIteration, csv.Error):
            continue
        if row:
            headers.append(
                (
                    os.path.basename(fpath),
                    [str(col).strip().lower() for col in row if str(col).strip()],
                )
            )
    return headers


def workspace_has_csv_columns(workspace_path: str, required_columns: list[str]) -> bool:
    """Return True if any workspace CSV contains all required columns."""
    required = {col.lower() for col in required_columns}
    for _, header in workspace_csv_headers(workspace_path):
        if required.issubset(set(header)):
            return True
    return False


def workspace_has_csv_column_group(
    workspace_path: str,
    required_groups: list[list[str]],
) -> bool:
    """Return True if any CSV contains one column from each conceptual group."""
    for _, header in workspace_csv_headers(workspace_path):
        header_set = set(header)
        if all(any(candidate.lower() in header_set for candidate in group) for group in required_groups):
            return True
    return False


def tool_called(tool_logs: list | None, tool_name: str) -> bool:
    """Return True if a tool was called."""
    return any(getattr(log, "name", "") == tool_name for log in tool_logs or [])


def python_source_records(workspace_path: str) -> list[dict]:
    """Return parsed Python source artifacts from the workspace."""
    records: list[dict] = []
    for fpath in workspace_files(workspace_path, suffixes=(".py",)):
        try:
            with open(fpath) as fh:
                code = fh.read()
            tree = ast.parse(code)
        except (IOError, UnicodeDecodeError, SyntaxError):
            continue
        records.append(
            {
                "path": fpath,
                "name": os.path.basename(fpath).lower(),
                "code": code,
                "code_lower": code.lower(),
                "tree": tree,
            }
        )
    return records


def python_code_text(records: list[dict]) -> str:
    """Concatenate all Python source for regex/substring checks."""
    return "\n".join(record["code_lower"] for record in records)


def named_nodes(records: list[dict]) -> dict[str, set[str]]:
    """Collect lower-cased class and function names across Python artifacts."""
    classes: set[str] = set()
    functions: set[str] = set()
    for record in records:
        for node in ast.walk(record["tree"]):
            if isinstance(node, ast.ClassDef):
                classes.add(node.name.lower())
            elif isinstance(node, ast.FunctionDef):
                functions.add(node.name.lower())
    return {"classes": classes, "functions": functions}


def source_has_component(
    records: list[dict],
    *,
    name_keywords: list[str],
    code_patterns: list[str] | None = None,
) -> bool:
    """Return True if a component is evidenced by names or code patterns."""
    names = named_nodes(records)
    for keyword in name_keywords:
        keyword = keyword.lower()
        if any(keyword in name for name in names["classes"]):
            return True
        if any(keyword in name for name in names["functions"]):
            return True
        if any(keyword in record["name"] for record in records):
            return True

    if code_patterns and has_regex(python_code_text(records), code_patterns):
        return True
    return False


def _node_source_segment(record: dict, node: ast.AST) -> str:
    """Extract source lines for a node if line info is available."""
    start = getattr(node, "lineno", None)
    end = getattr(node, "end_lineno", None)
    if start is None:
        return ""
    lines = record["code"].splitlines()
    if end is None:
        end = start
    return "\n".join(lines[start - 1 : end]).lower()


def strategy_segments(records: list[dict]) -> list[str]:
    """Extract source segments likely to implement strategy logic."""
    segments: list[str] = []
    segment_keywords = (
        "strategy",
        "signal",
        "on_bar",
        "generate_signal",
        "next",
        "crossover",
        "reversion",
        "breakout",
        "alpha",
        "momentum",
        "trend",
    )
    for record in records:
        for node in ast.walk(record["tree"]):
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                name = getattr(node, "name", "").lower()
                if any(keyword in name for keyword in segment_keywords):
                    segment = _node_source_segment(record, node)
                    if segment:
                        segments.append(segment)
    return segments


def strategy_isolated_from_data_io(records: list[dict]) -> bool:
    """Return True if strategy segments avoid direct dataframe/file access."""
    segments = strategy_segments(records)
    if not segments:
        return False
    bad_terms = [
        "import pandas",
        "from pandas",
        "pd.read_csv",
        "read_csv(",
        "open(",
        "dataframe",
        "iterrows(",
        "itertuples(",
    ]
    return all(not has_any(segment, bad_terms) for segment in segments)


def has_sequential_replay(text: str) -> bool:
    """Detect evidence of bar-by-bar replay or callback-style iteration."""
    patterns = [
        r"\byield\b",
        r"for\s+\w+\s+in\s+\w+",
        r"\bon_bar\s*\(",
        r"\bnext_bar\s*\(",
        r"\bcurrent_bar\b",
        r"\bitertuples\s*\(",
        r"\biterrows\s*\(",
        r"\b__iter__\b",
    ]
    return has_regex(text, patterns)


def has_lookahead_verification(records: list[dict], artifact_text: str) -> bool:
    """Detect explicit verification tests for look-ahead prevention."""
    verification_patterns = [
        r"\bassert\b.*(?:future|lookahead|look_ahead|leak|spy)",
        r"\bspy[_ ]strategy\b",
        r"\btest_.*(?:lookahead|look_ahead|future|leak)",
        r"\bverify_.*(?:lookahead|look_ahead|future|leak)",
        r"\bfuture spike\b",
    ]
    if has_regex(artifact_text, verification_patterns):
        return True

    for record in records:
        if has_any(record["name"], ["test", "verify", "lookahead", "look_ahead", "leak"]):
            if has_any(record["code_lower"], ["assert", "future", "lookahead", "look_ahead", "leak"]):
                return True
    return False
