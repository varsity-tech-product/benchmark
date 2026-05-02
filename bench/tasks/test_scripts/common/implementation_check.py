"""Shared helpers for I-series implementation eval scripts."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path


def collect_artifact_text(
    workspace_path: str,
    tool_logs: list | None = None,
) -> str:
    """Collect workspace artifacts and tool traces, including .cs files."""
    from common.shared_utils import collect_artifact_text as _cat

    return _cat(workspace_path, tool_logs, extra_suffixes=(".cs",))


# ── Reference data directory ──
_BENCH_ROOT = Path(__file__).parent.parent.parent.parent
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
        ratio = (
            abs(self.total_agent_trades - self.total_ref_trades) / self.total_ref_trades
        )
        return ratio <= tolerance

    def return_within_tolerance(self, tolerance: float = 0.20) -> bool:
        """Check if total return is within tolerance of reference."""
        if abs(self.ref_total_return) < 1e-9:
            return abs(self.agent_total_return) < 1e-9
        ratio = abs(self.agent_total_return - self.ref_total_return) / abs(
            self.ref_total_return
        )
        return ratio <= tolerance


def load_reference_trades(task_id: str, run_id: str | None = None) -> list[dict]:
    """Load reference trade log from bench/data/reference/.

    For multi-run tasks, use run_id to load a specific run's trades:
        {task_id}_reference_trades_{run_id}.json
    """
    if run_id:
        ref_path = _REFERENCE_DIR / f"{task_id}_reference_trades_{run_id}.json"
    else:
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


def _normalize_symbol_value(raw_sym, raw_symbols=None) -> str:
    """Normalize a LEAN symbol field into a clean comparable ticker.

    Handles:
    - ``Symbol`` / ``symbol`` as string or ``{"Value": ...}``
    - ``symbols`` arrays used by LEAN closedTrades
    - LEAN suffixes like ``"ADAUSDT 18R"``
    """
    symbol = ""
    if isinstance(raw_sym, dict):
        symbol = raw_sym.get("Value", raw_sym.get("value", str(raw_sym)))
    elif raw_sym is not None:
        symbol = str(raw_sym)

    if not symbol and isinstance(raw_symbols, list):
        values = []
        for entry in raw_symbols:
            if isinstance(entry, dict):
                val = entry.get("value", entry.get("Value", ""))
            else:
                val = str(entry)
            val = str(val).strip()
            if val:
                values.append(val)
        if len(values) == 1:
            symbol = values[0]
        elif values:
            symbol = " + ".join(values)

    symbol = str(symbol).strip()
    if not symbol:
        return ""
    return symbol.split()[0]


def _normalize_trade(trade: dict) -> dict:
    """Normalize a trade dict to the snake_case schema expected by match_trades().

    Handles LEAN PascalCase (EntryTime, Direction, ProfitLoss) and
    already-normalized snake_case (entry_time, direction, net_pnl).
    """
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
    raw_symbols = _ci_get_trade(trade, "Symbols", "symbols", default=None)
    symbol = _normalize_symbol_value(raw_sym, raw_symbols)

    profit_loss = float(
        _ci_get_trade(trade, "ProfitLoss", "gross_pnl", "net_pnl", default=0)
    )
    total_fees = float(_ci_get_trade(trade, "TotalFees", default=0))

    return {
        "symbol": symbol,
        "direction": direction,
        "quantity": abs(float(_ci_get_trade(trade, "Quantity", "quantity", default=0))),
        "entry_time": str(_ci_get_trade(trade, "EntryTime", "entry_time", default="")),
        "entry_price": float(
            _ci_get_trade(trade, "EntryPrice", "entry_price", default=0)
        ),
        "exit_time": str(_ci_get_trade(trade, "ExitTime", "exit_time", default="")),
        "exit_price": float(_ci_get_trade(trade, "ExitPrice", "exit_price", default=0)),
        "gross_pnl": profit_loss,
        "net_pnl": profit_loss - total_fees,
    }


def load_agent_trades(workspace_path: str) -> list[dict]:
    """Parse agent's trade log from workspace results.

    Searches multiple locations (in priority order) because LEAN output
    naming varies across builds and ``run_backtest.sh`` may fail to
    extract a standalone ``trades.json``.

    Normalizes LEAN PascalCase fields to the snake_case schema
    expected by match_trades().
    """
    import glob as _glob

    results_dir = os.path.join(workspace_path, "results")
    if not os.path.isdir(results_dir):
        return []

    # 1. LEAN-native closedTrades from main result JSON
    ct = _extract_closed_trades_from_main_json(results_dir)
    if ct:
        return [_normalize_trade(t) for t in ct]

    # 2. Standard extracted name (run_backtest.sh copy_result output)
    raw_trades = _try_load_trades_file(os.path.join(results_dir, "trades.json"))
    if raw_trades is not None:
        return [_normalize_trade(t) for t in raw_trades]

    # 3. LEAN-native pattern (*-trades.json)
    for f in _glob.glob(os.path.join(results_dir, "*-trades.json")):
        raw_trades = _try_load_trades_file(f)
        if raw_trades is not None:
            return [_normalize_trade(t) for t in raw_trades]

    # 4. Extract closedTrades from summary.json
    for candidate in [os.path.join(results_dir, "summary.json")] + _glob.glob(
        os.path.join(results_dir, "*-summary.json")
    ):
        ct = _extract_closed_trades(candidate)
        if ct:
            return [_normalize_trade(t) for t in ct]

    # 5. Pair round-trips from order events (lowest confidence)
    paired = _pair_trades_from_orders(results_dir)
    if paired:
        return paired

    return []


def _try_load_trades_file(path: str) -> list | None:
    """Load a trades JSON file, returning the raw trade list or None."""
    if not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return data.get("trades", data.get("ClosedTrades", []))
    except (json.JSONDecodeError, IOError):
        return None


def _extract_closed_trades(path: str) -> list:
    """Extract totalPerformance.closedTrades from a LEAN JSON file."""
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            data = json.load(f)
        return data.get("totalPerformance", {}).get("closedTrades", [])
    except (json.JSONDecodeError, IOError):
        return []


def _extract_closed_trades_from_main_json(results_dir: str) -> list:
    """Find the main LEAN output JSON (prefer result.json, else largest JSON)."""
    import glob as _glob

    # Prefer LEAN main JSONs first, then the deterministic result.json.
    native_candidates = []
    result_json = os.path.join(results_dir, "result.json")

    _SKIP_SUFFIXES = ("-summary.json", "-order-events.json", "-log.txt")
    _SKIP_PREFIXES = ("data-monitor", "succeeded-data", "failed-data")
    best, best_size = None, 0
    for f in _glob.glob(os.path.join(results_dir, "*.json")):
        name = os.path.basename(f)
        if any(name.endswith(s) for s in _SKIP_SUFFIXES):
            continue
        if any(name.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if name in ("trades.json", "orders.json", "summary.json", "result.json"):
            continue
        sz = os.path.getsize(f)
        if sz > best_size:
            best, best_size = f, sz

    if best and best_size > 10000:
        native_candidates.append(best)
    if os.path.exists(result_json):
        native_candidates.append(result_json)

    for path in native_candidates:
        ct = _extract_closed_trades(path)
        if ct:
            return ct
    return []


def _pair_trades_from_orders(results_dir: str) -> list[dict]:
    """Pair filled orders into round-trip trades using reference-compatible FIFO logic.

    This intentionally mirrors the simplified pairing approach used by the
    reference-trade generator so that fallback agent trades remain comparable
    to the benchmark's reference trade files.
    """
    import glob as _glob
    from collections import defaultdict

    orders_path = os.path.join(results_dir, "orders.json")
    if not os.path.exists(orders_path):
        # Try LEAN-native name
        candidates = _glob.glob(os.path.join(results_dir, "*-order-events.json"))
        if not candidates:
            return []
        orders_path = candidates[0]

    try:
        with open(orders_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    # Parse filled orders (reuse logic from load_agent_orders)
    if isinstance(data, dict):
        if "Orders" in data or "orders" in data:
            data = data.get("Orders", data.get("orders", {}))
        if isinstance(data, dict):
            raw_orders = list(data.values())
        else:
            raw_orders = data
    else:
        raw_orders = data

    by_symbol = defaultdict(list)
    for o in raw_orders:
        status = _ci_get_trade(o, "Status", "status", default=0)
        if isinstance(status, int) and status != 3:
            continue
        if isinstance(status, str) and status.lower() not in ("filled", "3"):
            continue
        sym = _normalize_symbol_value(
            _ci_get_trade(o, "Symbol", "symbol", default=""),
            _ci_get_trade(o, "Symbols", "symbols", default=None),
        ) or _normalize_symbol_value(
            _ci_get_trade(o, "symbolValue", "SymbolValue", default=""),
            None,
        )
        raw_dir = _ci_get_trade(o, "Direction", "direction", default=0)
        if isinstance(raw_dir, int):
            direction = "Buy" if raw_dir == 0 else "Sell"
        else:
            d = str(raw_dir).lower()
            direction = "Buy" if d in ("long", "buy", "0") else "Sell"
        by_symbol[str(sym)].append(
            {
                "direction": direction,
                "quantity": abs(
                    float(
                        _ci_get_trade(
                            o, "Quantity", "fillQuantity", "quantity", default=0
                        )
                    )
                ),
                "fill_price": float(
                    _ci_get_trade(
                        o, "Price", "fillPrice", "fill_price", "price", default=0
                    )
                ),
                "time": _ci_get_trade(o, "Time", "time", default=""),
            }
        )

    trades = []
    for sym, sym_orders in by_symbol.items():
        sym_orders.sort(key=lambda x: _parse_time(x["time"]))
        pending = None
        for order in sym_orders:
            if pending is None:
                pending = order
                continue

            if order["direction"] != pending["direction"]:
                matched_qty = min(float(pending["quantity"]), float(order["quantity"]))
                pnl = (order["fill_price"] - pending["fill_price"]) * matched_qty
                if pending["direction"] == "Sell":
                    pnl = -pnl
                trades.append(
                    {
                        "symbol": sym,
                        "direction": pending["direction"],
                        "quantity": matched_qty,
                        "entry_time": pending["time"],
                        "entry_price": pending["fill_price"],
                        "exit_time": order["time"],
                        "exit_price": order["fill_price"],
                        "gross_pnl": pnl,
                        "net_pnl": pnl,
                        "_source": "order_pairing",
                    }
                )
                pending = None
            else:
                # Same direction again: treat as updated entry, mirroring
                # generate_lean_reference.py's simplified pairing.
                pending = order

    return trades


def _parse_time(t: str | int | float) -> float:
    """Convert a time value to a comparable float (epoch seconds)."""
    if isinstance(t, (int, float)):
        # Assume millisecond epoch if large
        return t / 1000.0 if t > 1e12 else float(t)
    # Try ISO format parsing
    from datetime import datetime, timezone

    # Strip UTC indicators so strptime works with timezone-naive formats
    clean = str(t).strip()
    # Handle numeric strings emitted by fallback order pairing, e.g. "1641513600.0"
    try:
        numeric = float(clean)
        return numeric / 1000.0 if numeric > 1e12 else numeric
    except (ValueError, TypeError):
        pass
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
        # Normalize symbol for comparison (strip LEAN suffixes like " 18R")
        ref_sym_raw = str(ref_trade.get("symbol", "")).strip()
        ref_sym = ref_sym_raw.split()[0].upper() if ref_sym_raw else ""

        best_idx = None
        best_delta = float("inf")

        for i, agent_trade in enumerate(agent_trades):
            if i in used_agent_indices:
                continue
            # Symbol check: if both trades have symbols, they must match
            agent_sym_raw = str(agent_trade.get("symbol", "")).strip()
            agent_sym = agent_sym_raw.split()[0].upper() if agent_sym_raw else ""
            if ref_sym and agent_sym and ref_sym != agent_sym:
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
            agent_pnl = float(
                agent_trades[best_idx].get(
                    "net_pnl", agent_trades[best_idx].get("gross_pnl", 0)
                )
            )
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
            cov = sum(
                (r - mean_r) * (a - mean_a)
                for r, a in zip(matched_ref_pnls, matched_agent_pnls)
            )
            var_r = sum((r - mean_r) ** 2 for r in matched_ref_pnls)
            var_a = sum((a - mean_a) ** 2 for a in matched_agent_pnls)
            if var_r > 0 and var_a > 0:
                result.pnl_correlation = cov / (var_r**0.5 * var_a**0.5)
        except (ZeroDivisionError, ValueError):
            pass

    # Compute total returns from reference summary if available
    ref_return = sum(float(t.get("net_pnl", t.get("gross_pnl", 0))) for t in ref_trades)
    agent_return = sum(
        float(t.get("net_pnl", t.get("gross_pnl", 0))) for t in agent_trades
    )
    result.ref_total_return = ref_return
    result.agent_total_return = agent_return

    return result


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
    """Parse LEAN statistics from workspace results.

    Searches multiple file patterns because ``run_backtest.sh`` may fail
    to copy the summary to the standard name, and LEAN naming varies
    across builds (``*-summary.json`` vs ``*-statistics.json``).
    """
    import glob as _glob

    results_dir = os.path.join(workspace_path, "results")
    if not os.path.isdir(results_dir):
        return None

    # Try in priority order: standard name, LEAN summary, legacy statistics
    candidates = [os.path.join(results_dir, "summary.json")]
    candidates += sorted(_glob.glob(os.path.join(results_dir, "*-summary.json")))
    candidates += sorted(_glob.glob(os.path.join(results_dir, "*-statistics.json")))

    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

    return None


# ════════════════════════════════════════════════════════════════════
# Multi-Layer Behavioral Evaluation
# ════════════════════════════════════════════════════════════════════


# ── Reference data loaders ──


def load_reference_signals(task_id: str) -> dict:
    """Load reference signals from bench/data/reference/I0X_reference_signals.json."""
    path = _REFERENCE_DIR / f"{task_id}_reference_signals.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


def load_reference_positions(task_id: str) -> dict:
    """Reconstruct reference positions from reference trades at runtime.

    No separate positions file needed — avoids reference drift.
    """
    ref_trades = load_reference_trades(task_id)
    if not ref_trades:
        return {"task_id": task_id, "positions": {}}

    # Determine date range from the trades file's parent JSON
    ref_path = _REFERENCE_DIR / f"{task_id}_reference_trades.json"
    import sys as _sys

    _sys.path.insert(0, str(_BENCH_ROOT))
    from config.benchmark_dates import BENCH_END, BENCH_START

    start_date = BENCH_START
    end_date = BENCH_END
    if ref_path.exists():
        try:
            with open(ref_path) as f:
                meta = json.load(f)
            start_date = meta.get("start_date", start_date)
            end_date = meta.get("end_date", end_date)
        except (json.JSONDecodeError, IOError):
            pass

    positions = reconstruct_positions(ref_trades, start_date, end_date)
    return {"task_id": task_id, "positions": positions}


def load_reference_summary(task_id: str, run_id: str | None = None) -> dict:
    """Load reference summary from bench/data/reference/I0X_reference_summary.json.

    If run_id is provided, also tries {task_id}_reference_summary_{run_id}.json
    before falling back to the default summary.
    """
    if run_id:
        run_path = _REFERENCE_DIR / f"{task_id}_reference_summary_{run_id}.json"
        if run_path.exists():
            try:
                with open(run_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                pass
    path = _REFERENCE_DIR / f"{task_id}_reference_summary.json"
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {}


# ── Agent data extraction ──


def load_agent_orders(workspace_path: str) -> list[dict]:
    """Parse orders.json from agent workspace, normalize PascalCase."""
    orders_path = os.path.join(workspace_path, "results", "orders.json")
    if not os.path.exists(orders_path):
        return []
    try:
        with open(orders_path) as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        return []

    # LEAN orders can be a dict keyed by order_id or a list
    if isinstance(data, dict):
        # Could be nested under "Orders" key or be the orders dict itself
        if "Orders" in data or "orders" in data:
            data = data.get("Orders", data.get("orders", {}))
        if isinstance(data, dict):
            raw_orders = list(data.values())
        else:
            raw_orders = data
    else:
        raw_orders = data

    orders = []
    for o in raw_orders:
        # Normalize each order to consistent keys
        status = _ci_get_trade(o, "Status", "status", default=0)
        # Filter to filled orders (status 3 in LEAN)
        if isinstance(status, int) and status != 3:
            continue
        if isinstance(status, str) and status.lower() not in ("filled", "3"):
            continue

        # Symbol: prefer symbolValue (clean ticker), fall back to symbol
        # with LEAN suffix stripped (e.g., "ADAUSDT 18R" → "ADAUSDT")
        sym = _ci_get_trade(o, "symbolValue", "SymbolValue", default="")
        if not sym:
            sym = _ci_get_trade(o, "Symbol", "symbol", default="")
            if isinstance(sym, dict):
                sym = sym.get("Value", sym.get("value", str(sym)))
            sym = str(sym).split()[0]  # strip LEAN suffix

        raw_dir = _ci_get_trade(o, "Direction", "direction", default=0)
        if isinstance(raw_dir, int):
            direction = "Buy" if raw_dir == 0 else "Sell"
        else:
            d = str(raw_dir).lower()
            direction = "Buy" if d in ("long", "buy", "0") else "Sell"

        orders.append(
            {
                "symbol": str(sym),
                "direction": direction,
                "quantity": abs(
                    float(
                        _ci_get_trade(
                            o, "Quantity", "fillQuantity", "quantity", default=0
                        )
                    )
                ),
                "fill_price": float(
                    _ci_get_trade(
                        o, "Price", "fillPrice", "fill_price", "price", default=0
                    )
                ),
                "time": _ci_get_trade(o, "Time", "time", default=""),
            }
        )

    return orders


def reconstruct_positions(
    orders_or_trades: list[dict],
    start_date: str,
    end_date: str,
) -> dict[str, list[dict]]:
    """Build daily position series {symbol: [{date, quantity}]}.

    From orders: cumulative fill tracking, forward-filled daily.
    From trades: approximate from entry_time→exit_time spans.
    """
    from collections import defaultdict

    start_d = _parse_date(start_date)
    end_d = _parse_date(end_date)
    if start_d is None or end_d is None:
        return {}

    # Detect if these are orders (have 'time' + 'fill_price') or trades (have 'entry_time')
    sample = orders_or_trades[0] if orders_or_trades else {}
    is_trades = "entry_time" in sample

    # Generate all dates in range
    from datetime import timedelta

    all_dates = []
    d = start_d
    while d <= end_d:
        all_dates.append(d)
        d += timedelta(days=1)

    positions_by_sym: dict[str, dict] = defaultdict(lambda: {d: 0.0 for d in all_dates})

    if is_trades:
        # From trades: position = signed quantity from entry to exit
        for trade in orders_or_trades:
            sym = trade.get("symbol", "")
            direction = trade.get("direction", "Buy")
            qty = float(trade.get("quantity", 0))
            signed_qty = qty if direction.lower() in ("buy", "long") else -qty

            entry_d = _parse_date(trade.get("entry_time", ""))
            exit_d = _parse_date(trade.get("exit_time", ""))
            if entry_d is None or exit_d is None:
                continue

            for d in all_dates:
                if entry_d <= d < exit_d:
                    positions_by_sym[sym][d] += signed_qty
    else:
        # From orders: cumulative position tracking per symbol
        sym_net: dict[str, float] = defaultdict(float)
        # Sort orders by time
        sorted_orders = sorted(orders_or_trades, key=lambda o: o.get("time", ""))

        # Build event list: (date, symbol, qty_change)
        events: list[tuple] = []
        for o in sorted_orders:
            sym = o.get("symbol", "")
            direction = o.get("direction", "Buy")
            qty = float(o.get("quantity", 0))
            signed_delta = qty if direction.lower() in ("buy", "long") else -qty
            order_d = _parse_date(o.get("time", ""))
            if order_d is not None:
                events.append((order_d, sym, signed_delta))

        events.sort(key=lambda e: e[0])

        # Walk through dates, applying events
        event_idx = 0
        for d in all_dates:
            while event_idx < len(events) and events[event_idx][0] <= d:
                _, sym, delta = events[event_idx]
                sym_net[sym] += delta
                event_idx += 1
            for sym, net_qty in sym_net.items():
                positions_by_sym[sym][d] = net_qty

    # Convert to output format
    result: dict[str, list[dict]] = {}
    for sym, date_map in positions_by_sym.items():
        entries = []
        for d in all_dates:
            qty = date_map.get(d, 0.0)
            if qty != 0.0:
                entries.append({"date": str(d), "quantity": round(qty, 6)})
        if entries:
            result[sym] = entries

    return result


def _parse_date(s) -> "date | None":
    """Parse a date string or timestamp to a date object."""

    if s is None or s == "":
        return None
    if isinstance(s, (int, float)):
        ts = s / 1000.0 if s > 1e12 else float(s)
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    clean = str(s).strip()
    try:
        numeric = float(clean)
        ts = numeric / 1000.0 if numeric > 1e12 else numeric
        return datetime.fromtimestamp(ts, tz=timezone.utc).date()
    except (ValueError, TypeError, OSError):
        pass
    if clean.endswith("Z"):
        clean = clean[:-1]
    if clean.endswith("+00:00"):
        clean = clean[:-6]
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(clean, fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def load_agent_summary(workspace_path: str) -> dict:
    """Parse summary.json into standardized metrics dict."""
    lean = collect_lean_results(workspace_path)
    results_dir = os.path.join(workspace_path, "results")

    def _coerce_float(s, default=0.0):
        import re as _re

        if isinstance(s, (int, float)):
            return float(s)
        text = str(s).strip()
        if not text:
            return default
        text = text.replace(",", "")
        match = _re.search(r"-?\d+(?:\.\d+)?", text)
        return float(match.group(0)) if match else default

    def _pct(s):
        if isinstance(s, (int, float)):
            return float(s)
        return _coerce_float(str(s).replace("%", "").strip(), 0.0)

    # LEAN summary can have various key formats
    stats = lean if isinstance(lean, dict) else {}
    # Try nested Statistics key
    if "Statistics" in stats:
        stats = stats["Statistics"]
    elif "statistics" in stats:
        stats = stats["statistics"]

    sharpe = _coerce_float(
        stats.get("Sharpe Ratio", stats.get("sharpe_ratio", "0")), 0.0
    )
    total_return = _pct(stats.get("Net Profit", stats.get("total_return", "0%")))
    drawdown = _pct(stats.get("Drawdown", stats.get("max_drawdown", "0%")))
    win_rate_raw = _pct(stats.get("Win Rate", stats.get("win_rate", "0%")))
    total_trades = int(
        _coerce_float(stats.get("Total Trades", stats.get("total_trades", "0")), 0.0)
    )

    # Fallback for LEAN builds where summary.statistics is empty but result.json
    # still contains runtime stats and full equity/drawdown charts.
    if (sharpe == 0.0 and total_return == 0.0 and drawdown == 0.0) and os.path.isdir(
        results_dir
    ):
        result_path = os.path.join(results_dir, "result.json")
        if os.path.exists(result_path):
            try:
                with open(result_path) as f:
                    result = json.load(f)
            except (json.JSONDecodeError, IOError):
                result = {}

            runtime = result.get("runtimeStatistics", {})
            total_return = _pct(runtime.get("Return", total_return))

            charts = result.get("charts", {})
            drawdown_chart = charts.get("Drawdown", {})
            drawdown_series = drawdown_chart.get(
                "series", drawdown_chart.get("Series", {})
            )
            dd_values = drawdown_series.get("Equity Drawdown", {}).get(
                "values",
                drawdown_series.get("Equity Drawdown", {}).get("Values", []),
            )
            if dd_values:
                drawdown = abs(min(point[1] for point in dd_values if len(point) >= 2))

            strat_chart = charts.get("Strategy Equity", {})
            strat_series = strat_chart.get("series", strat_chart.get("Series", {}))
            equity_values = strat_series.get("Equity", {}).get(
                "values",
                strat_series.get("Equity", {}).get("Values", []),
            )
            if equity_values and sharpe == 0.0:
                closes = [
                    float(v[4])
                    for v in equity_values
                    if len(v) >= 5 and float(v[4]) != 0.0
                ]
                if len(closes) >= 2:
                    import math

                    returns = [
                        (b - a) / abs(a) for a, b in zip(closes, closes[1:]) if a != 0
                    ]
                    if returns:
                        mean = sum(returns) / len(returns)
                        if len(returns) > 1:
                            var = sum((r - mean) ** 2 for r in returns) / (
                                len(returns) - 1
                            )
                            std = math.sqrt(var)
                            if std > 0:
                                sharpe = (mean / std) * math.sqrt(365)

    if total_trades == 0:
        total_trades = len(load_agent_trades(workspace_path))

    win_rate = win_rate_raw / 100.0 if win_rate_raw > 1 else win_rate_raw

    return {
        "total_return_pct": total_return,
        "sharpe_ratio": float(sharpe or 0.0),
        "max_drawdown_pct": drawdown,
        "total_trades": total_trades,
        "win_rate": win_rate,
    }


# ── Layer scoring (continuous 0.0–1.0) ──


def score_signal_agreement(
    ref_signals: dict,
    agent_positions: dict[str, list[dict]],
    warmup_days: int = 0,
) -> float:
    """Compare reference signal direction vs sign(agent_position).

    Per (date, symbol): +1.0 if match, +0.3 if one is 0 (missed signal
    or flat when should be active), +0.0 if oppose.
    Returns weighted mean. Range [0.0, 1.0].

    warmup_days: skip the first N signal entries per symbol (grace period
    for indicator warm-up differences between reference and agent).
    """
    signals_by_sym = ref_signals.get("signals", {})
    if not signals_by_sym:
        return 0.0

    total_score = 0.0
    total_count = 0

    for sym, sig_list in signals_by_sym.items():
        # Build agent position lookup for this symbol
        agent_pos_map: dict[str, float] = {}
        for entry in agent_positions.get(sym, []):
            agent_pos_map[entry["date"]] = entry["quantity"]

        for idx, sig_entry in enumerate(sig_list):
            if idx < warmup_days:
                continue  # skip warmup grace period
            date_str = sig_entry["date"]
            ref_sig = sig_entry["signal"]  # +1, -1, or 0
            agent_qty = agent_pos_map.get(date_str, 0.0)
            agent_sig = 1 if agent_qty > 0 else (-1 if agent_qty < 0 else 0)

            if ref_sig == agent_sig:
                total_score += 1.0
            elif ref_sig == 0 or agent_sig == 0:
                total_score += 0.3  # missed signal or unnecessary flat
            else:
                total_score += 0.0  # opposing directions

            total_count += 1

    return total_score / total_count if total_count > 0 else 0.0


def score_position_overlap(
    ref_positions: dict,
    agent_positions: dict[str, list[dict]],
) -> float:
    """Compare positions per symbol per day via direction + size.

    Split into two components:
      direction_agreement (0.70): sign(ref) == sign(agent)
      size_similarity     (0.30): min(|ref|,|agent|) / max(|ref|,|agent|)
    This avoids false positives from tiny positions matching direction.
    Range [0.0, 1.0].
    """
    ref_pos = ref_positions.get("positions", {})
    if not ref_pos and not agent_positions:
        return 0.0

    eps = 1e-9
    dir_matches = 0
    dir_total = 0
    size_total = 0.0
    size_count = 0

    all_syms = set(ref_pos.keys()) | set(agent_positions.keys())

    for sym in all_syms:
        ref_map: dict[str, float] = {
            e["date"]: e["quantity"] for e in ref_pos.get(sym, [])
        }
        agent_map: dict[str, float] = {
            e["date"]: e["quantity"] for e in agent_positions.get(sym, [])
        }
        all_dates = set(ref_map.keys()) | set(agent_map.keys())

        for d in all_dates:
            r = ref_map.get(d, 0.0)
            a = agent_map.get(d, 0.0)

            # Direction agreement
            r_sign = 1 if r > 0 else (-1 if r < 0 else 0)
            a_sign = 1 if a > 0 else (-1 if a < 0 else 0)
            dir_total += 1
            if r_sign == a_sign:
                dir_matches += 1

            # Size similarity (only when both are non-zero)
            if abs(r) > eps and abs(a) > eps:
                size_sim = min(abs(r), abs(a)) / max(abs(r), abs(a))
                size_total += size_sim
                size_count += 1

    dir_score = dir_matches / dir_total if dir_total > 0 else 0.0
    size_score = size_total / size_count if size_count > 0 else 0.0

    return 0.70 * dir_score + 0.30 * size_score


def score_performance(ref_summary: dict, agent_summary: dict) -> float:
    """Compare Sharpe, return, drawdown within tolerances.

    Each sub-metric: proximity = 1 - |ref-agent|/max(|ref|,|agent|,eps).
    Equal-weighted average. Range [0.0, 1.0].
    """
    ref_m = ref_summary.get("metrics", ref_summary)
    if not ref_m or not agent_summary:
        return 0.0

    eps = 1e-9
    scores = []

    pairs = [
        ("sharpe_ratio", "sharpe_ratio"),
        ("total_return_pct", "total_return_pct"),
        ("max_drawdown_pct", "max_drawdown_pct"),
    ]

    for ref_key, agent_key in pairs:
        r = float(ref_m.get(ref_key, 0))
        a = float(agent_summary.get(agent_key, 0))
        denom = max(abs(r), abs(a), eps)
        proximity = max(0.0, 1.0 - abs(r - a) / denom)
        scores.append(proximity)

    # Trade count proximity (more lenient)
    ref_tc = int(ref_m.get("total_trades", 0))
    agent_tc = int(agent_summary.get("total_trades", 0))
    if ref_tc > 0:
        tc_prox = max(0.0, 1.0 - abs(ref_tc - agent_tc) / max(ref_tc, agent_tc))
    else:
        tc_prox = 1.0 if agent_tc == 0 else 0.0
    scores.append(tc_prox)

    return sum(scores) / len(scores) if scores else 0.0


def score_trade_similarity(match_result: MatchResult) -> float:
    """Continuous version of existing trade matching.

    Combines count similarity, entry/exit rates, PnL correlation.
    Uses match_trades() with tolerance=2 bars (relaxed). Range [0.0, 1.0].
    """
    if match_result.total_ref_trades == 0 and match_result.total_agent_trades == 0:
        return 1.0
    if match_result.total_ref_trades == 0 or match_result.total_agent_trades == 0:
        return 0.0

    # Count similarity: 1 - |ref-agent|/max(ref,agent)
    count_sim = max(
        0.0,
        1.0
        - abs(match_result.total_ref_trades - match_result.total_agent_trades)
        / max(match_result.total_ref_trades, match_result.total_agent_trades),
    )

    # Entry match rate (already 0-1)
    entry_rate = match_result.entry_match_rate

    # Direction match rate (already 0-1)
    dir_rate = match_result.direction_match_rate

    # Exit match rate (already 0-1)
    exit_rate = match_result.exit_match_rate

    # PnL correlation (map from [-1,1] to [0,1])
    pnl_corr = (match_result.pnl_correlation + 1.0) / 2.0

    # Weighted combination
    score = (
        0.25 * count_sim
        + 0.25 * entry_rate
        + 0.15 * dir_rate
        + 0.15 * exit_rate
        + 0.20 * pnl_corr
    )
    return max(0.0, min(1.0, score))


# ── Composite scoring ──


@dataclass
class BehavioralResult:
    """Result of multi-layer behavioral evaluation."""

    signal_score: float = 0.0
    position_score: float = 0.0
    performance_score: float = 0.0
    trade_score: float = 0.0
    signal_weight: float = 0.40
    position_weight: float = 0.30
    performance_weight: float = 0.20
    trade_weight: float = 0.10
    composite_score: float = 0.0

    # Diagnostic info for debugging
    layers_available: list = field(default_factory=list)


def compute_behavioral_score(
    task_id: str,
    workspace_path: str,
    resolution: str = "daily",
    run_id: str | None = None,
) -> BehavioralResult:
    """Main entry: load all data, score each layer, return composite.

    Redistributes weights when layers are unavailable.
    For multi-run tasks, pass run_id to load the correct reference trades/summary.
    """
    result = BehavioralResult()

    # ── Load reference data ──
    ref_signals = load_reference_signals(task_id)
    ref_positions = load_reference_positions(task_id)
    ref_summary = load_reference_summary(task_id, run_id=run_id)
    ref_trades = load_reference_trades(task_id, run_id=run_id)

    # ── Load agent data ──
    agent_trades = load_agent_trades(workspace_path)
    agent_orders = load_agent_orders(workspace_path)

    # Build agent positions (prefer orders, fall back to trades)
    ref_meta = ref_signals or ref_positions or {}
    import sys as _sys

    _sys.path.insert(0, str(_BENCH_ROOT))
    from config.benchmark_dates import BENCH_END, BENCH_START

    start_date = ref_meta.get("start_date", BENCH_START)
    end_date = ref_meta.get("end_date", BENCH_END)

    if agent_orders:
        agent_positions = reconstruct_positions(agent_orders, start_date, end_date)
    elif agent_trades:
        agent_positions = reconstruct_positions(agent_trades, start_date, end_date)
    else:
        agent_positions = {}

    agent_summary = load_agent_summary(workspace_path)

    # ── Score each layer ──
    available_weights = {}

    # Signal agreement
    has_signals = bool(ref_signals.get("signals"))
    has_agent_pos = bool(agent_positions)
    if has_signals and has_agent_pos:
        # Grace period: skip warmup days to avoid penalizing different warmup handling
        _warmup_map = {"daily": 30, "hour": 720, "4hour": 180, "minute": 43200}
        warmup = _warmup_map.get(resolution, 30)
        result.signal_score = score_signal_agreement(
            ref_signals, agent_positions, warmup_days=warmup
        )
        available_weights["signal"] = result.signal_weight
        result.layers_available.append("signal")

    # Position overlap (ref positions reconstructed from trades at runtime)
    has_ref_pos = bool(ref_positions.get("positions", {}))
    if has_ref_pos and has_agent_pos:
        result.position_score = score_position_overlap(ref_positions, agent_positions)
        available_weights["position"] = result.position_weight
        result.layers_available.append("position")

    # Performance
    has_ref_summary = bool(ref_summary.get("metrics", {}))
    has_agent_summary = bool(agent_summary)
    if has_ref_summary and has_agent_summary:
        result.performance_score = score_performance(ref_summary, agent_summary)
        available_weights["performance"] = result.performance_weight
        result.layers_available.append("performance")

    # Trade similarity (relaxed: 2 bar tolerance)
    if ref_trades and agent_trades:
        match_result = match_trades(
            ref_trades, agent_trades, time_tolerance_bars=2, resolution=resolution
        )
        result.trade_score = score_trade_similarity(match_result)
        available_weights["trade"] = result.trade_weight
        result.layers_available.append("trade")

    # ── Weight redistribution ──
    if not available_weights:
        result.composite_score = 0.0
        return result

    total_available = sum(available_weights.values())
    scale = 1.0 / total_available if total_available > 0 else 0.0

    composite = 0.0
    if "signal" in available_weights:
        composite += result.signal_score * result.signal_weight * scale
    if "position" in available_weights:
        composite += result.position_score * result.position_weight * scale
    if "performance" in available_weights:
        composite += result.performance_score * result.performance_weight * scale
    if "trade" in available_weights:
        composite += result.trade_score * result.trade_weight * scale

    result.composite_score = max(0.0, min(1.0, composite))
    return result


# ════════════════════════════════════════════════════════════════════
# Algorithm Framework Helpers (I07–I10)
# ════════════════════════════════════════════════════════════════════


def compute_trial_efficiency(workspace_path: str, default_max: int = 5) -> float:
    """Compute trial efficiency score from trial data.

    efficiency = (max_trials - trials_used) / (max_trials - 1)
    Clamped to [0.0, 1.0].

    Counts from .backtest_runs.jsonl (source of truth for ALL runs,
    both tool-invoked and shell_exec-invoked). Falls back to
    .trials/manifest.json if JSONL doesn't exist.

    If neither exists, returns 1.0 (agent didn't run backtests).
    """
    # Primary source: JSONL log written by run_backtest.sh EXIT trap
    jsonl_path = os.path.join(workspace_path, ".backtest_runs.jsonl")
    manifest_path = os.path.join(workspace_path, ".trials", "manifest.json")

    trials_used = 0
    max_trials = default_max

    if os.path.exists(jsonl_path):
        try:
            with open(jsonl_path) as f:
                trials_used = sum(1 for line in f if line.strip())
        except IOError:
            trials_used = 0
        # Read max_trials from manifest if available
        if os.path.exists(manifest_path):
            try:
                with open(manifest_path) as f:
                    manifest = json.load(f)
                max_trials = manifest.get("max_trials", default_max)
            except (json.JSONDecodeError, IOError):
                pass
    elif os.path.exists(manifest_path):
        # Fallback: manifest only (legacy path)
        try:
            with open(manifest_path) as f:
                manifest = json.load(f)
        except (json.JSONDecodeError, IOError):
            return 1.0
        max_trials = manifest.get("max_trials", default_max)
        trials_used = manifest.get("trials_used", 0)
    else:
        return 1.0

    if max_trials <= 1:
        return 1.0
    if trials_used <= 0:
        return 1.0

    efficiency = (max_trials - trials_used) / (max_trials - 1)
    return max(0.0, min(1.0, efficiency))


def check_framework_architecture(workspace_path: str) -> dict:
    """Check for SetAlpha/SetPortfolioConstruction/SetExecution patterns in .cs files."""
    checks = {
        "has_set_alpha": False,
        "has_set_portfolio": False,
        "has_set_execution": False,
        "has_add_alpha": False,
        "has_risk_management": False,
    }

    cs_text = ""
    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if fname.endswith(".cs"):
                    try:
                        with open(os.path.join(root, fname)) as f:
                            cs_text += f.read() + "\n"
                    except (IOError, UnicodeDecodeError):
                        continue

    cs_lower = cs_text.lower()
    checks["has_set_alpha"] = bool(re.search(r"setalpha\s*\(", cs_lower))
    checks["has_add_alpha"] = bool(re.search(r"addalpha\s*\(", cs_lower))
    checks["has_set_portfolio"] = bool(
        re.search(r"setportfolioconstruction\s*\(", cs_lower)
    )
    checks["has_set_execution"] = bool(re.search(r"setexecution\s*\(", cs_lower))
    checks["has_risk_management"] = bool(
        re.search(r"(?:set|add)riskmanagement\s*\(", cs_lower)
    )

    return checks


def check_alpha_model_class(workspace_path: str) -> dict:
    """Find classes inheriting AlphaModel with Update() method."""
    checks = {
        "inherits_alpha_model": False,
        "has_update_method": False,
        "alpha_class_count": 0,
    }

    cs_text = ""
    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if fname.endswith(".cs"):
                    try:
                        with open(os.path.join(root, fname)) as f:
                            cs_text += f.read() + "\n"
                    except (IOError, UnicodeDecodeError):
                        continue

    alpha_classes = re.findall(r"class\s+\w+\s*:\s*AlphaModel", cs_text, re.IGNORECASE)
    checks["alpha_class_count"] = len(alpha_classes)
    checks["inherits_alpha_model"] = len(alpha_classes) > 0
    checks["has_update_method"] = bool(
        re.search(r"override\s+.*Update\s*\(", cs_text, re.IGNORECASE)
    )

    return checks


def check_insight_emission(artifact_text: str) -> bool:
    """Check for Insight.Up/Down/Price emission with magnitude/confidence."""
    return bool(
        re.search(r"insight\.(up|down|price)\s*\(", artifact_text, re.IGNORECASE)
    )


def check_risk_model_class(workspace_path: str) -> dict:
    """Find classes inheriting RiskManagementModel with ManageRisk()."""
    checks = {
        "inherits_risk_model": False,
        "has_manage_risk": False,
    }

    cs_text = ""
    if workspace_path and os.path.isdir(workspace_path):
        for root, _, files in os.walk(workspace_path):
            for fname in sorted(files):
                if fname.endswith(".cs"):
                    try:
                        with open(os.path.join(root, fname)) as f:
                            cs_text += f.read() + "\n"
                    except (IOError, UnicodeDecodeError):
                        continue

    checks["inherits_risk_model"] = bool(
        re.search(r"class\s+\w+\s*:\s*RiskManagementModel", cs_text, re.IGNORECASE)
    )
    checks["has_manage_risk"] = bool(
        re.search(r"override\s+.*ManageRisk\s*\(", cs_text, re.IGNORECASE)
    )

    return checks


def load_multi_run_results(workspace_path: str, run_ids: list[str]) -> dict:
    """Load results from workspace/results/{run_id}/ subdirs.

    Returns dict mapping run_id → result dict (or None if not found).
    """
    results = {}
    for run_id in run_ids:
        run_dir = os.path.join(workspace_path, "results", run_id)
        if os.path.isdir(run_dir):
            summary_path = os.path.join(run_dir, "summary.json")
            if os.path.exists(summary_path):
                try:
                    with open(summary_path) as f:
                        results[run_id] = json.load(f)
                except (json.JSONDecodeError, IOError):
                    results[run_id] = None
            else:
                results[run_id] = None
        else:
            results[run_id] = None
    return results
