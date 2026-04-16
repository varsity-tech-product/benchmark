"""Core MCP tool implementations for QuantTutorBench.

These functions implement the core tools available to the Agent Under Test.
They are designed to run inside the Docker sandbox (or locally for development).

Directory paths are read lazily from environment variables so that the
orchestrator can update them per-task (e.g. to point at staged/filtered dirs).
"""

from __future__ import annotations

import glob as glob_module
import json
import os
import re
import shlex
import signal
import subprocess
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from server.core.tools.trial_manager import TrialManager

# ── Lazy directory accessors ────────────────────────────────────
# Read env vars at call time (not import time) so that the orchestrator
# can update QTB_*_DIR between tasks for per-task file filtering.


def _data_dir() -> str:
    return os.environ.get("QTB_DATA_DIR", "/data")


def _docs_dir() -> str:
    return os.environ.get("QTB_DOCS_DIR", "/docs")


def _workspace_dir() -> str:
    return os.environ.get("QTB_WORKSPACE_DIR", "/workspace")


def _student_code_dir() -> str:
    return os.environ.get("QTB_STUDENT_CODE_DIR", "/student_code")


def _session_context() -> dict:
    """Return truthful per-session runtime context exposed by the server."""
    raw = os.environ.get("QTB_SESSION_CONTEXT_JSON", "").strip()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _infer_csharp_entrypoint(source_path: str) -> dict[str, str]:
    """Infer namespace/class entrypoint from a C# QCAlgorithm source file."""
    try:
        with open(source_path, encoding="utf-8") as f:
            source = f.read()
    except OSError:
        return {}

    namespace_match = re.search(
        r"^\s*namespace\s+([A-Za-z_][A-Za-z0-9_.]*)",
        source,
        flags=re.MULTILINE,
    )
    class_match = re.search(
        r"\bclass\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*[^{\n]*\bQCAlgorithm\b",
        source,
    )
    if not class_match:
        return {}

    class_name = class_match.group(1)
    namespace = namespace_match.group(1) if namespace_match else ""
    full_type_name = f"{namespace}.{class_name}" if namespace else class_name
    return {
        "class_name": class_name,
        "full_type_name": full_type_name,
    }


def _extract_compile_errors(output: str, limit: int = 5) -> list[str]:
    """Pull out the most actionable compiler error lines from tool output."""
    errors: list[str] = []
    seen: set[str] = set()
    for line in output.splitlines():
        stripped = line.strip()
        lowered = stripped.lower()
        if "error cs" not in lowered and ": error " not in lowered:
            continue
        if stripped in seen:
            continue
        seen.add(stripped)
        errors.append(stripped)
        if len(errors) >= limit:
            break
    return errors


def _compute_performance_metrics(returns, annual_factor=252):
    """Compute standard performance metrics from a daily returns series.

    Shared helper used by both run_backtest and analyze_backtest_results
    to avoid code duplication.

    Args:
        returns: pandas Series of daily returns (not cumulative).
        annual_factor: trading days per year for annualization.

    Returns:
        dict with sharpe_ratio, annual_return, total_return, max_drawdown,
        win_rate, volatility, sortino_ratio, calmar_ratio, total_trading_days.
    """
    import numpy as np

    returns = returns.dropna()
    if len(returns) < 2:
        return {
            "sharpe_ratio": 0.0,
            "annual_return": 0.0,
            "total_return": 0.0,
            "max_drawdown": 0.0,
            "win_rate": 0.0,
            "volatility": 0.0,
            "sortino_ratio": 0.0,
            "calmar_ratio": 0.0,
            "total_trading_days": int(len(returns)),
        }

    mean_ret = returns.mean()
    std_ret = returns.std()

    sharpe = (mean_ret / std_ret * np.sqrt(annual_factor)) if std_ret > 0 else 0.0
    annual_return = (1 + mean_ret) ** annual_factor - 1
    cumulative = (1 + returns).cumprod()
    running_max = cumulative.cummax()
    max_drawdown = ((cumulative / running_max) - 1).min()
    win_rate = (returns > 0).mean()
    volatility = std_ret * np.sqrt(annual_factor)

    downside_returns = returns[returns < 0]
    downside_std = downside_returns.std() if len(downside_returns) > 0 else 0.0
    sortino = (
        (mean_ret / downside_std * np.sqrt(annual_factor)) if downside_std > 0 else 0.0
    )
    calmar = (annual_return / abs(max_drawdown)) if max_drawdown != 0 else 0.0
    total_return = float(cumulative.iloc[-1] - 1) if len(cumulative) > 0 else 0.0

    return {
        "sharpe_ratio": round(float(sharpe), 4),
        "annual_return": round(float(annual_return), 4),
        "total_return": round(float(total_return), 4),
        "max_drawdown": round(float(max_drawdown), 4),
        "win_rate": round(float(win_rate), 4),
        "volatility": round(float(volatility), 4),
        "sortino_ratio": round(float(sortino), 4),
        "calmar_ratio": round(float(calmar), 4),
        "total_trading_days": int(len(returns)),
    }


def _resolve_column_name(df, candidates: list[str]) -> Optional[str]:
    """Resolve the first matching column name, case-insensitively."""
    lookup = {str(col).lower(): col for col in df.columns}
    for candidate in candidates:
        match = lookup.get(candidate.lower())
        if match is not None:
            return match
    return None


def _safe_round(value, digits: int = 4) -> Optional[float]:
    """Round numeric values while preserving None for invalid inputs."""
    try:
        import pandas as pd

        if pd.isna(value):
            return None
    except Exception:
        pass
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _infer_annual_factor(df) -> int:
    """Infer a rough annualization factor from timestamp-like columns."""
    import pandas as pd

    time_col = _resolve_column_name(df, ["timestamp", "date", "datetime", "time"])
    if time_col is None:
        return 252

    raw = df[time_col]
    if pd.api.types.is_numeric_dtype(raw):
        median_abs = raw.dropna().abs().median()
        unit = "ms" if median_abs and median_abs > 10_000_000_000 else "s"
        parsed = pd.to_datetime(raw, errors="coerce", utc=True, unit=unit)
    else:
        parsed = pd.to_datetime(raw, errors="coerce", utc=True)
    deltas = parsed.sort_values().diff().dropna()
    if deltas.empty:
        return 252

    median_seconds = deltas.median().total_seconds()
    if median_seconds <= 10 * 60:
        return 365 * 24 * 12
    if median_seconds <= 90 * 60:
        return 365 * 24
    if median_seconds <= 36 * 3600:
        return 365
    return 252


_MAX_SHELL_TIMEOUT = 600  # Hard cap — allows .NET cold compilation (~5 min) in Docker


def shell_exec(command: str, timeout: int = 30) -> str:
    """Execute a shell command in the sandbox."""
    timeout = min(max(timeout, 1), _MAX_SHELL_TIMEOUT)
    try:
        # Use start_new_session so we can kill the entire process group on
        # timeout, including grandchild processes (e.g. dotnet).  Without
        # this, subprocess.run(shell=True) only kills the shell and the
        # grandchild keeps stdout pipes open, blocking the read forever.
        proc = subprocess.Popen(
            command,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=_workspace_dir(),
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=timeout)
        output = ""
        if stdout:
            output += stdout
        if stderr:
            output += f"\n[stderr]: {stderr}"
        if proc.returncode != 0:
            output += f"\n[exit code]: {proc.returncode}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        # Kill the entire process group to ensure grandchildren are terminated.
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            proc.kill()
        try:
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            proc.kill()
        return f"Error: Command timed out after {timeout}s"


def file_write(path: str, content: str) -> str:
    """Write content to a file in the workspace."""
    workspace = _workspace_dir()
    # Strip redundant workspace/ prefix (agents often write "workspace/foo.py"
    # which becomes /workspace/workspace/foo.py without this fix)
    clean = path
    while clean.startswith("workspace/") or clean.startswith("Workspace/"):
        clean = clean[len("workspace/") :]
    full_path = os.path.join(workspace, clean) if not clean.startswith("/") else clean
    full_path = os.path.realpath(full_path)
    if not full_path.startswith(os.path.realpath(workspace)):
        return f"Error: Can only write to {workspace}"
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w") as f:
        f.write(content)
    return f"Written {len(content)} bytes to {full_path}"


def _resolve_path(path: str) -> Optional[str]:
    """Resolve a relative path across all search directories.

    Handles the common case where the caller includes a known directory
    prefix (e.g. ``student_code/foo.py``) — we strip the prefix so that
    the search against the matching base directory doesn't double up.

    Security: absolute paths and path traversal (../) are restricted to
    the allowed base directories to prevent access to reference answers
    or other host files in local (non-Docker) mode.
    """
    bases = [_workspace_dir(), _data_dir(), _docs_dir(), _student_code_dir()]
    # Known directory name prefixes that callers might include
    _KNOWN_PREFIXES = ("workspace/", "data/", "docs/", "student_code/")

    def _is_within_bases(resolved: str) -> bool:
        """Check that resolved path is under one of the allowed base dirs."""
        real = os.path.realpath(resolved)
        return any(
            real.startswith(os.path.realpath(b) + os.sep) or real == os.path.realpath(b)
            for b in bases
        )

    # 1. Direct search
    for base in bases:
        if path.startswith("/"):
            full = path
        else:
            full = os.path.join(base, path)
        if (os.path.isfile(full) or os.path.isdir(full)) and _is_within_bases(full):
            return full

    # 2. Strip known directory prefix and retry
    for prefix in _KNOWN_PREFIXES:
        if path.startswith(prefix):
            stripped = path[len(prefix) :]
            for base in bases:
                full = os.path.join(base, stripped)
                if (os.path.isfile(full) or os.path.isdir(full)) and _is_within_bases(
                    full
                ):
                    return full
            break  # Only one prefix can match

    return None


def file_read(path: str, offset: int = 0, max_lines: int = 0) -> str:
    """Read a file from workspace, data, docs, or student_code.

    For large CSV files (>50 rows), returns a smart preview (header +
    first 5 + last 5 rows) by default.  Use ``offset`` and ``max_lines``
    to read specific sections.

    Args:
        path: File path (resolved across workspace/data/docs/student_code).
        offset: Start reading from this line number (0-based). Default 0.
        max_lines: Maximum lines to return. 0 means auto (preview for
                   large CSV, full content for everything else).
    """
    resolved = _resolve_path(path)
    if not resolved or not os.path.isfile(resolved):
        return f"Error: File not found: {path}"

    # Binary file detection
    _BINARY_EXTENSIONS = frozenset(
        {
            ".png",
            ".jpg",
            ".jpeg",
            ".gif",
            ".bmp",
            ".ico",
            ".svg",
            ".pdf",
            ".zip",
            ".gz",
            ".tar",
            ".7z",
            ".pkl",
            ".pickle",
            ".npy",
            ".npz",
            ".pyc",
            ".so",
            ".dylib",
            ".dll",
            ".exe",
        }
    )
    _, ext = os.path.splitext(resolved)
    if ext.lower() in _BINARY_EXTENSIONS:
        size = os.path.getsize(resolved)
        return (
            f"[{path}] Binary file ({ext}, {size:,} bytes). "
            f"Cannot display contents. Use shell_exec to process "
            f"this file with appropriate tools."
        )

    try:
        with open(resolved, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except UnicodeDecodeError:
        size = os.path.getsize(resolved)
        return (
            f"[{path}] Unable to read as text ({size:,} bytes). "
            f"The file may be binary or use a non-UTF-8 encoding."
        )

    total = len(lines)

    # Explicit pagination: offset and/or max_lines specified
    if offset > 0 or max_lines > 0:
        start = max(0, offset)
        subset = lines[start:]
        if max_lines > 0:
            subset = subset[:max_lines]
        end_line = start + len(subset)
        return f"[{path} | lines {start + 1}-{end_line} of {total}]\n" + "".join(subset)

    # Smart preview for large CSV files
    if path.endswith(".csv") and total > 50:
        header = lines[0]
        head = lines[1:6]
        tail = lines[-5:]
        return (
            f"[{path} | {total} rows | showing header + first 5 + last 5]\n"
            f"{header}{''.join(head)}\n"
            f"... ({total - 11} rows omitted) ...\n\n"
            f"{''.join(tail)}\n"
            f"Use offset and max_lines parameters to read specific sections."
        )

    # Small files / non-CSV: return full content
    return "".join(lines)


def file_list(directory: str = ".") -> str:
    """List files in a directory."""
    resolved = _resolve_path(directory)
    if resolved and os.path.isdir(resolved):
        entries = sorted(os.listdir(resolved))
        return "\n".join(entries) if entries else "(empty directory)"
    return f"Error: Directory not found: {directory}"


def fetch_market_data(symbol: str, start: str = "", end: str = "") -> str:
    """Return OHLCV data from frozen CSV for a given symbol and date range."""
    import pandas as pd

    pattern = os.path.join(_data_dir(), f"{symbol}*.csv")
    matches = glob_module.glob(pattern)
    if not matches:
        return f"Error: No data file found for symbol '{symbol}'"
    df = pd.read_csv(matches[0], parse_dates=["Date"])
    if start:
        df = df[df["Date"] >= start]
    if end:
        df = df[df["Date"] <= end]
    if df.empty:
        return f"No data for {symbol} in range {start} to {end}"

    # Save to workspace so subsequent tools can reference the file
    out_name = f"{symbol}_data.csv"
    out_path = os.path.join(_workspace_dir(), out_name)
    df.to_csv(out_path, index=False)

    # Return file path + compact summary instead of full CSV
    date_col = "Date"
    summary_parts = [
        f"Saved {len(df)} rows to {out_name}",
        f"Date range: {df[date_col].iloc[0]} to {df[date_col].iloc[-1]}",
        f"Columns: {', '.join(df.columns)}",
        "",
        f"First 5 rows:\n{df.head().to_csv(index=False)}",
        f"Last 5 rows:\n{df.tail().to_csv(index=False)}",
    ]
    return "\n".join(summary_parts)


def compute_indicator(
    data_path: str, indicator: str, indicator_params: Optional[dict] = None
) -> str:
    """Compute a technical indicator on a dataset."""
    import pandas as pd

    indicator_params = indicator_params or {}
    full_path = _resolve_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"
    with open(full_path) as _hdr:
        _has_date_col = "Date" in _hdr.readline()
    df = pd.read_csv(
        full_path,
        parse_dates=["Date"] if _has_date_col else None,
    )

    indicator = indicator.upper()
    if indicator == "SMA":
        window = indicator_params.get("window", 20)
        df[f"SMA_{window}"] = df["Close"].rolling(window).mean()
    elif indicator == "EMA":
        span = indicator_params.get("span", 20)
        df[f"EMA_{span}"] = df["Close"].ewm(span=span).mean()
    elif indicator == "RSI":
        window = indicator_params.get("window", 14)
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))
    elif indicator == "BOLLINGER":
        window = indicator_params.get("window", 20)
        std_dev = indicator_params.get("std_dev", 2)
        sma = df["Close"].rolling(window).mean()
        std = df["Close"].rolling(window).std()
        df["BB_Upper"] = sma + std_dev * std
        df["BB_Middle"] = sma
        df["BB_Lower"] = sma - std_dev * std
    elif indicator == "MACD":
        fast = indicator_params.get("fast", 12)
        slow = indicator_params.get("slow", 26)
        signal = indicator_params.get("signal", 9)
        ema_fast = df["Close"].ewm(span=fast).mean()
        ema_slow = df["Close"].ewm(span=slow).mean()
        df["MACD"] = ema_fast - ema_slow
        df["MACD_Signal"] = df["MACD"].ewm(span=signal).mean()
        df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]
    else:
        return f"Error: Unknown indicator '{indicator}'. Supported: SMA, EMA, RSI, BOLLINGER, MACD"

    # Save enriched data to workspace for subsequent tools
    base_name = os.path.splitext(os.path.basename(data_path))[0]
    out_name = f"{base_name}_{indicator.lower()}.csv"
    out_path = os.path.join(_workspace_dir(), out_name)
    df.to_csv(out_path, index=False)

    return (
        f"Computed {indicator} and saved to {out_name}\n\n"
        f"Last 10 rows:\n{df.tail(10).to_csv(index=False)}"
    )


