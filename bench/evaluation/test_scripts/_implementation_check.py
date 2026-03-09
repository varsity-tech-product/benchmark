"""Shared helpers for I-series implementation eval scripts."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path


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
    """Collect workspace artifacts and tool traces, including .cs files."""
    parts: list[str] = []

    for log in tool_logs or []:
        parts.append(str(getattr(log, "name", "")))
        parts.append(str(getattr(log, "args", {})))
        parts.append(str(getattr(log, "result", "") or ""))

    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if not fname.endswith((".cs", ".py", ".json", ".txt", ".md", ".csv", ".log")):
                    continue
                fpath = os.path.join(root, fname)
                try:
                    parts.append(os.path.relpath(fpath, workspace_path))
                    parts.append(_read_text_excerpt(fpath))
                except (IOError, UnicodeDecodeError):
                    continue

    return "\n".join(parts).lower()


def has_any(text: str, keywords: list[str]) -> bool:
    """Return True when any keyword is present as a substring."""
    return any(keyword.lower() in text for keyword in keywords)


def has_regex(text: str, patterns: list[str]) -> bool:
    """Return True when any regex pattern matches."""
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


# ── Reference data directory ──
_BENCH_ROOT = Path(__file__).parent.parent.parent
_REFERENCE_DIR = _BENCH_ROOT / "data" / "reference"


@dataclass
class MatchResult:
    """Result of trade log matching between reference and agent trades."""
    matched_count: int = 0
    unmatched_ref: int = 0
    extra_agent: int = 0
    entry_match_rate: float = 0.0
    direction_match_rate: float = 0.0
    exit_match_rate: float = 0.0
    pnl_correlation: float = 0.0
    total_ref_trades: int = 0
    total_agent_trades: int = 0
    ref_total_return: float = 0.0
    agent_total_return: float = 0.0
    matched_pairs: list = field(default_factory=list)

    def count_within_tolerance(self, tolerance: float = 0.10) -> bool:
        """Check if trade count is within tolerance of reference."""
        if self.total_ref_trades == 0:
            return self.total_agent_trades == 0
        ratio = abs(self.total_agent_trades - self.total_ref_trades) / self.total_ref_trades
        return ratio <= tolerance

    def return_within_tolerance(self, tolerance: float = 0.20) -> bool:
        """Check if total return is within tolerance of reference."""
        if abs(self.ref_total_return) < 1e-9:
            return abs(self.agent_total_return) < 1e-9
        ratio = abs(self.agent_total_return - self.ref_total_return) / abs(self.ref_total_return)
        return ratio <= tolerance


def load_reference_trades(task_id: str) -> list[dict]:
    """Load reference trade log from bench/data/reference/."""
    ref_path = _REFERENCE_DIR / f"{task_id}_reference_trades.json"
    if not ref_path.exists():
        return []
    with open(ref_path) as f:
        data = json.load(f)
    return data.get("trades", [])


def _ci_get_trade(d: dict, *keys, default=None):
    """Case-insensitive dict lookup across multiple candidate keys."""
    for key in keys:
        if key in d:
            return d[key]
        lower = key.lower()
        for k in d:
            if k.lower() == lower:
                return d[k]
    return default


def _normalize_trade(trade: dict) -> dict:
    """Normalize a trade dict to the snake_case schema expected by match_trades().

    Handles LEAN PascalCase (EntryTime, Direction, ProfitLoss) and
    already-normalized snake_case (entry_time, direction, net_pnl).
    """
    # If already has the key fields in snake_case, return as-is
    if "entry_time" in trade and "direction" in trade and "net_pnl" in trade:
        return trade

    # Direction: LEAN uses 0=Long/1=Short or string values
    raw_dir = _ci_get_trade(trade, "Direction", "direction", default="")
    if isinstance(raw_dir, int):
        direction = "Buy" if raw_dir == 0 else "Sell"
    else:
        d = str(raw_dir).lower()
        if d in ("long", "buy", "0"):
            direction = "Buy"
        elif d in ("short", "sell", "1"):
            direction = "Sell"
        else:
            direction = str(raw_dir)

    # Symbol: might be a string or an object with Value
    raw_sym = _ci_get_trade(trade, "Symbol", "symbol", default="")
    if isinstance(raw_sym, dict):
        symbol = raw_sym.get("Value", raw_sym.get("value", str(raw_sym)))
    else:
        symbol = str(raw_sym)

    profit_loss = float(_ci_get_trade(trade, "ProfitLoss", "gross_pnl", "net_pnl", default=0))
    total_fees = float(_ci_get_trade(trade, "TotalFees", default=0))

    return {
        "symbol": symbol,
        "direction": direction,
        "quantity": abs(float(_ci_get_trade(trade, "Quantity", "quantity", default=0))),
        "entry_time": str(_ci_get_trade(trade, "EntryTime", "entry_time", default="")),
        "entry_price": float(_ci_get_trade(trade, "EntryPrice", "entry_price", default=0)),
        "exit_time": str(_ci_get_trade(trade, "ExitTime", "exit_time", default="")),
        "exit_price": float(_ci_get_trade(trade, "ExitPrice", "exit_price", default=0)),
        "gross_pnl": profit_loss,
        "net_pnl": profit_loss - total_fees,
    }


def load_agent_trades(workspace_path: str) -> list[dict]:
    """Parse agent's trade log from workspace results.

    Normalizes LEAN PascalCase fields to the snake_case schema
    expected by match_trades().
    """
    trades_path = os.path.join(workspace_path, "results", "trades.json")
    if not os.path.exists(trades_path):
        return []
    try:
        with open(trades_path) as f:
            data = json.load(f)
        if isinstance(data, list):
            raw_trades = data
        else:
            raw_trades = data.get("trades", data.get("ClosedTrades", []))
        return [_normalize_trade(t) for t in raw_trades]
    except (json.JSONDecodeError, IOError):
        return []


def _parse_time(t: str | int | float) -> float:
    """Convert a time value to a comparable float (epoch seconds)."""
    if isinstance(t, (int, float)):
        # Assume millisecond epoch if large
        return t / 1000.0 if t > 1e12 else float(t)
    # Try ISO format parsing
    from datetime import datetime, timezone
    # Strip UTC indicators so strptime works with timezone-naive formats
    clean = str(t).strip()
    if clean.endswith("Z"):
        clean = clean[:-1]
    if clean.endswith("+00:00"):
        clean = clean[:-6]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(clean, fmt).replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except (ValueError, TypeError):
            continue
    return 0.0


def _bar_duration(time_tolerance_bars: int, resolution: str = "daily") -> float:
    """Get tolerance in seconds based on bar duration."""
    durations = {
        "minute": 60,
        "5minute": 300,
        "hour": 3600,
        "4hour": 14400,
        "daily": 86400,
    }
    base = durations.get(resolution, 86400)
    return time_tolerance_bars * base


def match_trades(
    ref_trades: list[dict],
    agent_trades: list[dict],
    time_tolerance_bars: int = 1,
    resolution: str = "daily",
) -> MatchResult:
    """Run the trade matching algorithm.

    For each reference trade:
      1. Find agent trades within +/-tolerance of entry_time
      2. Among candidates, pick one with same direction
      3. If found, mark as matched; compare exit_time and PnL
      4. If not found, mark as unmatched
    """
    result = MatchResult(
        total_ref_trades=len(ref_trades),
        total_agent_trades=len(agent_trades),
    )

    if not ref_trades:
        return result

    tolerance_secs = _bar_duration(time_tolerance_bars, resolution)
    used_agent_indices: set[int] = set()
    matched_ref_pnls: list[float] = []
    matched_agent_pnls: list[float] = []
    direction_matches = 0
    exit_matches = 0
    total_matched = 0

    for ref_trade in ref_trades:
        ref_entry = _parse_time(ref_trade.get("entry_time", 0))
        ref_dir = ref_trade.get("direction", "").lower()

        best_idx = None
        best_delta = float("inf")

        for i, agent_trade in enumerate(agent_trades):
            if i in used_agent_indices:
                continue
            agent_entry = _parse_time(agent_trade.get("entry_time", 0))
            delta = abs(agent_entry - ref_entry)
            if delta <= tolerance_secs and delta < best_delta:
                agent_dir = agent_trade.get("direction", "").lower()
                if agent_dir == ref_dir:
                    best_idx = i
                    best_delta = delta

        if best_idx is not None:
            used_agent_indices.add(best_idx)
            total_matched += 1
            direction_matches += 1  # Already matched by direction

            # Check exit timing
            ref_exit = _parse_time(ref_trade.get("exit_time", 0))
            agent_exit = _parse_time(agent_trades[best_idx].get("exit_time", 0))
            if abs(agent_exit - ref_exit) <= tolerance_secs:
                exit_matches += 1

            # Collect PnL for correlation
            ref_pnl = float(ref_trade.get("net_pnl", ref_trade.get("gross_pnl", 0)))
            agent_pnl = float(agent_trades[best_idx].get("net_pnl", agent_trades[best_idx].get("gross_pnl", 0)))
            matched_ref_pnls.append(ref_pnl)
            matched_agent_pnls.append(agent_pnl)

            result.matched_pairs.append((ref_trade, agent_trades[best_idx]))

    result.matched_count = total_matched
    result.unmatched_ref = len(ref_trades) - total_matched
    result.extra_agent = len(agent_trades) - total_matched

    if total_matched > 0:
        result.entry_match_rate = total_matched / len(ref_trades)
        result.direction_match_rate = direction_matches / total_matched
        result.exit_match_rate = exit_matches / total_matched

    # Compute PnL correlation
    if len(matched_ref_pnls) >= 3:
        try:
            mean_r = sum(matched_ref_pnls) / len(matched_ref_pnls)
            mean_a = sum(matched_agent_pnls) / len(matched_agent_pnls)
            cov = sum((r - mean_r) * (a - mean_a) for r, a in zip(matched_ref_pnls, matched_agent_pnls))
            var_r = sum((r - mean_r) ** 2 for r in matched_ref_pnls)
            var_a = sum((a - mean_a) ** 2 for a in matched_agent_pnls)
            if var_r > 0 and var_a > 0:
                result.pnl_correlation = cov / (var_r ** 0.5 * var_a ** 0.5)
        except (ZeroDivisionError, ValueError):
            pass

    # Compute total returns from reference summary if available
    ref_return = sum(float(t.get("net_pnl", t.get("gross_pnl", 0))) for t in ref_trades)
    agent_return = sum(float(t.get("net_pnl", t.get("gross_pnl", 0))) for t in agent_trades)
    result.ref_total_return = ref_return
    result.agent_total_return = agent_return

    return result


def compute_trade_log_score(match_result: MatchResult) -> float:
    """Apply the weighted scoring from S7.1.2."""
    score = 0.0
    # Trade count match (0.20)
    if match_result.count_within_tolerance(0.10):
        score += 0.20
    # Entry timing match (0.20)
    if match_result.entry_match_rate >= 0.80:
        score += 0.20
    # Direction match (0.15)
    if match_result.direction_match_rate >= 1.0:
        score += 0.15
    elif match_result.direction_match_rate >= 0.95:
        score += 0.10
    # Exit timing match (0.15)
    if match_result.exit_match_rate >= 0.70:
        score += 0.15
    # PnL alignment (0.10)
    if match_result.pnl_correlation > 0.85:
        score += 0.10
    # Return proximity (0.05)
    if match_result.return_within_tolerance(0.20):
        score += 0.05
    return score


def check_csharp_patterns(workspace_path: str, patterns: list[str]) -> dict[str, bool]:
    """Scan .cs files in workspace for expected code patterns."""
    results: dict[str, bool] = {p: False for p in patterns}
    if not workspace_path or not os.path.isdir(workspace_path):
        return results

    cs_text = ""
    for root, _, files in os.walk(workspace_path):
        for fname in sorted(files):
            if fname.endswith(".cs"):
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath) as f:
                        cs_text += f.read() + "\n"
                except (IOError, UnicodeDecodeError):
                    continue

    for pattern in patterns:
        if re.search(re.escape(pattern), cs_text, re.IGNORECASE):
            results[pattern] = True

    return results


def collect_lean_results(workspace_path: str) -> dict | None:
    """Parse LEAN output from /workspace/results/summary.json."""
    summary_path = os.path.join(workspace_path, "results", "summary.json")
    if not os.path.exists(summary_path):
        return None
    try:
        with open(summary_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return None
