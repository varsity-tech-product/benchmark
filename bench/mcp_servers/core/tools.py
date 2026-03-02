"""Core MCP tool implementations for QuantTutorBench.

These functions implement the 14 core tools available to the Agent Under Test.
They are designed to run inside the Docker sandbox (or locally for development).

Directory paths are read lazily from environment variables so that the
orchestrator can update them per-task (e.g. to point at staged/filtered dirs).
"""

import glob as glob_module
import json
import os
import subprocess
from typing import Optional

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


def shell_exec(command: str, timeout: int = 30) -> str:
    """Execute a shell command in the sandbox."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=_workspace_dir(),
        )
        output = ""
        if result.stdout:
            output += result.stdout
        if result.stderr:
            output += f"\n[stderr]: {result.stderr}"
        if result.returncode != 0:
            output += f"\n[exit code]: {result.returncode}"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"Error: Command timed out after {timeout}s"


def file_write(path: str, content: str) -> str:
    """Write content to a file in the workspace."""
    workspace = _workspace_dir()
    full_path = os.path.join(workspace, path) if not path.startswith("/") else path
    if not full_path.startswith(workspace):
        return f"Error: Can only write to {workspace}"
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    with open(full_path, "w") as f:
        f.write(content)
    return f"Written {len(content)} bytes to {path}"


def _resolve_path(path: str) -> Optional[str]:
    """Resolve a relative path across all search directories.

    Handles the common case where the caller includes a known directory
    prefix (e.g. ``student_code/foo.py``) — we strip the prefix so that
    the search against the matching base directory doesn't double up.
    """
    bases = [_workspace_dir(), _data_dir(), _docs_dir(), _student_code_dir()]
    # Known directory name prefixes that callers might include
    _KNOWN_PREFIXES = ("workspace/", "data/", "docs/", "student_code/")

    # 1. Direct search
    for base in bases:
        full = os.path.join(base, path) if not path.startswith("/") else path
        if os.path.isfile(full) or os.path.isdir(full):
            return full

    # 2. Strip known directory prefix and retry
    for prefix in _KNOWN_PREFIXES:
        if path.startswith(prefix):
            stripped = path[len(prefix) :]
            for base in bases:
                full = os.path.join(base, stripped)
                if os.path.isfile(full) or os.path.isdir(full):
                    return full
            break  # Only one prefix can match

    return None


def file_read(path: str) -> str:
    """Read a file from workspace, data, docs, or student_code."""
    resolved = _resolve_path(path)
    if resolved and os.path.isfile(resolved):
        with open(resolved, "r") as f:
            content = f.read()
        if len(content) > 50000:
            return content[:50000] + f"\n... (truncated, {len(content)} total bytes)"
        return content
    return f"Error: File not found: {path}"


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
    data_path: str, indicator: str, params: Optional[dict] = None
) -> str:
    """Compute a technical indicator on a dataset."""
    import pandas as pd

    params = params or {}
    full_path = _resolve_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"
    df = pd.read_csv(
        full_path,
        parse_dates=["Date"] if "Date" in open(full_path).readline() else None,
    )

    indicator = indicator.upper()
    if indicator == "SMA":
        window = params.get("window", 20)
        df[f"SMA_{window}"] = df["Close"].rolling(window).mean()
    elif indicator == "EMA":
        span = params.get("span", 20)
        df[f"EMA_{span}"] = df["Close"].ewm(span=span).mean()
    elif indicator == "RSI":
        window = params.get("window", 14)
        delta = df["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(window).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window).mean()
        rs = gain / loss
        df["RSI"] = 100 - (100 / (1 + rs))
    elif indicator == "BOLLINGER":
        window = params.get("window", 20)
        std_dev = params.get("std_dev", 2)
        sma = df["Close"].rolling(window).mean()
        std = df["Close"].rolling(window).std()
        df["BB_Upper"] = sma + std_dev * std
        df["BB_Middle"] = sma
        df["BB_Lower"] = sma - std_dev * std
    elif indicator == "MACD":
        fast = params.get("fast", 12)
        slow = params.get("slow", 26)
        signal = params.get("signal", 9)
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
    params: Optional[dict] = None,
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

    params = params or {}
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
        fast_window = params.get("fast_window", 20)
        slow_window = params.get("slow_window", 50)
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
        window = params.get("window", 14)
        overbought = params.get("overbought", 70)
        oversold = params.get("oversold", 30)

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
        window = params.get("window", 20)
        std_dev = params.get("std_dev", 2)
        mode = params.get("mode", "breakout")

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
    results_path = os.path.join(workspace, "backtest_results.csv")
    results_df.to_csv(results_path, index=False)

    metrics_path = os.path.join(workspace, "backtest_metrics.json")
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
        f"  backtest_results.csv (equity curve with signals)\n"
        f"  backtest_metrics.json (all metrics)"
    )


def compute_statistics(
    data_path: str, method: str, params: Optional[dict] = None
) -> str:
    """Run statistical tests on data."""
    import numpy as np
    import pandas as pd

    params = params or {}
    full_path = _resolve_path(data_path)
    if not full_path:
        return f"Error: File not found: {data_path}"
    df = pd.read_csv(full_path)

    method = method.upper()
    if method == "ADF":
        from statsmodels.tsa.stattools import adfuller

        col = params.get("column", "Close")
        result = adfuller(df[col].dropna())
        return json.dumps(
            {
                "test_statistic": round(result[0], 4),
                "p_value": round(result[1], 4),
                "critical_values": {k: round(v, 4) for k, v in result[4].items()},
                "stationary": result[1] < 0.05,
            },
            indent=2,
        )
    elif method == "CORRELATION":
        cols = params.get(
            "columns", [c for c in df.select_dtypes(include=[np.number]).columns]
        )
        corr = df[cols].corr()
        return corr.to_csv()
    elif method == "COINTEGRATION":
        from statsmodels.tsa.stattools import coint

        col1 = params.get("column1", df.columns[1])
        col2 = params.get("column2", df.columns[2])
        score, pvalue, _ = coint(df[col1].dropna(), df[col2].dropna())
        return json.dumps(
            {
                "test_statistic": round(score, 4),
                "p_value": round(pvalue, 4),
                "cointegrated": pvalue < 0.05,
            },
            indent=2,
        )
    else:
        return f"Error: Unknown method '{method}'. Supported: ADF, CORRELATION, COINTEGRATION"


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
    out_path = os.path.join(_workspace_dir(), "backtest_analysis.json")
    with open(out_path, "w") as f:
        json.dump(metrics, f, indent=2)

    return (
        f"Backtest Analysis (saved to backtest_analysis.json):\n"
        f"  Sharpe Ratio:   {metrics['sharpe_ratio']}\n"
        f"  Annual Return:  {metrics['annual_return']:.2%}\n"
        f"  Total Return:   {metrics['total_return']:.2%}\n"
        f"  Max Drawdown:   {metrics['max_drawdown']:.2%}\n"
        f"  Win Rate:       {metrics['win_rate']:.2%}\n"
        f"  Volatility:     {metrics['volatility']:.2%}\n"
        f"  Sortino Ratio:  {metrics['sortino_ratio']}\n"
        f"  Calmar Ratio:   {metrics['calmar_ratio']}\n"
    )


def search_web(query: str, max_results: int = 5) -> str:
    """Search the public web using DuckDuckGo's instant answer endpoint."""
    from urllib.parse import urlencode
    from urllib.request import urlopen

    if not query or not query.strip():
        return "Error: query must be non-empty."

    max_results = max(1, min(int(max_results or 5), 10))
    params = urlencode(
        {
            "q": query,
            "format": "json",
            "no_html": "1",
            "no_redirect": "1",
            "skip_disambig": "1",
        }
    )
    url = f"https://api.duckduckgo.com/?{params}"

    try:
        with urlopen(url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8", errors="ignore"))
    except Exception as exc:
        return f"Error: web search failed: {type(exc).__name__}: {exc}"

    results = []
    abstract = str(payload.get("AbstractText", "")).strip()
    abstract_url = str(payload.get("AbstractURL", "")).strip()
    if abstract:
        results.append(
            {
                "title": payload.get("Heading") or query,
                "url": abstract_url,
                "snippet": abstract,
                "source": "abstract",
            }
        )

    def _collect_topics(items):
        for item in items:
            if len(results) >= max_results:
                return
            if isinstance(item, dict) and item.get("Topics"):
                _collect_topics(item.get("Topics") or [])
                continue
            text = str(item.get("Text", "")).strip() if isinstance(item, dict) else ""
            first_url = (
                str(item.get("FirstURL", "")).strip() if isinstance(item, dict) else ""
            )
            if text:
                results.append(
                    {
                        "title": text.split(" - ")[0][:120],
                        "url": first_url,
                        "snippet": text,
                        "source": "related_topic",
                    }
                )

    _collect_topics(payload.get("RelatedTopics") or [])

    if not results:
        return json.dumps(
            {"query": query, "results": [], "note": "No web results returned."},
            indent=2,
        )

    return json.dumps({"query": query, "results": results[:max_results]}, indent=2)


def search_docs(query: str) -> str:
    """Full-text search across the /docs/ directory."""
    results = []
    query_lower = query.lower()
    docs_dir = _docs_dir()
    for root, _, files in os.walk(docs_dir):
        for fname in files:
            if not fname.endswith(".md"):
                continue
            fpath = os.path.join(root, fname)
            with open(fpath) as f:
                content = f.read()
            lines = content.split("\n")
            matches = [
                (i + 1, line.strip())
                for i, line in enumerate(lines)
                if query_lower in line.lower()
            ]
            if matches:
                results.append({"file": fname, "matches": matches[:5]})
    if not results:
        return f"No results found for '{query}'"
    return json.dumps(results, indent=2)


def send_message(text: str) -> str:
    """Send a message to the student. Primary tutoring action."""
    return (
        f"Message sent: {text[:100]}..." if len(text) > 100 else f"Message sent: {text}"
    )


def get_environment_info() -> str:
    """Return available data files, installed packages, and workspace contents."""
    info = {"data_files": [], "docs": [], "workspace": [], "installed_packages": []}
    for d, key in [
        (_data_dir(), "data_files"),
        (_docs_dir(), "docs"),
        (_workspace_dir(), "workspace"),
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
    return json.dumps(info, indent=2)


# _resolve_path is defined near the top of this module (used by file_read,
# file_list, and callers below).


# Tool registry for the proxy layer
CORE_TOOLS = {
    "shell_exec": {
        "func": shell_exec,
        "description": "Execute a shell command in the sandbox",
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
        "description": "Write content to a file in the workspace",
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
        "description": "Read a file from workspace, data, docs, or student_code directories",
        "params": {
            "path": {
                "type": "string",
                "description": "File path to read. Searches workspace/, data/, docs/, student_code/ directories.",
                "required": True,
            },
        },
    },
    "file_list": {
        "func": file_list,
        "description": "List files in a directory",
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
        "description": "Return OHLCV data from frozen CSV for a given symbol and date range",
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
            "params": {
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
            "params": {
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
        "description": "Run statistical tests on data (stationarity, correlation, cointegration)",
        "params": {
            "data_path": {
                "type": "string",
                "description": "Path to CSV file with numeric data",
                "required": True,
            },
            "method": {
                "type": "string",
                "description": "Statistical method: ADF (stationarity test), CORRELATION (correlation matrix), or COINTEGRATION",
                "required": True,
            },
            "params": {
                "type": "object",
                "description": 'Method parameters as JSON object, e.g. {"column": "Close"} for ADF, {"column1": "AAPL", "column2": "SPY"} for COINTEGRATION',
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
        "description": "Compute standard performance metrics (Sharpe Ratio, Annual Return, Total Return, Max Drawdown, Win Rate, Volatility, Sortino, Calmar) from a CSV of portfolio/strategy returns. Auto-detects the returns column from common names. Saves structured results to backtest_analysis.json in workspace.",
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
    "search_web": {
        "func": search_web,
        "description": "Search the public web for official references and API documentation. Returns compact JSON results (title/url/snippet).",
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
        "description": "Full-text search across reference documentation in /docs/",
        "params": {
            "query": {
                "type": "string",
                "description": "Search query string, e.g. 'moving average', 'Sharpe ratio', 'backtest'",
                "required": True,
            },
        },
    },
    "send_message": {
        "func": send_message,
        "description": "Send a message to the student (primary tutoring action)",
        "params": {
            "text": {
                "type": "string",
                "description": "Message text to send to the student",
                "required": True,
            },
        },
    },
    "get_environment_info": {
        "func": get_environment_info,
        "description": "Return available data files, installed packages, and workspace contents",
        "params": {},
    },
}

# ── Tool Tier Classification ───────────────────────────────────
# Used by the evaluation system (tool_usage scoring) to distinguish
# data-gate tools (Essential) from optional shortcuts (Convenience).
#
# Essential tools: No alternative exists within the MCP tool set.
#   These are data gates, I/O channels, and code execution channels.
#
# Convenience tools: Self-contained shortcuts that each use Python
#   libraries directly. None of them call shell_exec, file_write,
#   or any other Essential tool internally.

ESSENTIAL_TOOLS = {
    "fetch_market_data",  # sole data gate (frozen CSVs)
    "file_read",  # sole file reading channel
    "file_write",  # sole file writing channel
    "shell_exec",  # sole code execution channel (DIY path)
    "file_list",  # sole directory listing
    "search_docs",  # sole documentation access
    "get_environment_info",  # sole environment introspection
}

CONVENIENCE_TOOLS = {
    "compute_indicator",  # pandas rolling/ewm — replaces ~10 lines
    "run_backtest",  # built-in strategies — replaces ~40 lines
    "analyze_backtest_results",  # numpy metrics — replaces ~30 lines
    "compute_statistics",  # statsmodels tests — replaces ~15 lines
    "plot_chart",  # matplotlib exec — replaces ~5 lines
}