def run_backtest(
    data_path: str,
    strategy: str,
    strategy_params: Optional[dict] = None,
    start: str = "",
    end: str = "",
) -> str:
    """Run a complete backtest for a built-in strategy type.

    Self-contained: uses pandas/numpy directly. Does NOT call shell_exec
    or any other MCP tool. The agent does NOT need to write a script —
    just provide the strategy name and parameters.

    Supported strategies:
      ma_crossover:       Dual SMA crossover (golden cross / death cross).
                          Params: fast_window (default 20), slow_window (default 50).
      rsi_threshold:      RSI overbought / oversold signals.
                          Params: window (14), overbought (70), oversold (30).
      bollinger_breakout: Bollinger Band breakout or mean-reversion.
                          Params: window (20), std_dev (2),
                                  mode ("breakout" or "mean_reversion").

    Returns performance metrics (Sharpe, return, drawdown, etc.), a trade
    summary, and saves equity-curve CSV + metrics JSON to the workspace.
    """
    import pandas as pd

    strategy_params = strategy_params or {}
    full_path = _resolve_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"

    # Read header to check for Date column
    with open(full_path) as fh:
        header = fh.readline()
    has_date = "Date" in header

    df = pd.read_csv(full_path, parse_dates=["Date"] if has_date else None)

    if "Close" not in df.columns:
        return (
            f"Error: Data must have a 'Close' column. " f"Available: {list(df.columns)}"
        )

    # Optional date-range filter
    if has_date and "Date" in df.columns:
        if start:
            df = df[df["Date"] >= start]
        if end:
            df = df[df["Date"] <= end]

    if len(df) < 20:
        return f"Error: Not enough data ({len(df)} rows) for backtesting"

    df = df.reset_index(drop=True)
    df["daily_return"] = df["Close"].pct_change()

    strategy_name = strategy.lower().replace("-", "_").replace(" ", "_")

    # ── Strategy: MA Crossover ──────────────────────────────────
    if strategy_name == "ma_crossover":
        fast_window = strategy_params.get("fast_window", 20)
        slow_window = strategy_params.get("slow_window", 50)
        if fast_window >= slow_window:
            return (
                f"Error: fast_window ({fast_window}) must be less "
                f"than slow_window ({slow_window})"
            )
        df["SMA_fast"] = df["Close"].rolling(fast_window).mean()
        df["SMA_slow"] = df["Close"].rolling(slow_window).mean()
        # Long when fast SMA > slow SMA (golden cross)
        df["signal"] = (df["SMA_fast"] > df["SMA_slow"]).astype(int)
        strategy_desc = f"MA Crossover (fast={fast_window}, slow={slow_window})"

    # ── Strategy: RSI Threshold ─────────────────────────────────
    elif strategy_name == "rsi_threshold":
        window = strategy_params.get("window", 14)
        overbought = strategy_params.get("overbought", 70)
        oversold = strategy_params.get("oversold", 30)

        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))

        # State machine: enter long when RSI < oversold,
        # exit when RSI > overbought, hold otherwise.
        position = 0
        signals = []
        for rsi_val in df["RSI"]:
            if pd.isna(rsi_val):
                signals.append(0)
            elif rsi_val < oversold:
                position = 1
                signals.append(position)
            elif rsi_val > overbought:
                position = 0
                signals.append(position)
            else:
                signals.append(position)
        df["signal"] = signals
        strategy_desc = (
            f"RSI Threshold (window={window}, OB={overbought}, OS={oversold})"
        )

    # ── Strategy: Bollinger Breakout / Mean Reversion ───────────
    elif strategy_name == "bollinger_breakout":
        window = strategy_params.get("window", 20)
        std_dev = strategy_params.get("std_dev", 2)
        mode = strategy_params.get("mode", "breakout")

        sma = df["Close"].rolling(window).mean()
        std = df["Close"].rolling(window).std()
        df["BB_Upper"] = sma + std_dev * std
        df["BB_Lower"] = sma - std_dev * std

        position = 0
        signals = []
        for _, row in df.iterrows():
            close, upper, lower = row["Close"], row["BB_Upper"], row["BB_Lower"]
            if pd.isna(upper) or pd.isna(lower):
                signals.append(0)
                continue
            if mode == "breakout":
                if close > upper:
                    position = 1
                elif close < lower:
                    position = 0
            else:  # mean_reversion
                if close < lower:
                    position = 1
                elif close > upper:
                    position = 0
            signals.append(position)
        df["signal"] = signals
        strategy_desc = f"Bollinger {mode.title()} (window={window}, std={std_dev})"

    else:
        supported = "ma_crossover, rsi_threshold, bollinger_breakout"
        return f"Error: Unknown strategy '{strategy}'. Supported: {supported}"

    # ── Compute strategy returns (execute signal on next bar) ───
    df["position"] = df["signal"].shift(1).fillna(0)
    df["strategy_return"] = df["position"] * df["daily_return"]

    # Drop NaN warm-up rows
    valid = df.dropna(subset=["strategy_return"])
    if len(valid) < 2:
        return "Error: Strategy produced no valid trading days"

    # ── Performance metrics via shared helper ───────────────────
    metrics = _compute_performance_metrics(valid["strategy_return"])
    metrics["strategy"] = strategy_desc

    # Trade summary
    position_changes = valid["position"].diff().fillna(0)
    entries = int((position_changes == 1).sum())
    exits = int((position_changes == -1).sum())
    metrics["total_trades"] = entries + exits
    metrics["entries"] = entries
    metrics["exits"] = exits

    # ── Save results to workspace ───────────────────────────────
    workspace = _workspace_dir()

    cols = ["Close", "signal", "position", "strategy_return"]
    if "Date" in valid.columns:
        cols = ["Date"] + cols
    results_df = valid[cols].copy()
    results_df["equity"] = (1 + valid["strategy_return"]).cumprod()
    results_name = f"backtest_{strategy_name}_results.csv"
    metrics_name = f"backtest_{strategy_name}_metrics.json"
    results_path = os.path.join(workspace, results_name)
    results_df.to_csv(results_path, index=False)

    metrics_path = os.path.join(workspace, metrics_name)
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    return (
        f"Backtest: {strategy_desc}\n"
        f"Data: {data_path} ({len(valid)} trading days)\n\n"
        f"Performance Metrics:\n"
        f"  Sharpe Ratio:   {metrics['sharpe_ratio']}\n"
        f"  Annual Return:  {metrics['annual_return']:.2%}\n"
        f"  Total Return:   {metrics['total_return']:.2%}\n"
        f"  Max Drawdown:   {metrics['max_drawdown']:.2%}\n"
        f"  Win Rate:       {metrics['win_rate']:.2%}\n"
        f"  Volatility:     {metrics['volatility']:.2%}\n"
        f"  Sortino Ratio:  {metrics['sortino_ratio']}\n"
        f"  Calmar Ratio:   {metrics['calmar_ratio']}\n\n"
        f"Trade Summary:\n"
        f"  Total Trades:   {metrics['total_trades']}\n"
        f"  Entries:        {metrics['entries']}\n"
        f"  Exits:          {metrics['exits']}\n\n"
        f"Files saved:\n"
        f"  {results_name} (equity curve with signals)\n"
        f"  {metrics_name} (all metrics)"
    )


def compute_statistics(
    data_path: str, method: str, method_params: Optional[dict] = None
) -> str:
    """Run statistical tests or descriptive analysis on data."""
    import numpy as np
    import pandas as pd

    method_params = method_params or {}
    full_path = _resolve_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"
    df = pd.read_csv(full_path)

    method = method.upper()
    if method == "ADF":
        from statsmodels.tsa.stattools import adfuller

        col = method_params.get("column", "Close")
        result = adfuller(df[col].dropna())
        return json.dumps(
            {
                "test_statistic": round(result[0], 4),
                "p_value": round(result[1], 4),
                "critical_values": {k: round(v, 4) for k, v in result[4].items()},
                "stationary": bool(result[1] < 0.05),
            },
            indent=2,
        )
    elif method == "CORRELATION":
        cols = method_params.get(
            "columns", [c for c in df.select_dtypes(include=[np.number]).columns]
        )
        corr_method = method_params.get("method", "pearson")
        if corr_method not in ("pearson", "spearman", "kendall"):
            return (
                f"Error: Unknown correlation method '{corr_method}'. "
                f"Supported: pearson, spearman, kendall"
            )
        corr = df[cols].corr(method=corr_method)
        return corr.to_csv()
    elif method == "COINTEGRATION":
        import statsmodels.api as sm
        from statsmodels.tsa.stattools import coint

        if len(df.columns) < 3 and (
            "column1" not in method_params or "column2" not in method_params
        ):
            return (
                "Error: COINTEGRATION requires at least 3 columns or explicit "
                "'column1' and 'column2' in method_params. "
                f"Available columns: {list(df.columns)}"
            )
        col1 = method_params.get("column1", df.columns[1])
        col2 = method_params.get(
            "column2", df.columns[2] if len(df.columns) > 2 else df.columns[1]
        )
        trend = method_params.get("trend", "c")  # 'c', 'ct', 'ctt', 'n'
        maxlag = method_params.get("maxlag", None)  # None → auto
        autolag = method_params.get("autolag", "aic")  # 'aic','bic','t-stat',None
        save_spread = method_params.get("save_spread", True)

        y = df[col1].dropna().astype(float)
        x = df[col2].dropna().astype(float)
        common_idx = y.index.intersection(x.index)
        y, x = y.loc[common_idx], x.loc[common_idx]

        score, pvalue, _ = coint(y, x, trend=trend, maxlag=maxlag, autolag=autolag)

        # OLS hedge ratio: y = intercept + beta * x + epsilon
        X_ols = sm.add_constant(x.values)
        model = sm.OLS(y.values, X_ols).fit()
        hedge_ratio = float(model.params[1])
        intercept = float(model.params[0])

        # Spread series
        spread = y.values - hedge_ratio * x.values - intercept
        spread_series = pd.Series(spread, index=common_idx)

        # Half-life via mean-reversion speed: Δs_t = λ * s_{t-1} + ε
        spread_lag = spread_series.shift(1).dropna()
        spread_diff = spread_series.diff().dropna()
        common_hl = spread_lag.index.intersection(spread_diff.index)
        if len(common_hl) > 5:
            X_hl = sm.add_constant(spread_lag.loc[common_hl].values)
            model_hl = sm.OLS(spread_diff.loc[common_hl].values, X_hl).fit()
            lam = model_hl.params[1]
            half_life = -np.log(2) / lam if lam < 0 else float("inf")
        else:
            half_life = float("inf")

        # Z-score of spread
        spread_mean = float(spread_series.mean())
        spread_std = float(spread_series.std())
        current_zscore = (
            float((spread_series.iloc[-1] - spread_mean) / spread_std)
            if spread_std > 0
            else 0.0
        )

        result = {
            "test_statistic": round(score, 4),
            "p_value": round(pvalue, 4),
            "cointegrated": bool(pvalue < 0.05),
            "hedge_ratio": round(hedge_ratio, 6),
            "intercept": round(intercept, 6),
            "half_life_periods": (
                round(half_life, 2) if np.isfinite(half_life) else None
            ),
            "spread_mean": round(spread_mean, 6),
            "spread_std": round(spread_std, 6),
            "current_spread_zscore": round(current_zscore, 4),
            "r_squared": round(float(model.rsquared), 4),
        }

        if save_spread:
            spread_zscore = (
                (spread - spread_mean) / spread_std if spread_std > 0 else 0.0
            )
            spread_df = pd.DataFrame(
                {
                    col1: y.values,
                    col2: x.values,
                    "spread": spread,
                    "spread_zscore": spread_zscore,
                }
            )
            spread_file = f"spread_{col1}_{col2}.csv"
            spread_df.to_csv(os.path.join(_workspace_dir(), spread_file), index=False)
            result["spread_saved_to"] = spread_file

        return json.dumps(result, indent=2)
    elif method == "DESCRIPTIVE":
        col = method_params.get("column", None)
        if col:
            series = df[col].dropna()
            result = {
                "count": int(series.count()),
                "mean": round(float(series.mean()), 6),
                "std": round(float(series.std()), 6),
                "min": round(float(series.min()), 6),
                "25%": round(float(series.quantile(0.25)), 6),
                "50%": round(float(series.quantile(0.50)), 6),
                "75%": round(float(series.quantile(0.75)), 6),
                "max": round(float(series.max()), 6),
                "skew": round(float(series.skew()), 6),
                "kurtosis": round(float(series.kurtosis()), 6),
            }
        else:
            result = json.loads(df.describe(include="all").to_json())
        return json.dumps(result, indent=2)
    elif method == "MISSING":
        total = len(df)
        missing = {}
        for c in df.columns:
            n_miss = int(df[c].isna().sum())
            missing[c] = {
                "missing_count": n_miss,
                "missing_pct": round(n_miss / total * 100, 2) if total > 0 else 0.0,
            }
        return json.dumps({"total_rows": total, "columns": missing}, indent=2)
    elif method == "LEAD_LAG":
        if len(df.columns) < 3 and (
            "column1" not in method_params or "column2" not in method_params
        ):
            return (
                "Error: LEAD_LAG requires at least 3 columns or explicit "
                "'column1' and 'column2' in method_params. "
                f"Available columns: {list(df.columns)}"
            )
        col1 = method_params.get("column1", df.columns[1])
        col2 = method_params.get(
            "column2", df.columns[2] if len(df.columns) > 2 else df.columns[1]
        )
        max_lag = int(method_params.get("max_lag", 10))
        analysis_type = method_params.get(
            "type", "both"
        )  # cross_correlation, granger, both

        s1 = df[col1].dropna().astype(float)
        s2 = df[col2].dropna().astype(float)
        common_idx = s1.index.intersection(s2.index)
        s1, s2 = s1.loc[common_idx], s2.loc[common_idx]

        result = {"column1": col1, "column2": col2}

        # Cross-correlation at multiple lags
        if analysis_type in ("cross_correlation", "both"):
            cross_corr = {}
            for lag in range(-max_lag, max_lag + 1):
                if lag > 0:
                    corr = float(
                        s1.iloc[lag:]
                        .reset_index(drop=True)
                        .corr(s2.iloc[:-lag].reset_index(drop=True))
                    )
                elif lag < 0:
                    corr = float(
                        s1.iloc[:lag]
                        .reset_index(drop=True)
                        .corr(s2.iloc[-lag:].reset_index(drop=True))
                    )
                else:
                    corr = float(s1.corr(s2))
                cross_corr[str(lag)] = round(corr, 4) if np.isfinite(corr) else 0.0

            best_lag = max(cross_corr, key=lambda k: abs(cross_corr[k]))
            best_val = cross_corr[best_lag]
            bl = int(best_lag)
            if bl > 0:
                interp = f"{col1} leads {col2} by {bl} periods"
            elif bl < 0:
                interp = f"{col2} leads {col1} by {abs(bl)} periods"
            else:
                interp = "contemporaneous"

            result["cross_correlation"] = {
                "correlations": cross_corr,
                "best_lag": bl,
                "best_correlation": best_val,
                "interpretation": interp,
            }

        # Granger causality test
        if analysis_type in ("granger", "both"):
            from statsmodels.tsa.stattools import grangercausalitytests

            granger_maxlag = int(method_params.get("granger_maxlag", min(max_lag, 5)))
            test_data = pd.DataFrame({col1: s1, col2: s2}).dropna()

            gc_results = {}
            for cause, effect in [(col2, col1), (col1, col2)]:
                direction = f"{cause}→{effect}"
                try:
                    gc_test = grangercausalitytests(
                        test_data[[effect, cause]],
                        maxlag=granger_maxlag,
                        verbose=False,
                    )
                    lag_results = {}
                    for lag_i in range(1, granger_maxlag + 1):
                        f_stat = gc_test[lag_i][0]["ssr_ftest"][0]
                        p_val = gc_test[lag_i][0]["ssr_ftest"][1]
                        lag_results[str(lag_i)] = {
                            "f_statistic": round(f_stat, 4),
                            "p_value": round(p_val, 4),
                            "significant": bool(p_val < 0.05),
                        }
                    gc_results[direction] = lag_results
                except Exception as e:
                    gc_results[direction] = {"error": str(e)}

            result["granger_causality"] = gc_results

        return json.dumps(result, indent=2)

    elif method == "ROLLING":
        col1 = method_params.get("column1", method_params.get("column", df.columns[1]))
        col2 = method_params.get("column2", None)
        window = int(method_params.get("window", 60))
        step = int(method_params.get("step", 1))
        metric = method_params.get("metric", "correlation")
        workspace = _workspace_dir()

        if metric == "correlation":
            if col2 is None:
                return "Error: ROLLING correlation requires column2 parameter"
            s1 = df[col1].astype(float)
            s2 = df[col2].astype(float)
            rolling_corr = s1.rolling(window).corr(s2)
            if step > 1:
                rolling_corr = rolling_corr.iloc[::step]

            out_name = f"rolling_corr_{col1}_{col2}_w{window}.csv"
            result_df = pd.DataFrame(
                {col1: s1, col2: s2, "rolling_correlation": rolling_corr}
            )
            if step > 1:
                result_df = result_df.iloc[::step]
            result_df.dropna(subset=["rolling_correlation"]).to_csv(
                os.path.join(workspace, out_name), index=False
            )

            rc = rolling_corr.dropna()
            result = {
                "metric": "rolling_correlation",
                "window": window,
                "step": step,
                "observations": int(rc.count()),
                "mean": round(float(rc.mean()), 4),
                "std": round(float(rc.std()), 4),
                "min": round(float(rc.min()), 4),
                "max": round(float(rc.max()), 4),
                "current": round(float(rc.iloc[-1]), 4),
                "saved_to": out_name,
            }

        elif metric == "cointegration":
            if col2 is None:
                return "Error: ROLLING cointegration requires column2 parameter"
            import statsmodels.api as sm
            from statsmodels.tsa.stattools import coint

            s1 = df[col1].dropna().astype(float)
            s2 = df[col2].dropna().astype(float)
            common_idx = s1.index.intersection(s2.index)
            s1, s2 = s1.loc[common_idx], s2.loc[common_idx]
            maxlag = method_params.get("maxlag", None)
            autolag = method_params.get("autolag", "aic")

            results_list = []
            indices = list(range(window, len(s1), step))
            for i in indices:
                y_w = s1.iloc[i - window : i]
                x_w = s2.iloc[i - window : i]
                try:
                    _, pv, _ = coint(y_w, x_w, maxlag=maxlag, autolag=autolag)
                    X_ols = sm.add_constant(x_w.values)
                    mdl = sm.OLS(y_w.values, X_ols).fit()
                    hr = float(mdl.params[1])
                    results_list.append(
                        {
                            "window_end": int(common_idx[i]),
                            "p_value": round(pv, 4),
                            "cointegrated": bool(pv < 0.05),
                            "hedge_ratio": round(hr, 6),
                        }
                    )
                except Exception:
                    results_list.append(
                        {
                            "window_end": int(common_idx[i]),
                            "p_value": None,
                            "cointegrated": False,
                            "hedge_ratio": None,
                        }
                    )

            out_name = f"rolling_coint_{col1}_{col2}_w{window}.csv"
            pd.DataFrame(results_list).to_csv(
                os.path.join(workspace, out_name), index=False
            )

            valid = [r for r in results_list if r["p_value"] is not None]
            coint_pct = (
                sum(1 for r in valid if r["cointegrated"]) / len(valid) * 100
                if valid
                else 0
            )
            result = {
                "metric": "rolling_cointegration",
                "window": window,
                "step": step,
                "total_windows": len(results_list),
                "valid_windows": len(valid),
                "cointegrated_pct": round(coint_pct, 1),
                "mean_p_value": (
                    round(np.mean([r["p_value"] for r in valid]), 4) if valid else None
                ),
                "saved_to": out_name,
            }

        elif metric == "beta":
            if col2 is None:
                return "Error: ROLLING beta requires column2 parameter"
            import statsmodels.api as sm

            s1 = df[col1].dropna().astype(float)
            s2 = df[col2].dropna().astype(float)
            common_idx = s1.index.intersection(s2.index)
            s1, s2 = s1.loc[common_idx], s2.loc[common_idx]

            results_list = []
            indices = list(range(window, len(s1), step))
            for i in indices:
                y_w = s1.iloc[i - window : i]
                x_w = s2.iloc[i - window : i]
                try:
                    X_ols = sm.add_constant(x_w.values)
                    mdl = sm.OLS(y_w.values, X_ols).fit()
                    results_list.append(
                        {
                            "window_end": int(common_idx[i]),
                            "beta": round(float(mdl.params[1]), 6),
                            "alpha": round(float(mdl.params[0]), 6),
                            "r_squared": round(float(mdl.rsquared), 4),
                        }
                    )
                except Exception:
                    results_list.append(
                        {
                            "window_end": int(common_idx[i]),
                            "beta": None,
                            "alpha": None,
                            "r_squared": None,
                        }
                    )

            out_name = f"rolling_beta_{col1}_{col2}_w{window}.csv"
            pd.DataFrame(results_list).to_csv(
                os.path.join(workspace, out_name), index=False
            )

            valid_betas = [r["beta"] for r in results_list if r["beta"] is not None]
            result = {
                "metric": "rolling_beta",
                "window": window,
                "step": step,
                "total_windows": len(results_list),
                "mean_beta": (
                    round(float(np.mean(valid_betas)), 6) if valid_betas else None
                ),
                "std_beta": (
                    round(float(np.std(valid_betas)), 6) if valid_betas else None
                ),
                "current_beta": (round(valid_betas[-1], 6) if valid_betas else None),
                "saved_to": out_name,
            }

        elif metric == "adf":
            from statsmodels.tsa.stattools import adfuller

            col = method_params.get("column", col1)
            maxlag_adf = method_params.get("maxlag", None)
            autolag_adf = method_params.get("autolag", "AIC")

            s = df[col].dropna().astype(float)
            results_list = []
            indices = list(range(window, len(s), step))
            for i in indices:
                seg = s.iloc[i - window : i]
                try:
                    adf_r = adfuller(seg, maxlag=maxlag_adf, autolag=autolag_adf)
                    results_list.append(
                        {
                            "window_end": int(s.index[i]),
                            "test_statistic": round(adf_r[0], 4),
                            "p_value": round(adf_r[1], 4),
                            "stationary": bool(adf_r[1] < 0.05),
                        }
                    )
                except Exception:
                    results_list.append(
                        {
                            "window_end": int(s.index[i]),
                            "test_statistic": None,
                            "p_value": None,
                            "stationary": False,
                        }
                    )

            out_name = f"rolling_adf_{col}_w{window}.csv"
            pd.DataFrame(results_list).to_csv(
                os.path.join(workspace, out_name), index=False
            )

            valid = [r for r in results_list if r["p_value"] is not None]
            stat_pct = (
                sum(1 for r in valid if r["stationary"]) / len(valid) * 100
                if valid
                else 0
            )
            result = {
                "metric": "rolling_adf",
                "window": window,
                "step": step,
                "total_windows": len(results_list),
                "stationary_pct": round(stat_pct, 1),
                "saved_to": out_name,
            }

        elif metric == "volatility":
            col = method_params.get("column", col1)
            vol_type = method_params.get("vol_type", "realized")

            if vol_type == "realized":
                s = df[col].dropna().astype(float).pct_change().dropna()
                annual_factor = _infer_annual_factor(df)
                rolling_vol = s.rolling(window).std() * np.sqrt(annual_factor)
            elif vol_type == "parkinson":
                high_col = _resolve_column_name(df, ["high", "High"])
                low_col = _resolve_column_name(df, ["low", "Low"])
                if not (high_col and low_col):
                    return "Error: Parkinson volatility requires High and Low columns"
                hl = np.log(df[high_col].astype(float) / df[low_col].astype(float))
                annual_factor = _infer_annual_factor(df)
                rolling_vol = hl.rolling(window).apply(
                    lambda x: np.sqrt(np.sum(x**2) / (4 * len(x) * np.log(2)))
                ) * np.sqrt(annual_factor)
            else:
                return (
                    f"Error: Unknown vol_type '{vol_type}'. "
                    f"Supported: realized, parkinson"
                )

            out_name = f"rolling_vol_{col}_w{window}.csv"
            pd.DataFrame({col: df[col], "rolling_volatility": rolling_vol}).to_csv(
                os.path.join(workspace, out_name), index=False
            )

            rv = rolling_vol.dropna()
            result = {
                "metric": "rolling_volatility",
                "vol_type": vol_type,
                "window": window,
                "mean_volatility": round(float(rv.mean()), 4),
                "current_volatility": round(float(rv.iloc[-1]), 4),
                "saved_to": out_name,
            }

        else:
            return (
                f"Error: Unknown rolling metric '{metric}'. "
                f"Supported: correlation, cointegration, beta, adf, volatility"
            )

        return json.dumps(result, indent=2)

    else:
        return (
            f"Error: Unknown method '{method}'. "
            f"Supported: ADF, CORRELATION, COINTEGRATION, DESCRIPTIVE, "
            f"MISSING, LEAD_LAG, ROLLING"
        )


