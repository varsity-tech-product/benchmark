"""
Shared utilities for all MCP servers.
"""

import logging

import pandas as pd

logger = logging.getLogger(__name__)


def safe_float(val, decimals: int = 2) -> float | None:
    """Safely convert a value to float, rounded to N decimals. Returns None for NaN."""
    import math

    try:
        result = round(float(val), decimals)
        return None if math.isnan(result) else result
    except (TypeError, ValueError):
        return None


def safe_int(val) -> int | None:
    """Safely convert a value to int."""
    try:
        return int(float(val))
    except (TypeError, ValueError):
        return None


def has_data(df) -> bool:
    """Check if a DataFrame is non-None and non-empty."""
    return df is not None and not df.empty


def coerce_numeric(df: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Apply pd.to_numeric(errors='coerce') to specified columns in place."""
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def classify(
    value: float | None, thresholds: list[tuple[float, str]], default: str = "unknown"
) -> str:
    """
    Classify a numeric value by descending thresholds.

    Args:
        value: the number to classify
        thresholds: list of (threshold, label) sorted descending by threshold
        default: label to return if value is None or below all thresholds

    Example:
        classify(1.8, [(3.0, "strongly_bullish"), (1.5, "bullish"), (0.8, "neutral")])
        => "bullish"
    """
    if value is None:
        return default
    for threshold, label in thresholds:
        if value >= threshold:
            return label
    return default
