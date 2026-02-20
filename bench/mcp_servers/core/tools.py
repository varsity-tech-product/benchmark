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


def file_read(path: str) -> str:
    """Read a file from workspace, data, docs, or student_code."""
    for base in [_workspace_dir(), _data_dir(), _docs_dir(), _student_code_dir()]:
        full = os.path.join(base, path) if not path.startswith("/") else path
        if os.path.isfile(full):
            with open(full, "r") as f:
                content = f.read()
            if len(content) > 50000:
                return (
                    content[:50000] + f"\n... (truncated, {len(content)} total bytes)"
                )
            return content
    return f"Error: File not found: {path}"


def file_list(directory: str = ".") -> str:
    """List files in a directory."""
    for base in [_workspace_dir(), _data_dir(), _docs_dir(), _student_code_dir()]:
        full = (
            os.path.join(base, directory)
            if not directory.startswith("/")
            else directory
        )
        if os.path.isdir(full):
            entries = sorted(os.listdir(full))
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
    return df.to_csv(index=False)


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

    return df.tail(20).to_csv(index=False)


def run_backtest(script_path: str) -> str:
    """Execute a backtest script and return structured results."""
    workspace = _workspace_dir()
    full_path = (
        os.path.join(workspace, script_path)
        if not script_path.startswith("/")
        else script_path
    )
    if not os.path.isfile(full_path):
        return f"Error: Script not found: {script_path}"
    return shell_exec(f"python -u {full_path}", timeout=30)


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
    """Execute matplotlib code, save and return the image path."""
    workspace = _workspace_dir()
    chart_path = os.path.join(workspace, f"chart_{int(__import__('time').time())}.png")
    code_with_save = (
        python_code
        + f"\nimport matplotlib.pyplot as plt\nplt.savefig('{chart_path}', dpi=100, bbox_inches='tight')\nplt.close()"
    )
    exec_result = shell_exec(f'python -c "{code_with_save}"')
    if os.path.isfile(chart_path):
        return f"Chart saved to {chart_path}"
    return f"Error generating chart: {exec_result}"


def format_table(data: str, columns: Optional[list] = None, title: str = "") -> str:
    """Format data into a clean markdown table."""
    import io

    import pandas as pd

    try:
        df = pd.read_csv(io.StringIO(data))
        if columns:
            df = df[columns]
        header = f"### {title}\n\n" if title else ""
        return header + df.to_markdown(index=False)
    except Exception as e:
        return f"Error formatting table: {e}"


def compare_series(paths: list, metric: str = "sharpe") -> str:
    """Compare multiple return series on a given metric."""
    import numpy as np
    import pandas as pd

    results = {}
    for p in paths:
        full = _resolve_path(p)
        if not full:
            results[p] = {"error": "File not found"}
            continue
        df = pd.read_csv(full)
        returns_col = [c for c in df.columns if "return" in c.lower()]
        if not returns_col:
            if "Close" in df.columns:
                returns = df["Close"].pct_change().dropna()
            else:
                results[p] = {"error": "No returns column found"}
                continue
        else:
            returns = df[returns_col[0]].dropna()

        metric_lower = metric.lower()
        if metric_lower == "sharpe":
            val = (
                returns.mean() / returns.std() * np.sqrt(252)
                if returns.std() > 0
                else 0
            )
        elif metric_lower == "volatility":
            val = returns.std() * np.sqrt(252)
        elif metric_lower == "total_return":
            val = (1 + returns).prod() - 1
        else:
            val = 0
        results[p] = {metric: round(float(val), 4)}

    return json.dumps(results, indent=2)


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


def _resolve_path(path: str) -> Optional[str]:
    """Resolve a path by checking workspace, data, docs, student_code."""
    if os.path.isfile(path):
        return path
    for base in [_workspace_dir(), _data_dir(), _docs_dir(), _student_code_dir()]:
        full = os.path.join(base, path)
        if os.path.isfile(full):
            return full
    return None


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
        "description": "Compute a technical indicator on a dataset and return the last 20 rows",
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
        "description": "Execute a Python backtest script in the workspace and return its output",
        "params": {
            "script_path": {
                "type": "string",
                "description": "Path to a Python script in the workspace, e.g. 'backtest.py'",
                "required": True,
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
        "description": "Execute matplotlib Python code, save chart as PNG, and return the image path",
        "params": {
            "python_code": {
                "type": "string",
                "description": "Complete matplotlib Python code. Chart is auto-saved (no need to call plt.savefig).",
                "required": True,
            },
        },
    },
    "format_table": {
        "func": format_table,
        "description": "Format CSV data into a clean markdown table",
        "params": {
            "data": {
                "type": "string",
                "description": "CSV-formatted string data to display as a table",
                "required": True,
            },
            "columns": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Column names to include. Omit to show all columns.",
                "required": False,
            },
            "title": {
                "type": "string",
                "description": "Optional table title displayed as a markdown heading",
                "required": False,
            },
        },
    },
    "compare_series": {
        "func": compare_series,
        "description": "Compare multiple return series on a performance metric",
        "params": {
            "paths": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of CSV file paths to compare, e.g. ['strategy_a.csv', 'strategy_b.csv']",
                "required": True,
            },
            "metric": {
                "type": "string",
                "description": "Comparison metric: 'sharpe', 'volatility', or 'total_return'. Default: 'sharpe'.",
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