def plot_chart(python_code: str) -> str:
    """Execute matplotlib Python code in-process and save the chart as PNG.

    Self-contained: uses exec() directly. Does NOT call shell_exec or spawn
    a subprocess. The Agg backend is forced so no display server is needed.

    The provided code should create matplotlib figures. This tool automatically
    appends plt.savefig() and plt.close() — the caller does not need to
    include them (but including them is harmless).
    """
    import time as _time

    import matplotlib

    matplotlib.use("Agg")

    workspace = _workspace_dir()
    chart_path = os.path.join(workspace, f"chart_{int(_time.time())}.png")

    full_code = (
        python_code
        + "\nimport matplotlib.pyplot as plt\n"
        + f"plt.savefig('{chart_path}', dpi=100, bbox_inches='tight')\n"
        + "plt.close('all')\n"
    )

    try:
        exec(full_code, {"__builtins__": __builtins__})
    except Exception as e:
        return f"Error generating chart: {type(e).__name__}: {e}"

    if os.path.isfile(chart_path):
        return f"Chart saved to {chart_path}"
    return "Error: Chart file was not generated"


def analyze_backtest_results(data_path: str, returns_column: str = "returns") -> str:
    """Analyze a CSV with portfolio/strategy returns and compute performance metrics.

    Self-contained: uses pandas/numpy via the shared _compute_performance_metrics
    helper. Does NOT call shell_exec or any other MCP tool.

    Auto-detects the returns column from common names (returns, daily_return,
    strategy_return, pnl). If only a 'Close' price column exists, daily
    returns are computed automatically.
    """
    import pandas as pd

    full_path = _resolve_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"

    df = pd.read_csv(full_path)

    # Auto-detect returns column
    ret_col = None
    for candidate in [
        returns_column,
        "returns",
        "Returns",
        "daily_return",
        "daily_returns",
        "strategy_return",
        "strategy_returns",
        "pnl",
        "PnL",
    ]:
        if candidate in df.columns:
            ret_col = candidate
            break
    if ret_col is None and "Close" in df.columns:
        df["_returns"] = df["Close"].pct_change()
        ret_col = "_returns"
    if ret_col is None:
        return f"Error: No returns column found. Available columns: {list(df.columns)}"

    returns = df[ret_col].dropna()
    if len(returns) < 2:
        return f"Error: Not enough data points ({len(returns)}) to compute metrics."

    # Use shared helper for metric computation
    metrics = _compute_performance_metrics(returns)
    metrics["data_path"] = data_path

    # Save to workspace as structured JSON
    base = os.path.splitext(os.path.basename(data_path))[0]
    out_name = f"{base}_analysis.json"
    out_path = os.path.join(_workspace_dir(), out_name)
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)

    return (
        f"Backtest Analysis (saved to {out_name}):\n"
        f"  Sharpe Ratio:   {metrics['sharpe_ratio']}\n"
        f"  Annual Return:  {metrics['annual_return']:.2%}\n"
        f"  Total Return:   {metrics['total_return']:.2%}\n"
        f"  Max Drawdown:   {metrics['max_drawdown']:.2%}\n"
        f"  Win Rate:       {metrics['win_rate']:.2%}\n"
        f"  Volatility:     {metrics['volatility']:.2%}\n"
        f"  Sortino Ratio:  {metrics['sortino_ratio']}\n"
        f"  Calmar Ratio:   {metrics['calmar_ratio']}\n"
    )


def evaluate_signal(
    file_path: str,
    forward_periods: int = 1,
    quantiles: int = 5,
    decay_lags: int = 5,
) -> str:
    """Evaluate a trading signal against forward returns.

    Input CSV must contain at least a ``signal`` column and a close-price
    column (``close`` or ``Close``). If no returns column is present, period
    returns are derived from close prices.
    """
    import numpy as np
    import pandas as pd
    from scipy.stats import spearmanr
    from scipy.stats import t as student_t

    full_path = _resolve_path(file_path)
    if not full_path:
        return f"Error: File not found: {file_path}"

    forward_periods = max(1, int(forward_periods or 1))
    quantiles = max(2, int(quantiles or 5))
    decay_lags = max(1, int(decay_lags or 5))

    df = pd.read_csv(full_path)

    signal_col = _resolve_column_name(df, ["signal"])
    close_col = _resolve_column_name(
        df, ["close", "Close", "adj_close", "Adj Close", "price"]
    )
    returns_col = _resolve_column_name(
        df,
        [
            "returns",
            "return",
            "daily_return",
            "strategy_return",
            "pct_return",
        ],
    )

    if signal_col is None:
        return "Error: CSV must contain a 'signal' column."
    if close_col is None:
        return "Error: CSV must contain a 'close' or 'Close' column."

    signal = pd.to_numeric(df[signal_col], errors="coerce")
    close = pd.to_numeric(df[close_col], errors="coerce")
    returns = (
        pd.to_numeric(df[returns_col], errors="coerce")
        if returns_col is not None
        else close.pct_change()
    )
    forward_return = close.shift(-forward_periods) / close - 1

    aligned = pd.DataFrame(
        {
            "signal": signal,
            "returns": returns,
            "forward_return": forward_return,
        }
    ).dropna(subset=["signal", "forward_return"])

    if len(aligned) < max(10, quantiles * 2):
        return (
            "Error: Not enough aligned observations to evaluate the signal. "
            f"Only found {len(aligned)} usable rows."
        )

    def _spearman_corr(x, y) -> float:
        if len(x) < 3:
            return np.nan
        if (
            pd.Series(x).nunique(dropna=True) < 2
            or pd.Series(y).nunique(dropna=True) < 2
        ):
            return np.nan
        corr = spearmanr(x, y).correlation
        return float(corr) if corr is not None and np.isfinite(corr) else np.nan

    ic_decay: list[float] = []
    for lag in range(1, decay_lags + 1):
        lag_forward = close.shift(-lag) / close - 1
        lag_aligned = pd.DataFrame(
            {"signal": signal, "forward_return": lag_forward}
        ).dropna()
        lag_ic = _spearman_corr(
            lag_aligned["signal"],
            lag_aligned["forward_return"],
        )
        ic_decay.append(_safe_round(0.0 if np.isnan(lag_ic) else lag_ic, 4) or 0.0)

    window = min(60, max(20, len(aligned) // 5))
    rolling_ic: list[float] = []
    if len(aligned) >= window:
        for end in range(window, len(aligned) + 1):
            segment = aligned.iloc[end - window : end]
            corr = _spearman_corr(segment["signal"], segment["forward_return"])
            if not np.isnan(corr):
                rolling_ic.append(float(corr))

    if len(rolling_ic) >= 2:
        ic_mean = float(np.mean(rolling_ic))
        ic_std = float(np.std(rolling_ic, ddof=1))
        ic_ir = ic_mean / ic_std if ic_std > 0 else 0.0
        ic_tstat = ic_mean / (ic_std / np.sqrt(len(rolling_ic))) if ic_std > 0 else 0.0
        ic_pvalue = (
            2 * float(student_t.sf(abs(ic_tstat), df=len(rolling_ic) - 1))
            if ic_std > 0
            else 1.0
        )
    else:
        single_ic = _spearman_corr(aligned["signal"], aligned["forward_return"])
        ic_mean = 0.0 if np.isnan(single_ic) else float(single_ic)
        ic_std = 0.0
        ic_ir = 0.0
        ic_tstat = 0.0
        ic_pvalue = 1.0

    non_zero = aligned["signal"] != 0
    if non_zero.any():
        hit_rate = float(
            (
                np.sign(aligned.loc[non_zero, "signal"])
                == np.sign(aligned.loc[non_zero, "forward_return"])
            ).mean()
        )
    else:
        hit_rate = 0.0

    turnover = (
        float(signal.diff().abs().dropna().mean()) if signal.notna().sum() > 1 else 0.0
    )
    signal_autocorrelation = (
        float(signal.dropna().autocorr(lag=1)) if signal.dropna().shape[0] > 2 else 0.0
    )

    ranked = aligned.copy()
    ranked["bucket"] = pd.qcut(
        ranked["signal"].rank(method="first"),
        q=min(quantiles, ranked["signal"].nunique()),
        labels=False,
        duplicates="drop",
    )
    quantile_returns = (
        ranked.groupby("bucket", observed=False)["forward_return"].mean().tolist()
    )
    if quantile_returns:
        long_short_spread = float(quantile_returns[-1] - quantile_returns[0])
        if len(quantile_returns) > 1:
            monotonicity = _spearman_corr(
                pd.Series(range(len(quantile_returns))),
                pd.Series(quantile_returns),
            )
            monotonicity_score = max(
                0.0, 0.0 if np.isnan(monotonicity) else monotonicity
            )
        else:
            monotonicity_score = 0.0
    else:
        long_short_spread = 0.0
        monotonicity_score = 0.0

    strategy_return = (signal.shift(1) * returns).dropna()
    annual_factor = _infer_annual_factor(df)
    if len(strategy_return) >= 2:
        equity = (1 + strategy_return.fillna(0)).cumprod()
        total_return = float(equity.iloc[-1] - 1)
        annualized_return = (
            float((equity.iloc[-1]) ** (annual_factor / len(strategy_return)) - 1)
            if equity.iloc[-1] > 0
            else -1.0
        )
        sharpe = (
            float(
                strategy_return.mean() / strategy_return.std() * np.sqrt(annual_factor)
            )
            if float(strategy_return.std()) > 0
            else 0.0
        )
        running_max = equity.cummax()
        max_drawdown = float(((equity / running_max) - 1).min())
    else:
        total_return = 0.0
        annualized_return = 0.0
        sharpe = 0.0
        max_drawdown = 0.0

    corr_abs_return = _spearman_corr(aligned["signal"], aligned["forward_return"].abs())

    result = {
        "signal_metrics": {
            "ic_mean": _safe_round(ic_mean, 4),
            "ic_std": _safe_round(ic_std, 4),
            "ic_ir": _safe_round(ic_ir, 4),
            "ic_tstat": _safe_round(ic_tstat, 4),
            "ic_pvalue": _safe_round(ic_pvalue, 4),
            "ic_decay": [
                _safe_round(value, 4) if value is not None else 0.0
                for value in ic_decay
            ],
            "hit_rate": _safe_round(hit_rate, 4),
            "turnover": _safe_round(turnover, 4),
            "signal_autocorrelation": _safe_round(signal_autocorrelation, 4),
        },
        "quantile_analysis": {
            "quantile_mean_returns": [
                _safe_round(value, 6) if value is not None else 0.0
                for value in quantile_returns
            ],
            "long_short_spread": _safe_round(long_short_spread, 6),
            "monotonicity_score": _safe_round(monotonicity_score, 4),
        },
        "rough_pnl": {
            "total_return": _safe_round(total_return, 4),
            "annualized_return": _safe_round(annualized_return, 4),
            "annualized_sharpe": _safe_round(sharpe, 4),
            "max_drawdown": _safe_round(max_drawdown, 4),
            "num_observations": int(len(strategy_return)),
        },
        "diagnostics": {
            "signal_coverage": _safe_round(float(signal.notna().mean()), 4),
            "signal_mean": _safe_round(float(signal.mean()), 4),
            "signal_std": _safe_round(float(signal.std()), 4),
            "forward_return_mean": _safe_round(
                float(aligned["forward_return"].mean()), 6
            ),
            "correlation_signal_abs_return": _safe_round(
                0.0 if np.isnan(corr_abs_return) else corr_abs_return,
                4,
            ),
        },
    }

    base = os.path.splitext(os.path.basename(file_path))[0]
    out_name = f"{base}_signal_evaluation.json"
    out_path = os.path.join(_workspace_dir(), out_name)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    result["saved_to"] = out_name
    return json.dumps(result, indent=2)


def construct_signal(
    data_path: str,
    signal_type: str,
    signal_params: Optional[dict] = None,
    output_name: Optional[str] = None,
    close_column: Optional[str] = None,
) -> str:
    """Construct a trading signal from data, save as CSV ready for evaluate_signal.

    The output CSV always includes a ``signal`` column and a ``close`` column
    so that it can be directly passed to ``evaluate_signal()``.
    Use ``close_column`` to specify which column is the close price when
    the CSV does not have a standard 'close'/'Close' column (e.g. merged
    cross-asset data with 'BTC_Close', 'ETH_Close').

    Supported signal_type values:
      zscore          - Rolling z-score of a column.
      momentum        - Returns-based momentum (pct_change, log_return, rank).
      mean_reversion  - Negative z-score, Bollinger position, or RSI-based.
      spread          - Regression residual between two series (OLS, rolling_ols,
                        log_ratio).
      crossover       - Moving-average crossover (SMA or EMA).
      composite       - Weighted combination of existing columns.
      volume_imbalance - Microstructure signals (taker_ratio, volume_zscore,
                         trade_intensity, OBV).
    """
    import numpy as np
    import pandas as pd

    signal_params = signal_params or {}
    full_path = _resolve_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"

    df = pd.read_csv(full_path)
    signal_type = signal_type.lower()
    close_col = _resolve_column_name(
        df, ["close", "Close", "adj_close", "Adj Close", "price"]
    )

    # ── zscore ──────────────────────────────────────────────────
    if signal_type == "zscore":
        column = signal_params.get("column", close_col or df.columns[1])
        lookback = int(signal_params.get("lookback", 20))
        returns_based = signal_params.get("returns_based", False)

        s = df[column].astype(float)
        if returns_based:
            s = s.pct_change()

        rolling_mean = s.rolling(lookback).mean()
        rolling_std = s.rolling(lookback).std()
        df["signal"] = (s - rolling_mean) / rolling_std.replace(0, np.nan)
        desc = (
            f"Z-score of {column} (lookback={lookback}, returns_based={returns_based})"
        )

    # ── momentum ────────────────────────────────────────────────
    elif signal_type == "momentum":
        column = signal_params.get("column", close_col or df.columns[1])
        lookback = int(signal_params.get("lookback", 20))
        method = signal_params.get("method", "pct_change")
        normalize = signal_params.get("normalize", False)

        s = df[column].astype(float)
        if method == "pct_change":
            raw = s.pct_change(lookback)
        elif method == "log_return":
            raw = np.log(s / s.shift(lookback))
        elif method == "rank":
            raw = (
                s.pct_change(lookback)
                .rolling(max(lookback, 60))
                .apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1])
            )
        else:
            return (
                f"Error: Unknown momentum method '{method}'. "
                f"Supported: pct_change, log_return, rank"
            )

        if normalize:
            norm_window = int(signal_params.get("normalize_window", 60))
            rm = raw.rolling(norm_window).mean()
            rs = raw.rolling(norm_window).std()
            df["signal"] = (raw - rm) / rs.replace(0, np.nan)
        else:
            df["signal"] = raw
        desc = f"Momentum {method} (lookback={lookback}, normalize={normalize})"

    # ── mean_reversion ──────────────────────────────────────────
    elif signal_type == "mean_reversion":
        column = signal_params.get("column", close_col or df.columns[1])
        lookback = int(signal_params.get("lookback", 20))
        method = signal_params.get("method", "zscore")

        s = df[column].astype(float)
        if method == "zscore":
            rm = s.rolling(lookback).mean()
            rs = s.rolling(lookback).std()
            df["signal"] = -(s - rm) / rs.replace(0, np.nan)
        elif method == "bollinger":
            std_dev = float(signal_params.get("std_dev", 2))
            rm = s.rolling(lookback).mean()
            rs = s.rolling(lookback).std()
            band_width = 2 * std_dev * rs
            df["signal"] = -(s - rm) / band_width.replace(0, np.nan)
        elif method == "rsi":
            delta = s.diff()
            gain = delta.where(delta > 0, 0).rolling(lookback).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(lookback).mean()
            rs_val = gain / loss
            rsi = 100 - (100 / (1 + rs_val))
            df["signal"] = -(rsi - 50) / 50
        else:
            return (
                f"Error: Unknown mean_reversion method '{method}'. "
                f"Supported: zscore, bollinger, rsi"
            )
        desc = f"Mean reversion {method} (lookback={lookback})"

    # ── spread ──────────────────────────────────────────────────
    elif signal_type == "spread":
        if "column1" not in signal_params or "column2" not in signal_params:
            if len(df.columns) < 3:
                return (
                    "Error: spread requires 'column1' and 'column2' in signal_params. "
                    f"Available columns: {list(df.columns)}"
                )
        col1 = signal_params.get("column1", df.columns[1])
        col2 = signal_params.get(
            "column2", df.columns[2] if len(df.columns) > 2 else df.columns[1]
        )
        method = signal_params.get("method", "ols")
        lookback = int(signal_params.get("lookback", 60))
        normalize_spread = signal_params.get("normalize", True)

        y = df[col1].astype(float)
        x = df[col2].astype(float)

        if method == "ols":
            import statsmodels.api as sm

            valid = y.notna() & x.notna()
            X_ols = sm.add_constant(x[valid].values)
            mdl = sm.OLS(y[valid].values, X_ols).fit()
            spread = y - mdl.params[1] * x - mdl.params[0]
        elif method == "rolling_ols":
            import statsmodels.api as sm

            spread = pd.Series(np.nan, index=df.index)
            for i in range(lookback, len(df)):
                y_w = y.iloc[i - lookback : i]
                x_w = x.iloc[i - lookback : i]
                valid = y_w.notna() & x_w.notna()
                if valid.sum() < 10:
                    continue
                X_ols = sm.add_constant(x_w[valid].values)
                mdl = sm.OLS(y_w[valid].values, X_ols).fit()
                spread.iloc[i] = y.iloc[i] - mdl.params[1] * x.iloc[i] - mdl.params[0]
        elif method == "log_ratio":
            spread = np.log(y / x.replace(0, np.nan))
        else:
            return (
                f"Error: Unknown spread method '{method}'. "
                f"Supported: ols, rolling_ols, log_ratio"
            )

        if normalize_spread:
            rm = spread.rolling(lookback).mean()
            rs = spread.rolling(lookback).std()
            df["signal"] = (spread - rm) / rs.replace(0, np.nan)
        else:
            df["signal"] = spread
        desc = (
            f"Spread {method} ({col1} vs {col2}, "
            f"lookback={lookback}, normalize={normalize_spread})"
        )

    # ── crossover ───────────────────────────────────────────────
    elif signal_type == "crossover":
        column = signal_params.get("column", close_col or df.columns[1])
        fast = int(signal_params.get("fast", 10))
        slow = int(signal_params.get("slow", 30))
        ma_type = signal_params.get("ma_type", "ema")

        s = df[column].astype(float)
        if ma_type == "sma":
            fast_ma = s.rolling(fast).mean()
            slow_ma = s.rolling(slow).mean()
        elif ma_type == "ema":
            fast_ma = s.ewm(span=fast).mean()
            slow_ma = s.ewm(span=slow).mean()
        else:
            return f"Error: Unknown ma_type '{ma_type}'. Supported: sma, ema"

        df["signal"] = (fast_ma - slow_ma) / slow_ma.replace(0, np.nan)
        desc = f"Crossover {ma_type} (fast={fast}, slow={slow})"

    # ── composite ───────────────────────────────────────────────
    elif signal_type == "composite":
        columns = signal_params.get("columns", [])
        weights = signal_params.get("weights", None)
        combination = signal_params.get("combination", "weighted_mean")

        if not columns:
            return (
                "Error: composite signal requires 'columns' parameter "
                "(list of column names containing individual signals)"
            )
        missing = [c for c in columns if c not in df.columns]
        if missing:
            return (
                f"Error: columns not found: {missing}. "
                f"Available: {list(df.columns)}"
            )

        signal_df = df[columns].astype(float)

        if combination == "weighted_mean":
            if weights is None:
                weights = [1.0 / len(columns)] * len(columns)
            if len(weights) != len(columns):
                return (
                    f"Error: weights length ({len(weights)}) must "
                    f"match columns length ({len(columns)})"
                )
            df["signal"] = sum(w * signal_df[c] for w, c in zip(weights, columns))
        elif combination == "rank_mean":
            ranked = signal_df.rank(pct=True)
            if weights:
                df["signal"] = sum(w * ranked[c] for w, c in zip(weights, columns))
            else:
                df["signal"] = ranked.mean(axis=1)
        elif combination == "pca":
            from sklearn.decomposition import PCA

            valid = signal_df.dropna()
            if len(valid) < 10:
                return "Error: Not enough valid rows for PCA"
            pca = PCA(n_components=1)
            pc1 = pca.fit_transform((valid - valid.mean()) / valid.std().replace(0, 1))
            df.loc[valid.index, "signal"] = pc1.flatten()
        else:
            return (
                f"Error: Unknown combination '{combination}'. "
                f"Supported: weighted_mean, rank_mean, pca"
            )
        desc = f"Composite {combination} of {columns}"

    # ── volume_imbalance ────────────────────────────────────────
    elif signal_type == "volume_imbalance":
        method = signal_params.get("method", "taker_ratio")
        lookback = int(signal_params.get("lookback", 20))
        normalize = signal_params.get("normalize", True)

        if method == "taker_ratio":
            buy_col = _resolve_column_name(
                df,
                ["taker_buy_base_vol", "Taker_buy_base_vol", "taker_buy_volume"],
            )
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            if not (buy_col and vol_col):
                return (
                    f"Error: taker_ratio needs taker_buy_base_vol and volume. "
                    f"Available: {list(df.columns)}"
                )
            buy = df[buy_col].astype(float)
            vol = df[vol_col].astype(float)
            raw = buy / vol.replace(0, np.nan) - 0.5

        elif method == "volume_zscore":
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            if not vol_col:
                return (
                    f"Error: volume_zscore needs volume column. "
                    f"Available: {list(df.columns)}"
                )
            raw = df[vol_col].astype(float)

        elif method == "trade_intensity":
            trades_col = _resolve_column_name(
                df,
                ["trades", "Trades", "trade_count", "num_trades"],
            )
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            if not (trades_col and vol_col):
                return (
                    f"Error: trade_intensity needs trades and volume. "
                    f"Available: {list(df.columns)}"
                )
            raw = df[trades_col].astype(float) / df[vol_col].astype(float).replace(
                0, np.nan
            )

        elif method == "obv":
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            if not (close_col and vol_col):
                return "Error: OBV requires close and volume columns"
            direction = np.sign(df[close_col].astype(float).diff())
            raw = (direction * df[vol_col].astype(float)).cumsum()
            # For OBV, use rate of change as the signal
            if normalize:
                df["signal"] = raw.pct_change(lookback)
                normalize = False  # skip double-normalize below
            else:
                df["signal"] = raw

        else:
            return (
                f"Error: Unknown volume_imbalance method '{method}'. "
                f"Supported: taker_ratio, volume_zscore, trade_intensity, obv"
            )

        if normalize and "signal" not in df.columns:
            rm = raw.rolling(lookback).mean()
            rs = raw.rolling(lookback).std()
            df["signal"] = (raw - rm) / rs.replace(0, np.nan)
        elif "signal" not in df.columns:
            df["signal"] = raw

        desc = (
            f"Volume imbalance {method} "
            f"(lookback={lookback}, normalize={normalize})"
        )

    else:
        return (
            f"Error: Unknown signal_type '{signal_type}'. Supported: "
            f"zscore, momentum, mean_reversion, spread, crossover, "
            f"composite, volume_imbalance"
        )

    # Ensure close column exists in output for evaluate_signal.
    # Priority: explicit close_column param > auto-detected close_col.
    effective_close = close_column or close_col
    if effective_close and effective_close in df.columns:
        if effective_close.lower() != "close":
            df["close"] = df[effective_close]
    elif close_column and close_column not in df.columns:
        return (
            f"Error: close_column '{close_column}' not found. "
            f"Available: {list(df.columns)}"
        )

    # Save
    if output_name is None:
        base = os.path.splitext(os.path.basename(data_path))[0]
        output_name = f"{base}_{signal_type}_signal.csv"

    out_path = os.path.join(_workspace_dir(), output_name)
    df.to_csv(out_path, index=False)

    sig = df["signal"].dropna()
    stats = {
        "signal_type": signal_type,
        "description": desc,
        "observations": int(len(sig)),
        "coverage": round(float(sig.count() / len(df)), 4),
        "mean": round(float(sig.mean()), 6),
        "std": round(float(sig.std()), 6),
        "min": round(float(sig.min()), 6),
        "max": round(float(sig.max()), 6),
        "saved_to": output_name,
        "ready_for": "evaluate_signal",
    }
    return json.dumps(stats, indent=2)


