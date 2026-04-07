"""Shared helpers for B-series backtest-engine eval scripts."""

from __future__ import annotations

import ast
import os

from common.shared_utils import (
    has_any,
    has_regex,
    workspace_csv_headers,
    workspace_files,
)


def workspace_has_csv_column_group(
    workspace_path: str,
    required_groups: list[list[str]],
) -> bool:
    """Return True if any CSV contains one column from each conceptual group."""
    for _, header in workspace_csv_headers(workspace_path):
        header_set = set(header)
        if all(
            any(candidate.lower() in header_set for candidate in group)
            for group in required_groups
        ):
            return True
    return False


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
        if has_any(
            record["name"], ["test", "verify", "lookahead", "look_ahead", "leak"]
        ):
            if has_any(
                record["code_lower"],
                ["assert", "future", "lookahead", "look_ahead", "leak"],
            ):
                return True
    return False