def engineer_features(
    data_path: str,
    features: list,
    feature_params: Optional[dict] = None,
    output_name: Optional[str] = None,
) -> str:
    """Engineer quantitative features from OHLCV+ data.

    Adds computed feature columns and saves the enriched dataset.
    Gracefully skips features whose required columns are missing.

    Supported feature names:
      vwap_ratio, volume_zscore, taker_imbalance, trade_intensity,
      returns_multi, realized_vol, parkinson_vol, log_volume_ratio,
      obv, atr, price_acceleration
    """
    import numpy as np
    import pandas as pd

    feature_params = feature_params or {}
    full_path = _resolve_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"

    df = pd.read_csv(full_path)
    close_col = _resolve_column_name(df, ["close", "Close"])
    added = []

    for feat in features:
        feat = feat.lower().strip()

        if feat == "vwap_ratio":
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            quote_col = _resolve_column_name(
                df,
                ["quote_vol", "Quote_vol", "quote_volume", "Quote Volume"],
            )
            if vol_col and quote_col and close_col:
                vwap = df[quote_col].astype(float) / df[vol_col].astype(float).replace(
                    0, np.nan
                )
                df["vwap_ratio"] = (df[close_col].astype(float) - vwap) / vwap
                added.append("vwap_ratio")
            else:
                added.append("vwap_ratio (SKIPPED: need volume+quote_vol+close)")

        elif feat == "volume_zscore":
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            lb = int(feature_params.get("volume_zscore_lookback", 20))
            if vol_col:
                vol = df[vol_col].astype(float)
                df["volume_zscore"] = (vol - vol.rolling(lb).mean()) / vol.rolling(
                    lb
                ).std().replace(0, np.nan)
                added.append(f"volume_zscore (lookback={lb})")
            else:
                added.append("volume_zscore (SKIPPED: no volume column)")

        elif feat == "taker_imbalance":
            buy_col = _resolve_column_name(
                df,
                ["taker_buy_base_vol", "Taker_buy_base_vol", "taker_buy_volume"],
            )
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            lb = int(feature_params.get("taker_imbalance_lookback", 20))
            if buy_col and vol_col:
                ratio = df[buy_col].astype(float) / df[vol_col].astype(float).replace(
                    0, np.nan
                )
                df["taker_imbalance"] = ratio - ratio.rolling(lb).mean()
                added.append(f"taker_imbalance (lookback={lb})")
            else:
                added.append("taker_imbalance (SKIPPED: need taker_buy+volume)")

        elif feat == "trade_intensity":
            trades_col = _resolve_column_name(
                df,
                ["trades", "Trades", "trade_count", "num_trades"],
            )
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            lb = int(feature_params.get("trade_intensity_lookback", 20))
            if trades_col and vol_col:
                intensity = df[trades_col].astype(float) / df[vol_col].astype(
                    float
                ).replace(0, np.nan)
                df["trade_intensity"] = (
                    intensity - intensity.rolling(lb).mean()
                ) / intensity.rolling(lb).std().replace(0, np.nan)
                added.append(f"trade_intensity (lookback={lb})")
            else:
                added.append("trade_intensity (SKIPPED: need trades+volume)")

        elif feat == "returns_multi":
            horizons = feature_params.get("returns_horizons", [1, 5, 10, 20])
            if close_col:
                price = df[close_col].astype(float)
                for h in horizons:
                    col_name = f"return_{h}d"
                    df[col_name] = price.pct_change(int(h))
                    added.append(col_name)
            else:
                added.append("returns_multi (SKIPPED: no close column)")

        elif feat == "realized_vol":
            lb = int(feature_params.get("realized_vol_lookback", 20))
            if close_col:
                rets = df[close_col].astype(float).pct_change()
                af = _infer_annual_factor(df)
                df["realized_vol"] = rets.rolling(lb).std() * np.sqrt(af)
                added.append(f"realized_vol (lookback={lb})")
            else:
                added.append("realized_vol (SKIPPED: no close column)")

        elif feat == "parkinson_vol":
            high_col = _resolve_column_name(df, ["high", "High"])
            low_col = _resolve_column_name(df, ["low", "Low"])
            lb = int(feature_params.get("parkinson_vol_lookback", 20))
            if high_col and low_col:
                hl = np.log(df[high_col].astype(float) / df[low_col].astype(float))
                af = _infer_annual_factor(df)
                df["parkinson_vol"] = hl.rolling(lb).apply(
                    lambda x: np.sqrt(np.sum(x**2) / (4 * len(x) * np.log(2)))
                ) * np.sqrt(af)
                added.append(f"parkinson_vol (lookback={lb})")
            else:
                added.append("parkinson_vol (SKIPPED: need High+Low)")

        elif feat == "log_volume_ratio":
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            quote_col = _resolve_column_name(
                df, ["quote_vol", "Quote_vol", "quote_volume"]
            )
            if vol_col and quote_col:
                df["log_volume_ratio"] = np.log(
                    df[quote_col].astype(float)
                    / df[vol_col].astype(float).replace(0, np.nan)
                )
                added.append("log_volume_ratio")
            else:
                added.append("log_volume_ratio (SKIPPED: need volume+quote_vol)")

        elif feat == "obv":
            vol_col = _resolve_column_name(df, ["volume", "Volume", "base_vol"])
            if close_col and vol_col:
                direction = np.sign(df[close_col].astype(float).diff())
                df["obv"] = (direction * df[vol_col].astype(float)).cumsum()
                added.append("obv")
            else:
                added.append("obv (SKIPPED: need close+volume)")

        elif feat == "atr":
            high_col = _resolve_column_name(df, ["high", "High"])
            low_col = _resolve_column_name(df, ["low", "Low"])
            lb = int(feature_params.get("atr_lookback", 14))
            if high_col and low_col and close_col:
                h = df[high_col].astype(float)
                lo = df[low_col].astype(float)
                c = df[close_col].astype(float)
                tr = pd.concat(
                    [
                        h - lo,
                        (h - c.shift(1)).abs(),
                        (lo - c.shift(1)).abs(),
                    ],
                    axis=1,
                ).max(axis=1)
                df["atr"] = tr.rolling(lb).mean()
                added.append(f"atr (lookback={lb})")
            else:
                added.append("atr (SKIPPED: need High+Low+Close)")

        elif feat == "price_acceleration":
            lb = int(feature_params.get("price_acceleration_lookback", 5))
            if close_col:
                rets = df[close_col].astype(float).pct_change()
                df["price_acceleration"] = rets.diff(lb)
                added.append(f"price_acceleration (lookback={lb})")
            else:
                added.append("price_acceleration (SKIPPED: no close column)")

        else:
            added.append(f"{feat} (UNKNOWN — skipped)")

    if output_name is None:
        base = os.path.splitext(os.path.basename(data_path))[0]
        output_name = f"{base}_features.csv"

    out_path = os.path.join(_workspace_dir(), output_name)
    df.to_csv(out_path, index=False)

    return json.dumps(
        {
            "added_features": added,
            "total_columns": len(df.columns),
            "total_rows": len(df),
            "saved_to": output_name,
        },
        indent=2,
    )


def search_web(query: str, max_results: int = 5) -> str:
    """Search the public web using DuckDuckGo and return result links with snippets."""
    if not query or not query.strip():
        return "Error: query must be non-empty."

    max_results = max(1, min(int(max_results or 5), 10))

    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS
        except ImportError:
            return "Error: web search unavailable (ddgs package not installed)"

    try:
        raw = DDGS().text(query, max_results=max_results)
    except Exception as exc:
        return f"Error: web search failed: {type(exc).__name__}: {exc}"

    if not raw:
        return json.dumps(
            {"query": query, "results": [], "note": "No web results returned."},
            indent=2,
        )

    results = [
        {
            "title": r.get("title", ""),
            "url": r.get("href", ""),
            "snippet": r.get("body", ""),
        }
        for r in raw
    ]
    return json.dumps({"query": query, "results": results}, indent=2)


def search_docs(query: str) -> str:
    """Keyword search across the /docs/ directory.

    Splits the query into keywords and scores each line by the number of
    keywords it contains (case-insensitive).  Returns the top matches
    ranked by relevance.
    """
    keywords = [kw for kw in query.lower().split() if len(kw) >= 2]
    if not keywords:
        return f"No results found for '{query}'"

    results = []
    docs_dir = _docs_dir()
    for root, _, files in os.walk(docs_dir):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath) as f:
                lines = f.read().split("\n")

            scored_lines: list[tuple[int, int, str]] = []  # (score, lineno, text)
            for i, line in enumerate(lines):
                line_lower = line.lower()
                hits = sum(1 for kw in keywords if kw in line_lower)
                if hits > 0:
                    scored_lines.append((hits, i + 1, line.strip()))

            if scored_lines:
                scored_lines.sort(key=lambda x: -x[0])
                matches = [
                    {"line": lineno, "score": score, "text": text}
                    for score, lineno, text in scored_lines[:10]
                ]
                results.append({"file": fname, "matches": matches})

    if not results:
        return f"No results found for '{query}'"
    results.sort(key=lambda r: -r["matches"][0]["score"])
    return json.dumps(results, indent=2)


def get_environment_info() -> str:
    """Return directory paths, available files, and installed packages.

    Provides explicit absolute paths so the agent knows where to find
    files when using shell_exec (e.g. ``pd.read_csv('/data/AAPL.csv')``).
    Automatically detects LEAN/dotnet environments and reports them.
    """
    data_dir = _data_dir()
    docs_dir = _docs_dir()
    workspace = _workspace_dir()
    student_code_dir = _student_code_dir()
    session_context = _session_context()

    info = {
        "directories": {
            "data": data_dir,
            "docs": docs_dir,
            "workspace": workspace,
        },
        "data_files": [],
        "docs": [],
        "workspace": [],
        "installed_packages": [],
    }
    if os.path.isdir(student_code_dir):
        info["directories"]["student_code"] = student_code_dir
    for d, key in [
        (data_dir, "data_files"),
        (docs_dir, "docs"),
        (workspace, "workspace"),
    ]:
        if os.path.isdir(d):
            info[key] = sorted(os.listdir(d))
    try:
        result = subprocess.run(
            "pip list --format=columns",
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        info["installed_packages"] = result.stdout.strip().split("\n")[:20]
    except Exception:
        info["installed_packages"] = ["(unable to list)"]

    # ---------- LEAN / .NET detection ----------
    lean_info: dict = {}
    try:
        dotnet_result = subprocess.run(
            "dotnet --version",
            shell=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if dotnet_result.returncode == 0:
            lean_info["dotnet_version"] = dotnet_result.stdout.strip()
    except Exception:
        pass

    lean_launcher = "/lean/Launcher/bin/Debug/QuantConnect.Lean.Launcher.dll"
    if os.path.isfile(lean_launcher):
        lean_info["lean_engine"] = True
        lean_info["lean_root"] = "/lean"

    run_backtest_path = "/usr/local/bin/run_backtest"
    if os.path.isfile(run_backtest_path):
        lean_info["run_backtest"] = run_backtest_path

    lean_metadata_dir = "/lean/Data"
    if os.path.isdir(lean_metadata_dir):
        lean_info["lean_metadata_root"] = lean_metadata_dir
        lean_info["metadata_sidecars"] = {
            "universe_json": os.path.isfile(
                os.path.join(lean_metadata_dir, "universe.json")
            ),
            "market_hours_database": os.path.isfile(
                os.path.join(
                    lean_metadata_dir,
                    "market-hours",
                    "market-hours-database.json",
                )
            ),
            "symbol_properties_database": os.path.isfile(
                os.path.join(
                    lean_metadata_dir,
                    "symbol-properties",
                    "symbol-properties-database.csv",
                )
            ),
            "security_database": os.path.isfile(
                os.path.join(
                    lean_metadata_dir,
                    "symbol-properties",
                    "security-database.csv",
                )
            ),
        }

    custom_data_root = "/data/custom/binance"
    if os.path.isdir(custom_data_root):
        lean_info["custom_data_root"] = custom_data_root
        custom_data_summary: dict = {}
        for res in sorted(os.listdir(custom_data_root)):
            res_path = os.path.join(custom_data_root, res)
            if os.path.isdir(res_path):
                symbols = sorted(
                    name
                    for name in os.listdir(res_path)
                    if os.path.isdir(os.path.join(res_path, name))
                )
                custom_data_summary[res] = symbols[:10]
        if custom_data_summary:
            lean_info["available_data"] = custom_data_summary

    if lean_info:
        info["lean_environment"] = lean_info
    if session_context:
        info["session_context"] = session_context

    # ---------- Note (context-aware) ----------
    if lean_info.get("lean_engine"):
        note_parts = [
            f"Data files are in {data_dir}.",
            f"This environment has LEAN Engine (QuantConnect) with .NET {lean_info.get('dotnet_version', 'unknown')}.",
            (
                f"Tracked LEAN runs should generally use run_lean_backtest; "
                f"source files may come from {workspace}"
                + (
                    f" and, when relevant, {student_code_dir}"
                    if os.path.isdir(student_code_dir)
                    else ""
                )
                + "."
            ),
            "run_lean_backtest can infer a C# entrypoint from the source file and also accepts explicit class_name or full_type_name overrides.",
            "12-col custom market data is pre-loaded at /data/custom/binance and required LEAN metadata is mounted at /lean/Data.",
            f"Detailed run artifacts are written under {workspace}/results/ and can be inspected with file_read().",
            "Python is also available for analysis (pandas 3.0 — use df.ffill()/df.bfill() instead of fillna(method=...); order status values are lowercase e.g. 'filled' not 'Filled').",
            f"Workspace for saving outputs: {workspace}.",
        ]
        if os.path.isdir(student_code_dir):
            note_parts.append(
                f"A task-provided code directory is mounted at {student_code_dir}; inspect it with file_list/file_read when relevant."
            )
        if session_context.get("max_backtest_trials"):
            note_parts.append(
                f"Tracked LEAN backtests are budgeted at {session_context['max_backtest_trials']} trial(s) for this session."
            )
        info["note"] = " ".join(note_parts)
    else:
        note_parts = [
            f"Data files are in {data_dir}.",
            f"Use absolute paths in Python code, e.g. pd.read_csv('{data_dir}/FILENAME.csv').",
            f"Workspace for saving outputs: {workspace}.",
        ]
        if os.path.isdir(student_code_dir):
            note_parts.append(
                f"A task-provided code directory is mounted at {student_code_dir}; inspect it with file_list/file_read when relevant."
            )
        info["note"] = " ".join(note_parts)

    return json.dumps(info, indent=2)


def compare_backtest_results(
    data_paths: list,
    labels: Optional[list] = None,
    returns_column: str = "returns",
    metrics: Optional[list] = None,
    significance_test: str = "none",
    bootstrap_samples: int = 1000,
    benchmark_index: int = 0,
) -> str:
    """Compare performance metrics across multiple backtest result CSVs.

    Loads N backtest result CSVs, computes standard performance metrics for
    each, and presents a side-by-side comparison.  Optionally runs statistical
    significance tests (bootstrap CI or paired t-test) to determine if
    differences are meaningful.

    Args:
        data_paths: List of CSV file paths (2+).
        labels: Human-readable labels for each backtest. Defaults to filenames.
        returns_column: Returns column name (auto-detected from common names).
        metrics: Subset of metrics to compare. None = all standard metrics.
        significance_test: "none", "bootstrap", or "paired_t".
        bootstrap_samples: Number of bootstrap resamples (bootstrap mode).
        benchmark_index: Index in data_paths to use as the reference baseline.
    """
    import numpy as np
    import pandas as pd

    if not isinstance(data_paths, list) or len(data_paths) < 2:
        return "Error: data_paths must be a list of at least 2 CSV file paths."

    # Resolve paths and load returns
    all_returns = []
    resolved_labels = []
    for i, dp in enumerate(data_paths):
        full = _resolve_path(dp)
        if not full:
            return f"Error: File not found: {dp}"
        df = pd.read_csv(full)

        # Auto-detect returns column
        ret_col = None
        for cand in [
            returns_column,
            "returns",
            "Returns",
            "daily_return",
            "daily_returns",
            "strategy_return",
            "strategy_returns",
            "pnl",
            "PnL",
        ]:
            if cand in df.columns:
                ret_col = cand
                break
        if ret_col is None and "Close" in df.columns:
            df["_returns"] = df["Close"].pct_change()
            ret_col = "_returns"
        if ret_col is None:
            return f"Error: No returns column in {dp}. Columns: {list(df.columns)}"

        all_returns.append(df[ret_col].dropna())
        if labels and i < len(labels):
            resolved_labels.append(labels[i])
        else:
            resolved_labels.append(os.path.splitext(os.path.basename(dp))[0])

    # Compute metrics for each
    all_metrics = []
    for ret_series in all_returns:
        m = _compute_performance_metrics(ret_series)
        all_metrics.append(m)

    # Filter metrics if requested
    if metrics:
        for m in all_metrics:
            keys_to_remove = [k for k in m if k not in metrics]
            for k in keys_to_remove:
                del m[k]

    # Build comparison table
    comparison = {"labels": resolved_labels, "metrics": all_metrics}

    # Compute differences relative to benchmark
    bench_idx = min(benchmark_index, len(all_metrics) - 1)
    bench = all_metrics[bench_idx]
    diffs = []
    for i, m in enumerate(all_metrics):
        if i == bench_idx:
            diffs.append({})
            continue
        d = {}
        for k in m:
            if (
                k in bench
                and isinstance(m[k], (int, float))
                and isinstance(bench[k], (int, float))
            ):
                d[k] = round(m[k] - bench[k], 6)
        diffs.append(d)
    comparison["differences_vs_benchmark"] = diffs
    comparison["benchmark"] = resolved_labels[bench_idx]

    # Significance tests
    if significance_test == "bootstrap" and len(all_returns) >= 2:
        np_rng = np.random.default_rng(42)
        sig_results = {}
        bench_ret = all_returns[bench_idx].values
        for i, ret_s in enumerate(all_returns):
            if i == bench_idx:
                continue
            ret_vals = ret_s.values
            # Align lengths
            min_len = min(len(bench_ret), len(ret_vals))
            b_r = bench_ret[:min_len]
            c_r = ret_vals[:min_len]
            diff_sharpes = []
            for _ in range(bootstrap_samples):
                idx = np_rng.integers(0, min_len, size=min_len)
                s_b = b_r[idx]
                s_c = c_r[idx]
                sharpe_b = (
                    (s_b.mean() / s_b.std() * np.sqrt(252)) if s_b.std() > 0 else 0
                )
                sharpe_c = (
                    (s_c.mean() / s_c.std() * np.sqrt(252)) if s_c.std() > 0 else 0
                )
                diff_sharpes.append(sharpe_c - sharpe_b)
            ds = np.array(diff_sharpes)
            ci_low, ci_high = float(np.percentile(ds, 2.5)), float(
                np.percentile(ds, 97.5)
            )
            sig_results[resolved_labels[i]] = {
                "sharpe_diff_mean": round(float(ds.mean()), 4),
                "ci_95_low": round(ci_low, 4),
                "ci_95_high": round(ci_high, 4),
                "significant": bool(ci_low > 0 or ci_high < 0),
            }
        comparison["significance_bootstrap"] = sig_results

    elif significance_test == "paired_t" and len(all_returns) >= 2:
        from scipy.stats import ttest_rel

        sig_results = {}
        bench_ret = all_returns[bench_idx].values
        for i, ret_s in enumerate(all_returns):
            if i == bench_idx:
                continue
            ret_vals = ret_s.values
            min_len = min(len(bench_ret), len(ret_vals))
            if min_len < 10:
                sig_results[resolved_labels[i]] = {"error": "too few observations"}
                continue
            stat, pval = ttest_rel(bench_ret[:min_len], ret_vals[:min_len])
            sig_results[resolved_labels[i]] = {
                "t_statistic": round(float(stat), 4),
                "p_value": round(float(pval), 4),
                "significant": bool(pval < 0.05),
            }
        comparison["significance_paired_t"] = sig_results

    # Save
    out_name = "comparison_results.json"
    out_path = os.path.join(_workspace_dir(), out_name)
    with open(out_path, "w") as f:
        json.dump(comparison, f, indent=2)

    # Format text output
    lines = [f"Comparison of {len(data_paths)} backtests (saved to {out_name}):"]
    lines.append(f"Benchmark: {resolved_labels[bench_idx]}\n")
    header = ["Metric"] + resolved_labels
    lines.append("  ".join(f"{h:>18}" for h in header))
    lines.append("-" * (18 * len(header) + 2 * (len(header) - 1)))
    metric_keys = list(all_metrics[0].keys())
    for k in metric_keys:
        row = [f"{k:>18}"]
        for m in all_metrics:
            v = m.get(k, "N/A")
            if isinstance(v, float):
                row.append(f"{v:>18.4f}")
            else:
                row.append(f"{str(v):>18}")
        lines.append("  ".join(row))

    return "\n".join(lines)


def align_timeseries(
    data_paths: list,
    time_column: str = "auto",
    method: str = "inner",
    resample: Optional[str] = None,
    fill_limit: Optional[int] = None,
    column_prefix: str = "auto",
    custom_prefixes: Optional[list] = None,
    output_name: Optional[str] = None,
) -> str:
    """Align and merge multiple time-series CSVs on a common time axis.

    Loads N CSV files, detects or uses the specified time column, aligns
    them on a common time index using the chosen method, and saves the
    merged dataset.

    Args:
        data_paths: List of CSV file paths (2+).
        time_column: Time column name ("auto" = detect from Date/timestamp/datetime).
        method: Alignment method — "inner", "outer_ffill", "outer_bfill",
                "outer_interpolate", or "nearest".
        resample: Resample frequency before alignment (e.g. "1D", "1H", "5T").
                  None = no resampling.
        fill_limit: Max consecutive NaN fills for ffill/bfill. None = unlimited.
        column_prefix: "auto" (use filename), "none", or "custom".
        custom_prefixes: List of prefixes when column_prefix="custom".
        output_name: Output CSV filename. Auto-generated if omitted.
    """
    import pandas as pd

    if not isinstance(data_paths, list) or len(data_paths) < 2:
        return "Error: data_paths must be a list of at least 2 CSV file paths."

    dfs = []
    prefixes = []
    time_cols_found = []

    for i, dp in enumerate(data_paths):
        full = _resolve_path(dp)
        if not full:
            return f"Error: File not found: {dp}"
        df = pd.read_csv(full)

        # Detect time column
        if time_column == "auto":
            tc = _resolve_column_name(
                df,
                [
                    "Date",
                    "date",
                    "timestamp",
                    "Timestamp",
                    "datetime",
                    "Datetime",
                    "time",
                    "Time",
                ],
            )
            if tc is None:
                return f"Error: No time column detected in {dp}. Columns: {list(df.columns)}"
        else:
            tc = time_column if time_column in df.columns else None
            if tc is None:
                return f"Error: Column '{time_column}' not found in {dp}."

        time_cols_found.append(tc)

        # Parse time column
        df[tc] = pd.to_datetime(df[tc], errors="coerce", utc=True)
        df = df.dropna(subset=[tc]).set_index(tc).sort_index()

        # Resample if requested
        if resample:
            # Use last valid observation for resampling
            numeric_cols = df.select_dtypes(include="number").columns
            df = df[numeric_cols].resample(resample).last().dropna(how="all")

        # Apply column prefix
        if column_prefix == "auto":
            pfx = os.path.splitext(os.path.basename(dp))[0]
        elif column_prefix == "custom" and custom_prefixes and i < len(custom_prefixes):
            pfx = custom_prefixes[i]
        else:
            pfx = ""

        if pfx:
            df = df.rename(columns={c: f"{pfx}_{c}" for c in df.columns})
            prefixes.append(pfx)
        else:
            prefixes.append(os.path.splitext(os.path.basename(dp))[0])

        dfs.append(df)

    # Merge based on method
    join_type = "inner" if method == "inner" else "outer"
    merged = dfs[0]
    for df in dfs[1:]:
        merged = merged.join(df, how=join_type)

    # Fill NaNs for outer join methods
    if method == "outer_ffill":
        merged = merged.ffill(limit=fill_limit)
    elif method == "outer_bfill":
        merged = merged.bfill(limit=fill_limit)
    elif method == "outer_interpolate":
        merged = merged.interpolate(method="time", limit=fill_limit)
    elif method == "nearest":
        merged = merged.interpolate(method="nearest", limit=fill_limit)

    # Drop rows that are still all-NaN after filling
    merged = merged.dropna(how="all")

    # Save
    if output_name is None:
        output_name = "aligned_" + "_".join(prefixes[:3]) + ".csv"
    out_path = os.path.join(_workspace_dir(), output_name)
    merged.to_csv(out_path)

    # Quality report
    nan_pct = round(merged.isna().mean().mean() * 100, 2)
    report = {
        "method": method,
        "resample": resample,
        "input_files": len(data_paths),
        "input_rows": [len(df) for df in dfs],
        "output_rows": len(merged),
        "output_columns": len(merged.columns),
        "remaining_nan_pct": nan_pct,
        "time_range": (
            f"{merged.index[0]} to {merged.index[-1]}" if len(merged) > 0 else "empty"
        ),
        "saved_to": output_name,
    }

    return json.dumps(report, indent=2, default=str)


def breakdown_pnl(
    trades_path: str,
    input_format: str = "auto",
    fee_model: str = "percentage",
    taker_fee: float = 0.0004,
    maker_fee: float = 0.0002,
    flat_fee: float = 0.0,
    fee_tiers: Optional[list] = None,
    order_type: str = "taker",
    slippage_model: str = "proportional",
    slippage_bps: float = 1.0,
    fixed_slippage: float = 0.0,
    impact_coefficient: float = 0.1,
    funding_path: Optional[str] = None,
    funding_interval_hours: int = 8,
    initial_capital: float = 10000.0,
) -> str:
    """Decompose backtest PnL into gross, fee, slippage, and funding components.

    Accepts either trade-level data (timestamp/side/price/quantity) or
    return-level data (daily returns series).  Applies configurable cost
    models and produces a layered PnL breakdown.

    Fee models: "percentage" (maker/taker), "flat" (per-trade), "tiered"
    (volume-based tiers), "none".

    Slippage models: "none", "fixed" (constant amount), "proportional"
    (basis points of price), "sqrt_impact" (Almgren-Chriss square-root
    market impact).

    Funding: Optional perpetual futures funding rate from a separate CSV.
    """
    import numpy as np
    import pandas as pd

    full_path = _resolve_path(trades_path)
    if not full_path:
        return f"Error: File not found: {trades_path}"
    df = pd.read_csv(full_path)

    # Auto-detect format
    if input_format == "auto":
        has_side = _resolve_column_name(df, ["side", "Side", "direction"]) is not None
        has_price = (
            _resolve_column_name(df, ["price", "Price", "fill_price"]) is not None
        )
        has_qty = (
            _resolve_column_name(df, ["quantity", "qty", "Quantity", "size", "Size"])
            is not None
        )
        if has_side and has_price and has_qty:
            input_format = "trades"
        else:
            input_format = "returns"

    workspace = _workspace_dir()

    if input_format == "trades":
        # ── Trade-level breakdown ──
        side_col = _resolve_column_name(df, ["side", "Side", "direction"]) or "side"
        price_col = (
            _resolve_column_name(df, ["price", "Price", "fill_price"]) or "price"
        )
        qty_col = (
            _resolve_column_name(df, ["quantity", "qty", "Quantity", "size", "Size"])
            or "quantity"
        )
        otype_col = _resolve_column_name(df, ["order_type", "type"])

        prices = df[price_col].astype(float)
        quantities = df[qty_col].astype(float)
        notionals = prices * quantities
        sides = df[side_col].astype(str).str.lower()

        total_trades = len(df)

        # Gross PnL: sum of signed notional changes
        # For trade-level data, compute PnL from price changes between entries/exits
        signed = np.where(sides.isin(["buy", "long", "1"]), 1, -1)
        gross_pnl = float((signed * notionals).sum())

        # Fee calculation
        if fee_model == "none":
            total_fees = 0.0
        elif fee_model == "flat":
            total_fees = flat_fee * total_trades
        elif fee_model == "tiered" and fee_tiers:
            cumulative_vol = 0.0
            total_fees = 0.0
            for _, row_notional in enumerate(notionals):
                cumulative_vol += float(row_notional)
                # Find applicable tier
                applicable_rate = taker_fee  # default
                for tier in sorted(fee_tiers, key=lambda t: t.get("volume_usd", 0)):
                    if cumulative_vol >= tier.get("volume_usd", 0):
                        applicable_rate = tier.get(
                            order_type + "_fee", tier.get("taker", taker_fee)
                        )
                total_fees += float(row_notional) * applicable_rate
        else:  # percentage
            fee_rate = taker_fee if order_type == "taker" else maker_fee
            if otype_col and otype_col in df.columns:
                per_trade_rate = df[otype_col].apply(
                    lambda t: maker_fee if str(t).lower() == "maker" else taker_fee
                )
                total_fees = float((notionals * per_trade_rate).sum())
            else:
                total_fees = float(notionals.sum() * fee_rate)

        # Slippage calculation
        if slippage_model == "none":
            total_slippage = 0.0
        elif slippage_model == "fixed":
            total_slippage = fixed_slippage * total_trades
        elif slippage_model == "sqrt_impact":
            # Almgren-Chriss: impact = coefficient * sqrt(quantity / ADV)
            adv = quantities.mean()
            total_slippage = float(
                (impact_coefficient * np.sqrt(quantities / max(adv, 1)) * prices).sum()
            )
        else:  # proportional
            total_slippage = float(notionals.sum() * slippage_bps / 10000)

    else:
        # ── Return-level breakdown ──
        ret_col = None
        for cand in [
            "returns",
            "Returns",
            "daily_return",
            "strategy_return",
            "strategy_returns",
            "pnl",
            "PnL",
        ]:
            if cand in df.columns:
                ret_col = cand
                break
        if ret_col is None and "Close" in df.columns:
            df["_returns"] = df["Close"].pct_change()
            ret_col = "_returns"
        if ret_col is None:
            return f"Error: No returns column found. Columns: {list(df.columns)}"

        returns = df[ret_col].dropna().astype(float)
        total_trades = int((returns.diff().fillna(0) != 0).sum())

        equity = initial_capital * (1 + returns).cumprod()
        gross_pnl = float(equity.iloc[-1] - initial_capital)

        # Estimate costs on return-level data
        daily_notional = equity.shift(1).fillna(initial_capital).abs()

        # Fees: applied on position changes (turnover)
        position_changes = returns.diff().abs().fillna(0)
        turnover_notional = float((position_changes * daily_notional).sum())
        fee_rate = taker_fee if order_type == "taker" else maker_fee
        total_fees = turnover_notional * fee_rate if fee_model != "none" else 0.0

        # Slippage
        if slippage_model == "none":
            total_slippage = 0.0
        elif slippage_model == "proportional":
            total_slippage = turnover_notional * slippage_bps / 10000
        else:
            total_slippage = turnover_notional * slippage_bps / 10000  # fallback

    # Funding rate costs
    total_funding = 0.0
    if funding_path:
        funding_full = _resolve_path(funding_path)
        if funding_full:
            fdf = pd.read_csv(funding_full)
            rate_col = _resolve_column_name(
                fdf, ["fundingRate", "funding_rate", "rate", "Rate"]
            )
            if rate_col:
                funding_rates = fdf[rate_col].astype(float)
                # Assume position held throughout, approximate
                periods_per_day = 24 / funding_interval_hours
                avg_rate = float(funding_rates.mean())
                trading_days = total_trades if input_format == "trades" else len(df)
                total_funding = abs(
                    initial_capital * avg_rate * trading_days * periods_per_day
                )

    net_pnl = gross_pnl - total_fees - total_slippage - total_funding

    result = {
        "gross_pnl": round(gross_pnl, 2),
        "fee_cost": round(total_fees, 2),
        "slippage_cost": round(total_slippage, 2),
        "funding_cost": round(total_funding, 2),
        "net_pnl": round(net_pnl, 2),
        "cost_breakdown_pct": {
            "fees_pct_of_gross": (
                round(total_fees / abs(gross_pnl) * 100, 2) if gross_pnl != 0 else 0
            ),
            "slippage_pct_of_gross": (
                round(total_slippage / abs(gross_pnl) * 100, 2) if gross_pnl != 0 else 0
            ),
            "funding_pct_of_gross": (
                round(total_funding / abs(gross_pnl) * 100, 2) if gross_pnl != 0 else 0
            ),
            "total_cost_pct": (
                round(
                    (total_fees + total_slippage + total_funding)
                    / abs(gross_pnl)
                    * 100,
                    2,
                )
                if gross_pnl != 0
                else 0
            ),
        },
        "parameters": {
            "input_format": input_format,
            "fee_model": fee_model,
            "slippage_model": slippage_model,
            "initial_capital": initial_capital,
        },
    }

    out_name = "pnl_breakdown.json"
    out_path = os.path.join(workspace, out_name)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    return (
        f"PnL Breakdown (saved to {out_name}):\n"
        f"  Gross PnL:       ${result['gross_pnl']:,.2f}\n"
        f"  Fee Cost:        ${result['fee_cost']:,.2f} "
        f"({result['cost_breakdown_pct']['fees_pct_of_gross']:.1f}% of gross)\n"
        f"  Slippage Cost:   ${result['slippage_cost']:,.2f} "
        f"({result['cost_breakdown_pct']['slippage_pct_of_gross']:.1f}% of gross)\n"
        f"  Funding Cost:    ${result['funding_cost']:,.2f} "
        f"({result['cost_breakdown_pct']['funding_pct_of_gross']:.1f}% of gross)\n"
        f"  Net PnL:         ${result['net_pnl']:,.2f}\n"
        f"  Total Cost:      "
        f"{result['cost_breakdown_pct']['total_cost_pct']:.1f}% of gross PnL"
    )


def split_walkforward_windows(
    data_path: str,
    time_column: str = "auto",
    scheme: str = "rolling",
    train_size: int = 252,
    test_size: int = 63,
    step_size: Optional[int] = None,
    min_train_size: Optional[int] = None,
    embargo_size: int = 0,
    purge_size: int = 0,
    n_splits: int = 5,
    size_unit: str = "rows",
    save_splits: bool = True,
    output_prefix: str = "fold",
) -> str:
    """Split time-series data into train/test windows for walk-forward validation.

    Supports multiple splitting schemes from simple rolling windows to
    advanced combinatorial purged cross-validation.

    Schemes:
      rolling:       Fixed-size train and test windows, sliding by step_size.
      expanding:     Train window grows from min_train_size; test is fixed.
      purged_kfold:  Time-series K-fold with embargo and purge gaps.
      combinatorial: All C(n_splits, 2) train/test combinations with purging.

    Args:
        data_path: CSV file path.
        time_column: Time column name ("auto" = detect).
        scheme: "rolling", "expanding", "purged_kfold", "combinatorial".
        train_size: Training window size (rows or calendar days).
        test_size: Test window size.
        step_size: Slide step (None = test_size for non-overlapping).
        min_train_size: Minimum train size for expanding scheme.
        embargo_size: Gap rows between train end and test start.
        purge_size: Rows to remove from train end (label leakage prevention).
        n_splits: Number of folds (purged_kfold, combinatorial).
        size_unit: "rows" or "calendar_days".
        save_splits: Save individual fold CSVs to workspace.
        output_prefix: Filename prefix for saved fold CSVs.
    """
    import pandas as pd

    full_path = _resolve_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"
    df = pd.read_csv(full_path)

    # Detect time column
    tc = None
    if time_column == "auto":
        tc = _resolve_column_name(
            df,
            [
                "Date",
                "date",
                "timestamp",
                "Timestamp",
                "datetime",
                "Datetime",
                "time",
                "Time",
            ],
        )
    elif time_column in df.columns:
        tc = time_column

    # Parse dates if time column found and size_unit is calendar_days
    if tc and size_unit == "calendar_days":
        df[tc] = pd.to_datetime(df[tc], errors="coerce", utc=True)
        df = df.dropna(subset=[tc]).sort_values(tc).reset_index(drop=True)

    n = len(df)
    step = step_size if step_size is not None else test_size
    workspace = _workspace_dir()
    folds = []

    if scheme == "rolling":
        window_total = train_size + embargo_size + test_size
        if window_total > n:
            return (
                f"Error: train({train_size}) + embargo({embargo_size}) + "
                f"test({test_size}) = {window_total} > data rows ({n})"
            )
        start = 0
        fold_idx = 0
        while start + window_total <= n:
            train_end = start + train_size - purge_size
            test_start = start + train_size + embargo_size
            test_end = test_start + test_size

            folds.append(
                {
                    "fold": fold_idx,
                    "train_start": start,
                    "train_end": train_end,
                    "test_start": test_start,
                    "test_end": min(test_end, n),
                    "train_rows": train_end - start,
                    "test_rows": min(test_end, n) - test_start,
                }
            )
            fold_idx += 1
            start += step

    elif scheme == "expanding":
        min_train = min_train_size if min_train_size else train_size
        if min_train + embargo_size + test_size > n:
            return (
                f"Error: min_train({min_train}) + embargo({embargo_size}) + "
                f"test({test_size}) = {min_train + embargo_size + test_size} > data rows ({n})"
            )
        test_start_pos = min_train + embargo_size
        fold_idx = 0
        while test_start_pos + test_size <= n:
            train_end = test_start_pos - embargo_size - purge_size
            test_end = test_start_pos + test_size

            folds.append(
                {
                    "fold": fold_idx,
                    "train_start": 0,
                    "train_end": train_end,
                    "test_start": test_start_pos,
                    "test_end": min(test_end, n),
                    "train_rows": train_end,
                    "test_rows": min(test_end, n) - test_start_pos,
                }
            )
            fold_idx += 1
            test_start_pos += step

    elif scheme == "purged_kfold":
        fold_size = n // n_splits
        if fold_size < 10:
            return f"Error: {n} rows / {n_splits} splits = {fold_size} rows per fold (too small)"
        for i in range(n_splits):
            test_start = i * fold_size
            test_end = test_start + fold_size if i < n_splits - 1 else n

            # Train = everything except test + embargo + purge zones
            train_indices = list(
                range(0, max(0, test_start - embargo_size - purge_size))
            )
            train_indices += list(range(min(n, test_end + embargo_size), n))

            folds.append(
                {
                    "fold": i,
                    "train_start": "non-contiguous",
                    "train_end": "non-contiguous",
                    "test_start": test_start,
                    "test_end": test_end,
                    "train_rows": len(train_indices),
                    "test_rows": test_end - test_start,
                    "_train_indices": train_indices,
                }
            )

    elif scheme == "combinatorial":
        from itertools import combinations

        fold_size = n // n_splits
        if fold_size < 10:
            return f"Error: {n} rows / {n_splits} splits = {fold_size} rows per fold (too small)"
        fold_boundaries = [
            (i * fold_size, min((i + 1) * fold_size, n)) for i in range(n_splits)
        ]
        fold_idx = 0
        for test_group in combinations(range(n_splits), 2):
            test_indices = []
            for g in test_group:
                s, e = fold_boundaries[g]
                test_indices.extend(range(s, e))
            train_indices = []
            for g in range(n_splits):
                if g in test_group:
                    continue
                s, e = fold_boundaries[g]
                safe_e = max(s, e - purge_size)
                train_indices.extend(range(s, safe_e))
            # Apply embargo
            if embargo_size > 0:
                test_set = set(test_indices)
                train_indices = [
                    t
                    for t in train_indices
                    if not any(abs(t - ts) <= embargo_size for ts in test_set)
                ]

            folds.append(
                {
                    "fold": fold_idx,
                    "test_groups": list(test_group),
                    "train_rows": len(train_indices),
                    "test_rows": len(test_indices),
                    "_train_indices": train_indices,
                    "_test_indices": test_indices,
                }
            )
            fold_idx += 1
    else:
        return (
            f"Error: Unknown scheme '{scheme}'. "
            f"Supported: rolling, expanding, purged_kfold, combinatorial"
        )

    # Save fold CSVs
    if save_splits and folds:
        for fold_info in folds:
            fi = fold_info["fold"]
            if "_train_indices" in fold_info:
                train_df = df.iloc[fold_info["_train_indices"]]
                if "_test_indices" in fold_info:
                    test_df = df.iloc[fold_info["_test_indices"]]
                else:
                    test_df = df.iloc[fold_info["test_start"] : fold_info["test_end"]]
            else:
                train_df = df.iloc[fold_info["train_start"] : fold_info["train_end"]]
                test_df = df.iloc[fold_info["test_start"] : fold_info["test_end"]]
            train_df.to_csv(
                os.path.join(workspace, f"{output_prefix}_{fi}_train.csv"), index=False
            )
            test_df.to_csv(
                os.path.join(workspace, f"{output_prefix}_{fi}_test.csv"), index=False
            )

    # Clean internal indices from output
    clean_folds = []
    for f_info in folds:
        clean = {k: v for k, v in f_info.items() if not k.startswith("_")}
        # Add date ranges if time column available
        if tc and tc in df.columns:
            if "test_start" in clean and isinstance(clean["test_start"], int):
                clean["test_date_range"] = (
                    f"{df[tc].iloc[clean['test_start']]} to "
                    f"{df[tc].iloc[min(clean['test_end'] - 1, n - 1)]}"
                )
        clean_folds.append(clean)

    # Coverage statistics
    all_test_rows = set()
    for f_info in folds:
        if "_test_indices" in f_info:
            all_test_rows.update(f_info["_test_indices"])
        elif isinstance(f_info.get("test_start"), int):
            all_test_rows.update(range(f_info["test_start"], f_info["test_end"]))
    coverage_pct = round(len(all_test_rows) / n * 100, 1) if n > 0 else 0

    result = {
        "scheme": scheme,
        "total_data_rows": n,
        "total_folds": len(folds),
        "test_coverage_pct": coverage_pct,
        "embargo_size": embargo_size,
        "purge_size": purge_size,
        "folds": clean_folds,
        "saved_files": (
            [
                f"{output_prefix}_{i}_train.csv, {output_prefix}_{i}_test.csv"
                for i in range(len(folds))
            ]
            if save_splits
            else []
        ),
    }

    out_name = "walkforward_splits.json"
    out_path = os.path.join(workspace, out_name)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2, default=str)

    return (
        f"Walk-forward split: {scheme} ({len(folds)} folds)\n"
        f"Data: {n} rows, test coverage: {coverage_pct}%\n"
        f"Embargo: {embargo_size}, Purge: {purge_size}\n"
        f"{'Saved fold CSVs to workspace.' if save_splits else ''}\n"
        f"Metadata saved to {out_name}"
    )


# ---------------------------------------------------------------------------
# note_to_self — agent scratchpad (no side effects, logged for analysis)
# ---------------------------------------------------------------------------


def note_to_self(thought: str = "") -> str:
    """Record agent reasoning. No side effects — purely logged by the proxy."""
    return "Noted."


# Tool registry for the proxy layer
CORE_TOOLS = {
    "note_to_self": {
        "func": note_to_self,
        "description": (
            "Record your reasoning, observations, or intermediate findings "
            "for your own reference. Content is not shown to the student. "
            "Use this to organize your thoughts before responding."
        ),
        "params": {
            "thought": {
                "type": "string",
                "description": "Your note — reasoning, hypothesis, observation, or plan.",
                "required": True,
            },
        },
    },
    "shell_exec": {
        "func": shell_exec,
        "description": "Execute a shell command in the sandbox. Returns stdout and stderr combined. Default timeout: 30 seconds. Non-zero exit codes are appended as '[exit code]: N'.",
        "params": {
            "command": {
                "type": "string",
                "description": "Shell command to execute. Can run Python scripts, e.g. 'python script.py'",
                "required": True,
            },
            "timeout": {
                "type": "integer",
                "description": "Timeout in seconds. Default: 30. Optional.",
                "required": False,
            },
        },
    },
    "file_write": {
        "func": file_write,
        "description": "Write content to a file in the workspace. Creates parent directories automatically. Overwrites existing files.",
        "params": {
            "path": {
                "type": "string",
                "description": "File path relative to workspace, e.g. 'strategy.py' or 'results/output.csv'",
                "required": True,
            },
            "content": {
                "type": "string",
                "description": "Full file content to write",
                "required": True,
            },
        },
    },
    "file_read": {
        "func": file_read,
        "description": "Read a file from workspace, data, docs, or student_code directories. Large CSV files (>50 rows) return a smart preview (header + first 5 + last 5 rows) by default. Use offset/max_lines for specific sections.",
        "params": {
            "path": {
                "type": "string",
                "description": "File path to read. Searches workspace/, data/, docs/, student_code/ directories.",
                "required": True,
            },
            "offset": {
                "type": "integer",
                "description": "Start reading from this line number (0-based). Default: 0.",
                "required": False,
            },
            "max_lines": {
                "type": "integer",
                "description": "Maximum number of lines to return. Default: 0 (auto — smart preview for large CSV, full content otherwise).",
                "required": False,
            },
        },
    },
    "file_list": {
        "func": file_list,
        "description": "List files and directories. Returns names with type indicators (/ for dirs). Default: workspace root.",
        "params": {
            "directory": {
                "type": "string",
                "description": "Directory path to list. Default: workspace root. Use '.' for current workspace.",
                "required": False,
            },
        },
    },
    "fetch_market_data": {
        "func": fetch_market_data,
        "description": "Fetch OHLCV data for a given symbol and date range, save to workspace as CSV, and return a summary with first/last rows",
        "params": {
            "symbol": {
                "type": "string",
                "description": "Stock ticker symbol, e.g. 'AAPL', 'SPY', 'MSFT'",
                "required": True,
            },
            "start": {
                "type": "string",
                "description": "Start date in YYYY-MM-DD format, e.g. '2020-01-01'. Omit for earliest available.",
                "required": False,
            },
            "end": {
                "type": "string",
                "description": "End date in YYYY-MM-DD format. Omit for latest available.",
                "required": False,
            },
        },
    },
    "compute_indicator": {
        "func": compute_indicator,
        "description": "Compute a technical indicator (SMA, EMA, RSI, Bollinger Bands, MACD) on a CSV dataset with a 'Close' column. Adds the indicator as new column(s), saves the enriched dataset to workspace, and returns the last 10 rows.",
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with OHLCV data (must have 'Close' column)",
                "required": True,
            },
            "indicator": {
                "type": "string",
                "description": "Indicator name: SMA, EMA, RSI, BOLLINGER, or MACD",
                "required": True,
            },
            "indicator_params": {
                "type": "object",
                "description": 'Indicator parameters as JSON object, e.g. {"window": 20} for SMA, {"fast": 12, "slow": 26, "signal": 9} for MACD',
                "required": False,
            },
        },
    },
    "run_backtest": {
        "func": run_backtest,
        "description": (
            "Run a complete backtest for a built-in strategy type. Supports: "
            "ma_crossover (dual SMA crossover), rsi_threshold (RSI overbought/"
            "oversold), bollinger_breakout (Bollinger Band breakout or mean "
            "reversion). Returns performance metrics (Sharpe, return, drawdown) "
            "and saves equity curve CSV + metrics JSON to workspace. "
            "Self-contained — no need to write a backtest script."
        ),
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with OHLCV data (must have 'Close' column)",
                "required": True,
            },
            "strategy": {
                "type": "string",
                "description": "Strategy name: 'ma_crossover', 'rsi_threshold', or 'bollinger_breakout'",
                "required": True,
            },
            "strategy_params": {
                "type": "object",
                "description": (
                    "Strategy parameters as JSON object. "
                    'ma_crossover: {"fast_window": 20, "slow_window": 50}. '
                    'rsi_threshold: {"window": 14, "overbought": 70, "oversold": 30}. '
                    'bollinger_breakout: {"window": 20, "std_dev": 2, '
                    '"mode": "breakout" or "mean_reversion"}.'
                ),
                "required": False,
            },
            "start": {
                "type": "string",
                "description": "Start date filter in YYYY-MM-DD format. Optional.",
                "required": False,
            },
            "end": {
                "type": "string",
                "description": "End date filter in YYYY-MM-DD format. Optional.",
                "required": False,
            },
        },
    },
    "compute_statistics": {
        "func": compute_statistics,
        "description": (
            "Run statistical tests or analysis on data. Methods: "
            "ADF (stationarity), CORRELATION (matrix), "
            "COINTEGRATION (Engle-Granger with hedge ratio, spread, half-life), "
            "DESCRIPTIVE (summary stats), MISSING (missing values), "
            "LEAD_LAG (cross-correlation + Granger causality), "
            "ROLLING (rolling-window: correlation, cointegration, beta, adf, volatility)"
        ),
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with numeric data",
                "required": True,
            },
            "method": {
                "type": "string",
                "description": (
                    "Statistical method: ADF, CORRELATION, COINTEGRATION, "
                    "DESCRIPTIVE, MISSING, LEAD_LAG, or ROLLING"
                ),
                "required": True,
            },
            "method_params": {
                "type": "object",
                "description": (
                    "Method parameters as JSON object. "
                    'ADF: {"column": "Close", "maxlag": null, "autolag": "aic"}. '
                    'CORRELATION: {"columns": [...], "method": "spearman"}. '
                    'COINTEGRATION: {"column1": "X", "column2": "Y", "trend": "c", '
                    '"maxlag": null, "autolag": "aic", "save_spread": true} — '
                    "returns hedge_ratio, spread, half_life, z-score. "
                    'LEAD_LAG: {"column1": "X", "column2": "Y", "max_lag": 10, '
                    '"type": "both", "granger_maxlag": 5} — '
                    "cross-correlation + Granger causality. "
                    'ROLLING: {"column1": "X", "column2": "Y", "window": 60, '
                    '"step": 1, "metric": "correlation"} — '
                    "metrics: correlation, cointegration, beta, adf, volatility. "
                    "For rolling volatility: vol_type: realized or parkinson."
                ),
                "required": False,
            },
        },
    },
    "plot_chart": {
        "func": plot_chart,
        "description": "Execute matplotlib Python code and save the resulting chart as PNG. Automatically appends plt.savefig() and plt.close() — just provide the plotting code. Returns the saved image file path.",
        "params": {
            "python_code": {
                "type": "string",
                "description": "Complete matplotlib Python code. Chart is auto-saved (no need to call plt.savefig).",
                "required": True,
            },
        },
    },
    "analyze_backtest_results": {
        "func": analyze_backtest_results,
        "description": "Compute standard performance metrics (Sharpe Ratio, Annual Return, Total Return, Max Drawdown, Win Rate, Volatility, Sortino, Calmar) from a CSV of portfolio/strategy returns. Auto-detects the returns column from common names. Saves structured results as {input_name}_analysis.json in workspace.",
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with returns data. Must have a returns column or a 'Close' price column for automatic return computation.",
                "required": True,
            },
            "returns_column": {
                "type": "string",
                "description": "Name of the returns column. Auto-detects from common names (returns, daily_return, strategy_return, pnl) if omitted. If only 'Close' exists, daily returns are computed automatically.",
                "required": False,
            },
        },
    },
    "evaluate_signal": {
        "func": evaluate_signal,
        "description": "Evaluate the quality of a trading signal against forward returns. Computes Information Coefficient, IC decay, quantile returns, turnover, hit rate, and rough PnL metrics from a CSV with at least 'signal' and 'close' columns. Saves structured results as {input_name}_signal_evaluation.json in workspace.",
        "params": {
            "file_path": {
                "type": "string",
                "description": "Path to a CSV containing at least a 'signal' column and a close-price column.",
                "required": True,
            },
            "forward_periods": {
                "type": "integer",
                "description": "Number of periods ahead for forward returns. Default: 1.",
                "required": False,
            },
            "quantiles": {
                "type": "integer",
                "description": "Number of quantile buckets for quantile analysis. Default: 5.",
                "required": False,
            },
            "decay_lags": {
                "type": "integer",
                "description": "Number of lags for IC decay analysis. Default: 5.",
                "required": False,
            },
        },
    },
    "search_web": {
        "func": search_web,
        "description": "Search the public web using DuckDuckGo. Returns real search result links with titles and snippets. Use for finding API documentation, library references, or technical guides.",
        "params": {
            "query": {
                "type": "string",
                "description": "Search query string, e.g. 'FRED API observations endpoint'",
                "required": True,
            },
            "max_results": {
                "type": "integer",
                "description": "Maximum result count (1-10). Default: 5.",
                "required": False,
            },
        },
    },
    "search_docs": {
        "func": search_docs,
        "description": "Keyword search across reference documentation in /docs/. Splits the query into keywords and returns lines ranked by relevance (number of keyword matches).",
        "params": {
            "query": {
                "type": "string",
                "description": "Search keywords, e.g. 'moving average', 'read_csv parse_dates', 'Sharpe ratio'",
                "required": True,
            },
        },
    },
    "get_environment_info": {
        "func": get_environment_info,
        "description": "Return directory paths, mounted files, installed packages, and truthful session runtime context such as student_code availability or tracked trial budget when present.",
        "params": {},
    },
    "construct_signal": {
        "func": construct_signal,
        "description": (
            "Construct a trading signal and save as CSV ready for evaluate_signal. "
            "Signal types: zscore, momentum, mean_reversion, spread, crossover, "
            "composite, volume_imbalance. Output always has 'signal' + 'close' columns."
        ),
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with price/feature data",
                "required": True,
            },
            "signal_type": {
                "type": "string",
                "description": (
                    "Signal type: zscore, momentum, mean_reversion, spread, "
                    "crossover, composite, or volume_imbalance"
                ),
                "required": True,
            },
            "signal_params": {
                "type": "object",
                "description": (
                    "Signal parameters as JSON object. "
                    'zscore: {"column": "Close", "lookback": 20, "returns_based": false}. '
                    'momentum: {"column": "Close", "lookback": 20, "method": "pct_change", '
                    '"normalize": false, "normalize_window": 60}. '
                    'mean_reversion: {"column": "Close", "lookback": 20, '
                    '"method": "zscore|bollinger|rsi"}. '
                    'spread: {"column1": "X", "column2": "Y", "method": "ols|rolling_ols|log_ratio", '
                    '"lookback": 60, "normalize": true}. '
                    'crossover: {"column": "Close", "fast": 10, "slow": 30, "ma_type": "ema|sma"}. '
                    'composite: {"columns": [...], "weights": [...], '
                    '"combination": "weighted_mean|rank_mean|pca"}. '
                    'volume_imbalance: {"method": "taker_ratio|volume_zscore|trade_intensity|obv", '
                    '"lookback": 20, "normalize": true}.'
                ),
                "required": False,
            },
            "output_name": {
                "type": "string",
                "description": "Output CSV file name. Auto-generated if omitted.",
                "required": False,
            },
            "close_column": {
                "type": "string",
                "description": (
                    "Name of the column to use as close price. Use when "
                    "the CSV has non-standard close column names (e.g. "
                    "'BTC_Close'). If omitted, auto-detects 'close', "
                    "'Close', 'adj_close', 'Adj Close', 'price'."
                ),
                "required": False,
            },
        },
    },
    "engineer_features": {
        "func": engineer_features,
        "description": (
            "Engineer quantitative features from OHLCV+ data. Adds computed "
            "feature columns and saves the enriched dataset. Supports: "
            "vwap_ratio, volume_zscore, taker_imbalance, trade_intensity, "
            "returns_multi, realized_vol, parkinson_vol, log_volume_ratio, "
            "obv, atr, price_acceleration. Gracefully skips features whose "
            "required columns are missing."
        ),
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with OHLCV+ data",
                "required": True,
            },
            "features": {
                "type": "array",
                "description": (
                    "List of feature names to compute: vwap_ratio, volume_zscore, "
                    "taker_imbalance, trade_intensity, returns_multi, realized_vol, "
                    "parkinson_vol, log_volume_ratio, obv, atr, price_acceleration"
                ),
                "required": True,
            },
            "feature_params": {
                "type": "object",
                "description": (
                    "Feature-specific parameters as JSON object. "
                    'E.g. {"volume_zscore_lookback": 20, "atr_lookback": 14, '
                    '"returns_horizons": [1, 5, 10, 20], '
                    '"realized_vol_lookback": 20, "parkinson_vol_lookback": 20, '
                    '"taker_imbalance_lookback": 20, '
                    '"trade_intensity_lookback": 20, '
                    '"price_acceleration_lookback": 5}'
                ),
                "required": False,
            },
            "output_name": {
                "type": "string",
                "description": "Output CSV file name. Auto-generated if omitted.",
                "required": False,
            },
        },
    },
    "compare_backtest_results": {
        "func": compare_backtest_results,
        "description": (
            "Compare performance metrics across multiple backtest result CSVs. "
            "Loads N backtests, computes Sharpe/return/drawdown/etc for each, "
            "and presents side-by-side comparison with optional statistical "
            "significance testing (bootstrap CI or paired t-test)."
        ),
        "params": {
            "data_paths": {
                "type": "array",
                "description": (
                    "List of CSV file paths to compare (at least 2). "
                    "Each must contain a returns column or a Close column."
                ),
                "required": True,
            },
            "labels": {
                "type": "array",
                "description": "Human-readable labels for each backtest. Defaults to filenames.",
                "required": False,
            },
            "returns_column": {
                "type": "string",
                "description": (
                    "Returns column name. Auto-detects from common names "
                    "(returns, daily_return, strategy_return, pnl)."
                ),
                "required": False,
            },
            "metrics": {
                "type": "array",
                "description": (
                    "Subset of metrics to compare (e.g. ['sharpe_ratio', 'max_drawdown']). "
                    "None = all standard metrics."
                ),
                "required": False,
            },
            "significance_test": {
                "type": "string",
                "description": (
                    "Statistical test: 'none' (default), 'bootstrap' "
                    "(bootstrap CI on Sharpe difference), or 'paired_t' "
                    "(paired t-test on daily returns)."
                ),
                "required": False,
            },
            "bootstrap_samples": {
                "type": "integer",
                "description": "Number of bootstrap resamples. Default: 1000.",
                "required": False,
            },
            "benchmark_index": {
                "type": "integer",
                "description": "Index of the benchmark backtest in data_paths (0-based). Default: 0.",
                "required": False,
            },
        },
    },
    "align_timeseries": {
        "func": align_timeseries,
        "description": (
            "Align and merge multiple time-series CSVs on a common time axis. "
            "Methods: inner (intersection), outer_ffill (union + forward fill), "
            "outer_bfill, outer_interpolate, nearest. Supports resampling, "
            "N assets, and auto column-prefixing to avoid name collisions."
        ),
        "params": {
            "data_paths": {
                "type": "array",
                "description": "List of CSV file paths to align (at least 2).",
                "required": True,
            },
            "time_column": {
                "type": "string",
                "description": (
                    "Time column name. 'auto' (default) detects from "
                    "Date/timestamp/datetime/time."
                ),
                "required": False,
            },
            "method": {
                "type": "string",
                "description": (
                    "Alignment method: 'inner' (default, intersection only), "
                    "'outer_ffill' (union + forward fill), 'outer_bfill', "
                    "'outer_interpolate' (linear interpolation), 'nearest'."
                ),
                "required": False,
            },
            "resample": {
                "type": "string",
                "description": (
                    "Resample frequency before alignment (e.g. '1D', '1H', '5T'). "
                    "None = no resampling."
                ),
                "required": False,
            },
            "fill_limit": {
                "type": "integer",
                "description": "Max consecutive NaN fills. None = unlimited.",
                "required": False,
            },
            "column_prefix": {
                "type": "string",
                "description": (
                    "'auto' (default, uses filename), 'none' (no prefix), "
                    "or 'custom' (use custom_prefixes list)."
                ),
                "required": False,
            },
            "custom_prefixes": {
                "type": "array",
                "description": "Custom column prefixes when column_prefix='custom'.",
                "required": False,
            },
            "output_name": {
                "type": "string",
                "description": "Output CSV filename. Auto-generated if omitted.",
                "required": False,
            },
        },
    },
    "breakdown_pnl": {
        "func": breakdown_pnl,
        "description": (
            "Decompose backtest PnL into gross, fee, slippage, and funding "
            "cost components. Accepts trade-level or return-level CSVs. "
            "Fee models: percentage (maker/taker), flat, tiered, none. "
            "Slippage: none, fixed, proportional (bps), sqrt_impact "
            "(Almgren-Chriss). Optional funding rate integration for "
            "perpetual futures."
        ),
        "params": {
            "trades_path": {
                "type": "string",
                "description": (
                    "Path to CSV with trade data (side/price/quantity columns) "
                    "or returns data (returns/daily_return column)."
                ),
                "required": True,
            },
            "input_format": {
                "type": "string",
                "description": (
                    "'auto' (default, detects from columns), 'trades' "
                    "(side/price/quantity), or 'returns' (daily return series)."
                ),
                "required": False,
            },
            "fee_model": {
                "type": "string",
                "description": (
                    "Fee model: 'percentage' (default, uses taker_fee/maker_fee), "
                    "'flat' (fixed per trade), 'tiered' (volume-based), 'none'."
                ),
                "required": False,
            },
            "taker_fee": {
                "type": "number",
                "description": "Taker fee rate (e.g. 0.0004 = 4 bps). Default: 0.0004.",
                "required": False,
            },
            "maker_fee": {
                "type": "number",
                "description": "Maker fee rate (e.g. 0.0002 = 2 bps). Default: 0.0002.",
                "required": False,
            },
            "flat_fee": {
                "type": "number",
                "description": "Fixed fee per trade (flat model). Default: 0.",
                "required": False,
            },
            "fee_tiers": {
                "type": "array",
                "description": (
                    "Volume-based fee tiers for tiered model. List of objects: "
                    '[{"volume_usd": 1000000, "taker": 0.0004, "maker": 0.0002}, ...]'
                ),
                "required": False,
            },
            "order_type": {
                "type": "string",
                "description": "Default order type: 'taker' (default) or 'maker'.",
                "required": False,
            },
            "slippage_model": {
                "type": "string",
                "description": (
                    "Slippage model: 'proportional' (default, bps), 'fixed', "
                    "'sqrt_impact' (Almgren-Chriss), 'none'."
                ),
                "required": False,
            },
            "slippage_bps": {
                "type": "number",
                "description": "Slippage in basis points (proportional model). Default: 1.0.",
                "required": False,
            },
            "fixed_slippage": {
                "type": "number",
                "description": "Fixed slippage amount per trade. Default: 0.",
                "required": False,
            },
            "impact_coefficient": {
                "type": "number",
                "description": "Market impact coefficient (sqrt_impact model). Default: 0.1.",
                "required": False,
            },
            "funding_path": {
                "type": "string",
                "description": (
                    "Path to funding rate CSV (for perpetual futures). "
                    "Must have a fundingRate/funding_rate/rate column."
                ),
                "required": False,
            },
            "funding_interval_hours": {
                "type": "integer",
                "description": "Funding settlement interval in hours. Default: 8.",
                "required": False,
            },
            "initial_capital": {
                "type": "number",
                "description": "Initial capital for return-level PnL calculation. Default: 10000.",
                "required": False,
            },
        },
    },
    "split_walkforward_windows": {
        "func": split_walkforward_windows,
        "description": (
            "Split time-series data into train/test windows for walk-forward "
            "validation. Schemes: rolling (fixed sliding window), expanding "
            "(growing train), purged_kfold (time-series K-fold with embargo), "
            "combinatorial (all C(n,2) combos with purging). Saves fold CSVs "
            "to workspace."
        ),
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with time-series data.",
                "required": True,
            },
            "time_column": {
                "type": "string",
                "description": (
                    "Time column name. 'auto' (default) detects from "
                    "Date/timestamp/datetime."
                ),
                "required": False,
            },
            "scheme": {
                "type": "string",
                "description": (
                    "Splitting scheme: 'rolling' (default), 'expanding', "
                    "'purged_kfold', or 'combinatorial'."
                ),
                "required": False,
            },
            "train_size": {
                "type": "integer",
                "description": "Training window size in rows. Default: 252 (~1 year daily).",
                "required": False,
            },
            "test_size": {
                "type": "integer",
                "description": "Test window size in rows. Default: 63 (~1 quarter daily).",
                "required": False,
            },
            "step_size": {
                "type": "integer",
                "description": "Slide step in rows. Default: test_size (non-overlapping).",
                "required": False,
            },
            "min_train_size": {
                "type": "integer",
                "description": "Minimum train size for expanding scheme. Default: train_size.",
                "required": False,
            },
            "embargo_size": {
                "type": "integer",
                "description": (
                    "Gap rows between train and test to prevent label leakage. "
                    "Default: 0."
                ),
                "required": False,
            },
            "purge_size": {
                "type": "integer",
                "description": (
                    "Rows to remove from train end (forward-looking label "
                    "leakage prevention). Default: 0."
                ),
                "required": False,
            },
            "n_splits": {
                "type": "integer",
                "description": "Number of folds for purged_kfold/combinatorial. Default: 5.",
                "required": False,
            },
            "size_unit": {
                "type": "string",
                "description": "'rows' (default) or 'calendar_days'.",
                "required": False,
            },
            "save_splits": {
                "type": "boolean",
                "description": "Save individual fold train/test CSVs. Default: true.",
                "required": False,
            },
            "output_prefix": {
                "type": "string",
                "description": "Filename prefix for fold CSVs. Default: 'fold'.",
                "required": False,
            },
        },
    },
}


# ── Trial management tools (I-series LEAN tasks) ──────────────

# Lazy singleton accessor keyed by workspace path
_trial_managers: dict[str, "TrialManager"] = {}


def _get_trial_manager() -> "TrialManager":
    """Return a TrialManager for the current workspace (lazy init)."""
    try:
        from server.core.tools.trial_manager import TrialManager
    except ImportError:
        from trial_manager import TrialManager  # inside container (/opt/bench/)

    workspace = _workspace_dir()
    if workspace not in _trial_managers:
        max_trials = int(os.environ.get("QTB_MAX_BACKTEST_TRIALS", "5"))
        _trial_managers[workspace] = TrialManager(workspace, max_trials=max_trials)
    return _trial_managers[workspace]


def run_lean_backtest(
    algorithm_path: str,
    params_json: str = "",
    run_id: str = "",
    class_name: str = "",
    full_type_name: str = "",
) -> str:
    """Compile and run a LEAN backtest, automatically recording the result as a trial.

    Budget is enforced at the Python level before executing the shell
    script.  Returns structured status with metrics and remaining budget.
    """
    tm = _get_trial_manager()

    # Budget check — refuse before consuming any compute
    if not tm.can_run():
        status = tm.get_status()
        return (
            f"Error: Backtest budget exhausted "
            f"({status['trials_used']}/{status['max_trials']} used). "
            f"No more trials available.\n"
            f"Use select_submission(trial_id) to pick your best trial, "
            f"or get_trial_status() to review all results."
        )

    requested_path = algorithm_path
    resolved_algorithm_path = _resolve_path(algorithm_path)
    if not resolved_algorithm_path or not os.path.isfile(resolved_algorithm_path):
        return f"Error: Algorithm file not found: {requested_path}"

    inferred_entrypoint = _infer_csharp_entrypoint(resolved_algorithm_path)
    if not full_type_name and inferred_entrypoint.get("full_type_name"):
        full_type_name = inferred_entrypoint["full_type_name"]
    if not class_name and inferred_entrypoint.get("class_name"):
        class_name = inferred_entrypoint["class_name"]

    # Build the run_backtest command
    cmd_parts = ["run_backtest", shlex.quote(resolved_algorithm_path)]
    if params_json:
        cmd_parts.extend(["--params", shlex.quote(params_json)])
    if run_id:
        cmd_parts.extend(["--run-id", shlex.quote(run_id)])
    if full_type_name:
        cmd_parts.extend(["--full-type-name", shlex.quote(full_type_name)])
    elif class_name:
        cmd_parts.extend(["--class-name", shlex.quote(class_name)])
    cmd = " ".join(cmd_parts)

    # Execute via shell_exec (reuses existing bash script inside container)
    output = shell_exec(cmd, timeout=600)

    # Determine exit code from output
    import re as _re

    _exit_match = _re.search(r"\[exit code\]: (\d+)", output)
    _exit_code = int(_exit_match.group(1)) if _exit_match else 0

    # Exit code 5 = budget exhausted (script refused to run)
    if _exit_code == 5:
        status = tm.get_status()
        return (
            f"Backtest budget exhausted ({status['trials_used']}/{status['max_trials']} used). "
            f"Use select_submission to pick a trial, or get_trial_status to review results.\n\n"
            f"--- Script Output ---\n{output}"
        )

    # Determine status from results.
    # When --run-id is used, run_backtest.sh writes results to results/<run_id>/.
    workspace = _workspace_dir()
    results_subdir = os.path.join("results", run_id) if run_id else "results"
    results_dir = os.path.join(workspace, results_subdir)
    summary_path = os.path.join(results_dir, "summary.json")

    # Fallback: LEAN may name it {AlgoName}-summary.json instead of summary.json
    if not os.path.exists(summary_path) and os.path.isdir(results_dir):
        for f in os.listdir(results_dir):
            if f.endswith("-summary.json"):
                summary_path = os.path.join(results_dir, f)
                break

    if _exit_code == 2 or (
        "error" in output.lower() and "build failed" in output.lower()
    ):
        status = "compile_error"
    elif os.path.exists(summary_path):
        # Check results first — LEAN may complete with trades even if the
        # wrapper script reports a non-zero exit (timeout race, extraction issue).
        trade_count = 0
        order_count = 0
        try:
            with open(summary_path) as f:
                sdata = json.load(f)
            perf = sdata.get("totalPerformance", {}).get("tradeStatistics", {})
            trade_count = perf.get("totalNumberOfTrades", 0)
            stats = sdata.get("statistics", {})
            order_count = int(stats.get("Total Orders", "0"))
        except (json.JSONDecodeError, IOError, ValueError):
            pass
        # Fallback: when statistics dict is empty (multi-symbol CryptoFuture),
        # count filled orders directly from orders.json.
        if order_count == 0:
            orders_path = os.path.join(results_dir, "orders.json")
            if os.path.exists(orders_path):
                try:
                    with open(orders_path) as f:
                        odata = json.load(f)
                    if isinstance(odata, list):
                        order_count = sum(
                            1
                            for o in odata
                            if isinstance(o, dict) and o.get("status") == "filled"
                        )
                except Exception:
                    pass
        if trade_count == 0 and order_count > 0:
            trade_count = order_count // 2
        # Success if closed trades exist OR orders were filled (TradeBuilder
        # may report 0 closed trades for multi-symbol CryptoFuture strategies)
        status = "success" if (trade_count > 0 or order_count > 0) else "empty_trades"
    elif _exit_code in (3, 4, 124):
        status = "runtime_error"
    else:
        status = "runtime_error"

    # Allocate a trial_id (increments the counter atomically)
    trial_id = tm.next_trial_id()

    # Record structured metadata (snapshot + manifest entry)
    meta = tm.snapshot_and_record(
        trial_id,
        status,
        algo_path=requested_path,
        source_results_dir=results_dir,
    )
    metrics = meta.get("metrics", {})
    remaining = tm.max_trials - tm.trials_used()
    compile_errors = (
        _extract_compile_errors(output) if status == "compile_error" else []
    )

    # Build structured response
    parts = [
        f"=== Trial {trial_id} Result ===",
        f"Status: {status}",
    ]
    if metrics:
        parts.append(f"Trades: {metrics.get('total_trades', 'N/A')}")
        parts.append(f"Sharpe: {metrics.get('sharpe_ratio', 'N/A')}")
        parts.append(f"Return: {metrics.get('total_return_pct', 'N/A')}%")
    parts.append(f"Source file: {resolved_algorithm_path}")
    if full_type_name:
        parts.append(f"Entrypoint: {full_type_name}")
    elif class_name:
        parts.append(f"Entrypoint class: {class_name}")
    parts.append(f"Remaining trials: {remaining}/{tm.max_trials}")
    parts.append("")
    parts.append("--- Backtest Output ---")
    # Truncate very long output to keep response manageable
    if len(output) > 4000:
        parts.append(output[:2000] + "\n...(truncated)...\n" + output[-1500:])
    else:
        parts.append(output)
    if compile_errors:
        parts.append("")
        parts.append("--- Key Compile Errors ---")
        parts.extend(compile_errors)

    return "\n".join(parts)


def submit_trial(notes: str = "") -> str:
    """Snapshot the current workspace state as a trial (no backtest run).

    Use this for I10 grid search or manual checkpointing. Does NOT consume
    a backtest budget slot (no compute used), but the snapshot is available
    for selection via select_submission / auto_select.
    """
    tm = _get_trial_manager()

    # Allocate a trial_id that doesn't conflict with JSONL-tracked runs.
    # Use max(existing manifest IDs, JSONL count) + 1.
    manifest_ids = [int(k) for k in tm.get_status()["trials"].keys()] or [0]
    trial_id = max(max(manifest_ids), tm.trials_used()) + 1

    # Determine status from current results
    workspace = _workspace_dir()
    summary_path = os.path.join(workspace, "results", "summary.json")
    trades_path = os.path.join(workspace, "results", "trades.json")

    if os.path.exists(summary_path):
        trade_count = 0
        if os.path.exists(trades_path):
            try:
                with open(trades_path) as f:
                    tdata = json.load(f)
                if isinstance(tdata, list):
                    trade_count = len(tdata)
                else:
                    trade_count = len(
                        tdata.get("trades", tdata.get("ClosedTrades", []))
                    )
            except (json.JSONDecodeError, IOError):
                pass
        status = "success" if trade_count > 0 else "empty_trades"
    else:
        status = "no_results"

    meta = tm.snapshot_and_record(trial_id, status)
    metrics = meta.get("metrics", {})
    remaining = tm.max_trials - tm.trials_used()

    parts = [
        f"=== Trial {trial_id} Submitted (checkpoint) ===",
        f"Status: {status}",
    ]
    if notes:
        parts.append(f"Notes: {notes}")
    if metrics:
        parts.append(f"Trades: {metrics.get('total_trades', 'N/A')}")
        parts.append(f"Sharpe: {metrics.get('sharpe_ratio', 'N/A')}")
        parts.append(f"Return: {metrics.get('total_return_pct', 'N/A')}%")
    parts.append(f"Backtest budget: {remaining}/{tm.max_trials} remaining")

    return "\n".join(parts)


def select_submission(trial_id: int) -> str:
    """Select which trial to use for final evaluation.

    Copies the selected trial's results and code back to /workspace/ so
    existing evaluation scripts read the chosen version.
    """
    tm = _get_trial_manager()
    result = tm.select(int(trial_id))

    if result.startswith("Error"):
        return result

    # Return confirmation with metrics
    status = tm.get_status()
    trial_meta = status["trials"].get(str(trial_id), {})
    metrics = trial_meta.get("metrics", {})

    parts = [result]
    if metrics:
        parts.append(f"Trades: {metrics.get('total_trades', 'N/A')}")
        parts.append(f"Sharpe: {metrics.get('sharpe_ratio', 'N/A')}")
        parts.append(f"Return: {metrics.get('total_return_pct', 'N/A')}%")

    return "\n".join(parts)


def get_trial_status() -> str:
    """View all trials, their metrics, remaining budget, and current selection."""
    tm = _get_trial_manager()
    status = tm.get_status()

    parts = [
        "=== Trial Status ===",
        f"Budget: {status['trials_used']}/{status['max_trials']} used, "
        f"{status['trials_remaining']} remaining",
    ]

    if status["selected_trial"]:
        parts.append(f"Selected for evaluation: Trial {status['selected_trial']}")
    else:
        parts.append("No trial selected yet (auto-select will pick best on evaluation)")

    if status["trials"]:
        parts.append("")
        for tid_str in sorted(status["trials"], key=int):
            meta = status["trials"][tid_str]
            metrics = meta.get("metrics", {})
            line = f"  Trial {tid_str}: {meta.get('status', 'unknown')}"
            if metrics.get("total_trades"):
                line += f" | {metrics['total_trades']} trades"
            if metrics.get("sharpe_ratio") is not None:
                line += f" | Sharpe={metrics['sharpe_ratio']}"
            if metrics.get("total_return_pct") is not None:
                line += f" | Return={metrics['total_return_pct']}%"
            if status["selected_trial"] == int(tid_str):
                line += "  <-- SELECTED"
            parts.append(line)
    else:
        parts.append("\nNo trials recorded yet.")

    return "\n".join(parts)


# Register trial tools in CORE_TOOLS
CORE_TOOLS["run_lean_backtest"] = {
    "func": run_lean_backtest,
    "description": (
        "Compile and run a LEAN C# backtest, automatically recording the result as a trial. "
        "Each call uses one trial from the budget (default 5). Returns trial status, "
        "trade count, Sharpe ratio, and remaining budget. Use this instead of "
        "shell_exec('run_backtest ...') for tracked iteration. The source file may live "
        "in the workspace or in a mounted student_code directory."
    ),
    "params": {
        "algorithm_path": {
            "type": "string",
            "description": "Path to the .cs algorithm file relative to the workspace or a mounted task-provided code directory, e.g. 'Algorithm.cs' or 'student_code/student_code.cs'.",
            "required": True,
        },
        "params_json": {
            "type": "string",
            "description": 'JSON string of algorithm parameters, e.g. \'{"fast": 10, "slow": 30}\'. Optional.',
            "required": False,
        },
        "run_id": {
            "type": "string",
            "description": "Run identifier for multi-run tasks. Optional.",
            "required": False,
        },
        "class_name": {
            "type": "string",
            "description": "Optional C# class name override for the QCAlgorithm entrypoint. If omitted, the tool will try to infer it from the source.",
            "required": False,
        },
        "full_type_name": {
            "type": "string",
            "description": "Optional fully-qualified C# type name override, e.g. 'MyNamespace.MyStrategy'. Takes precedence over class_name.",
            "required": False,
        },
    },
}

CORE_TOOLS["submit_trial"] = {
    "func": submit_trial,
    "description": (
        "Snapshot the current workspace state as a trial without running a backtest. "
        "Use for grid search checkpointing or manual saves. Each call uses one trial "
        "from the budget."
    ),
    "params": {
        "notes": {
            "type": "string",
            "description": "Optional notes describing this trial, e.g. 'grid search complete, 180 combos'",
            "required": False,
        },
    },
}

CORE_TOOLS["select_submission"] = {
    "func": select_submission,
    "description": (
        "Select which trial to submit for final evaluation. Copies the selected "
        "trial's results and code back to /workspace/ for scoring. Call get_trial_status "
        "first to review all trials."
    ),
    "params": {
        "trial_id": {
            "type": "integer",
            "description": "Trial number to select (1-based), e.g. 3",
            "required": True,
        },
    },
}

CORE_TOOLS["get_trial_status"] = {
    "func": get_trial_status,
    "description": (
        "View all recorded trials with their status, metrics (trades, Sharpe, return), "
        "remaining trial budget, and which trial is currently selected for evaluation."
    ),
    "params": {},
}


# ---------------------------------------------------------------------------
# analyze_lean_results — structured LEAN result analysis
# ---------------------------------------------------------------------------


def analyze_lean_results(
    results_path: str = "",
    trial_id: int = 0,
    sections: str = "summary",
) -> str:
    """Analyze LEAN backtest results and return structured metrics.

    Provides pre-built analysis of backtest output files so the agent
    does not need to write Python parsing scripts via shell_exec.
    Handles the quirks of LEAN's output format (empty statistics dict,
    UNIX timestamps in orders, lowercase status values, etc.).
    """
    import json as _json
    from collections import Counter as _Counter

    workspace = _workspace_dir()

    # Resolve results directory
    rdir = ""
    if trial_id > 0:
        trial_dir = os.path.join(workspace, ".trials", f"trial_{trial_id}", "results")
        if os.path.isdir(trial_dir):
            rdir = trial_dir
    if not rdir and results_path:
        candidate = results_path
        if not os.path.isabs(candidate):
            candidate = os.path.join(workspace, candidate)
        if os.path.isdir(candidate):
            rdir = candidate
    if not rdir:
        # Auto-detect: latest trial or workspace/results
        try:
            manifest_path = os.path.join(workspace, ".trials", "manifest.json")
            if os.path.exists(manifest_path):
                with open(manifest_path) as f:
                    manifest = _json.load(f)
                best_tid = None
                for tid_str, meta in manifest.get("trials", {}).items():
                    if meta.get("status") == "success":
                        best_tid = int(tid_str)
                if best_tid:
                    candidate = os.path.join(
                        workspace, ".trials", f"trial_{best_tid}", "results"
                    )
                    if os.path.isdir(candidate):
                        rdir = candidate
        except Exception:
            pass
        if not rdir:
            # Fallback: workspace/results
            candidate = os.path.join(workspace, "results")
            if os.path.isdir(candidate):
                rdir = candidate
    if not rdir:
        return "Error: No results directory found. Run a backtest first."

    requested = set(s.strip().lower() for s in sections.split(","))
    if "all" in requested:
        requested = {"summary", "orders", "trades", "symbols"}

    parts = [f"Results directory: {rdir}"]

    # ── Load summary.json ──
    summary_data = None
    for fn in os.listdir(rdir):
        if fn.endswith("-summary.json") or fn == "summary.json":
            try:
                with open(os.path.join(rdir, fn)) as f:
                    summary_data = _json.load(f)
                break
            except Exception:
                pass

    # ── SUMMARY section ──
    if "summary" in requested:
        parts.append("\n=== Backtest Summary ===")
        if summary_data:
            rt = summary_data.get("runtimeStatistics", {})
            stats = summary_data.get("statistics", {})
            ts = summary_data.get("totalPerformance", {}).get("tradeStatistics", {})
            ps = summary_data.get("totalPerformance", {}).get("portfolioStatistics", {})

            # Prefer statistics dict, fall back to runtimeStatistics
            ret = stats.get("Net Profit", rt.get("Return", "N/A"))
            sharpe = stats.get("Sharpe Ratio", ps.get("sharpeRatio", "N/A"))
            sortino = stats.get("Sortino Ratio", ps.get("sortinoRatio", "N/A"))
            cagr = stats.get(
                "Compounding Annual Return", ps.get("compoundingAnnualReturn", "N/A")
            )
            drawdown = stats.get("Drawdown", ps.get("drawdown", "N/A"))
            start_eq = stats.get("Start Equity", "N/A")
            end_eq = stats.get("End Equity", rt.get("Equity", "N/A"))
            fees = stats.get("Total Fees", rt.get("Fees", "N/A"))
            total_orders = stats.get("Total Orders", "N/A")

            # Trades: prefer tradeStatistics, fall back to orders.json count
            trades = ts.get("totalNumberOfTrades", 0)
            win_rate = ts.get("winRate", "N/A")
            avg_win = stats.get("Average Win", "N/A")
            avg_loss = stats.get("Average Loss", "N/A")

            # If trades=0 but orders exist, estimate from orders.json
            if trades == 0:
                orders_path = os.path.join(rdir, "orders.json")
                if os.path.exists(orders_path):
                    try:
                        with open(orders_path) as f:
                            odata = _json.load(f)
                        if isinstance(odata, list):
                            filled = sum(
                                1 for o in odata if o.get("status") == "filled"
                            )
                            trades = filled // 2
                            total_orders = filled
                    except Exception:
                        pass

            parts.append(
                f"  Return: {ret} | CAGR: {cagr} | Sharpe: {sharpe} | Sortino: {sortino}"
            )
            parts.append(
                f"  Max Drawdown: {drawdown} | Start Equity: {start_eq} | End Equity: {end_eq}"
            )
            parts.append(f"  Trades: {trades} | Orders: {total_orders} | Fees: {fees}")
            parts.append(
                f"  Win Rate: {win_rate} | Avg Win: {avg_win} | Avg Loss: {avg_loss}"
            )
        else:
            parts.append("  (summary.json not found)")

    # ── ORDERS section ──
    if "orders" in requested:
        parts.append("\n=== Order Analysis ===")
        orders_path = os.path.join(rdir, "orders.json")
        if os.path.exists(orders_path):
            try:
                with open(orders_path) as f:
                    orders = _json.load(f)
                if isinstance(orders, list) and orders:
                    filled = [o for o in orders if o.get("status") == "filled"]
                    buys = sum(1 for o in filled if o.get("direction") == "buy")
                    sells = sum(1 for o in filled if o.get("direction") == "sell")
                    symbols = _Counter(o.get("symbolValue", "") for o in filled)
                    parts.append(
                        f"  Total filled: {len(filled)} | Buy: {buys} | Sell: {sells}"
                    )
                    parts.append(f"  Unique symbols traded: {len(symbols)}")
                    top5 = symbols.most_common(5)
                    parts.append(
                        f"  Top 5 by order count: {', '.join(f'{s}({c})' for s, c in top5)}"
                    )
                    # Time range
                    times = [o.get("time", 0) for o in filled if o.get("time")]
                    if times:
                        import datetime

                        first = datetime.datetime.utcfromtimestamp(min(times)).strftime(
                            "%Y-%m-%d"
                        )
                        last = datetime.datetime.utcfromtimestamp(max(times)).strftime(
                            "%Y-%m-%d"
                        )
                        parts.append(f"  First fill: {first} | Last fill: {last}")
                else:
                    parts.append("  (no order events)")
            except Exception as e:
                parts.append(f"  Error reading orders.json: {e}")
        else:
            parts.append("  (orders.json not found)")

    # ── TRADES section ──
    if "trades" in requested:
        parts.append("\n=== Trade Analysis ===")
        if summary_data:
            ts = summary_data.get("totalPerformance", {}).get("tradeStatistics", {})
            total = ts.get("totalNumberOfTrades", 0)
            if total > 0:
                wins = ts.get("numberOfWinningTrades", 0)
                losses = ts.get("numberOfLosingTrades", 0)
                parts.append(
                    f"  Closed trades: {total} | Winners: {wins} ({ts.get('winRate', 'N/A')}) | Losers: {losses}"
                )
                parts.append(
                    f"  Avg profit: {ts.get('averageProfit', 'N/A')} | Avg loss: {ts.get('averageLoss', 'N/A')}"
                )
                parts.append(
                    f"  Largest win: {ts.get('largestProfit', 'N/A')} | Largest loss: {ts.get('largestLoss', 'N/A')}"
                )
                parts.append(f"  Profit factor: {ts.get('profitFactor', 'N/A')}")
                parts.append(
                    f"  Max consecutive wins: {ts.get('maxConsecutiveWinningTrades', 'N/A')} | losses: {ts.get('maxConsecutiveLosingTrades', 'N/A')}"
                )
            else:
                parts.append(
                    "  (LEAN TradeBuilder reported 0 trades — use 'orders' section for fill-level data)"
                )
        else:
            parts.append("  (summary.json not found)")

    # ── SYMBOLS section ──
    if "symbols" in requested:
        parts.append("\n=== Symbol P&L Breakdown ===")
        orders_path = os.path.join(rdir, "orders.json")
        if os.path.exists(orders_path):
            try:
                with open(orders_path) as f:
                    orders = _json.load(f)
                if isinstance(orders, list):
                    filled = [o for o in orders if o.get("status") == "filled"]
                    # Compute approximate P&L per symbol from fills
                    sym_fills = {}
                    for o in filled:
                        sym = o.get("symbolValue", "")
                        if sym not in sym_fills:
                            sym_fills[sym] = {"buys": 0, "sells": 0, "volume": 0.0}
                        price = float(o.get("fillPrice", 0) or 0)
                        qty = abs(float(o.get("fillQuantity", 0) or 0))
                        if o.get("direction") == "buy":
                            sym_fills[sym]["buys"] += 1
                        else:
                            sym_fills[sym]["sells"] += 1
                        sym_fills[sym]["volume"] += price * qty

                    parts.append(f"  Total symbols with fills: {len(sym_fills)}")
                    # Sort by fill count
                    by_fills = sorted(
                        sym_fills.items(),
                        key=lambda x: x[1]["buys"] + x[1]["sells"],
                        reverse=True,
                    )
                    parts.append("  Top 10 by fill count:")
                    for sym, data in by_fills[:10]:
                        total_fills = data["buys"] + data["sells"]
                        parts.append(
                            f"    {sym}: {total_fills} fills (buy={data['buys']}, sell={data['sells']}, volume=₮{data['volume']:,.0f})"
                        )
                else:
                    parts.append("  (no order data)")
            except Exception as e:
                parts.append(f"  Error: {e}")
        else:
            parts.append("  (orders.json not found)")

    return "\n".join(parts)


CORE_TOOLS["analyze_lean_results"] = {
    "func": analyze_lean_results,
    "description": (
        "Analyze LEAN backtest results and return structured metrics. "
        "Use this instead of writing Python scripts to parse summary.json or orders.json. "
        "Sections: 'summary' (metrics), 'orders' (fill analysis), 'trades' (win/loss stats), "
        "'symbols' (per-symbol breakdown), or 'all'. "
        "Specify trial_id to analyze a specific trial, or leave blank for latest successful trial."
    ),
    "params": {
        "results_path": {
            "type": "string",
            "description": "Path to results directory (relative to workspace). If empty, auto-detects latest trial.",
            "required": False,
        },
        "trial_id": {
            "type": "integer",
            "description": "Trial number to analyze (e.g. 3). Overrides results_path. 0 = auto-detect latest successful trial.",
            "required": False,
        },
        "sections": {
            "type": "string",
            "description": "Comma-separated sections: summary, orders, trades, symbols, or all. Default: summary.",
            "required": False,
        },
    },
}
