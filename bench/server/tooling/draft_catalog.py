"""Draft canonical tool specs for the new client-server architecture.

This file is intentionally isolated from the legacy benchmark stack.
It provides manual high-quality overrides for the canonical tool catalog
used by the new client-server architecture, without leaking
benchmark-only metadata such as internal role labels, task-specific
hints, real data paths, or concrete dataset filenames.
"""

from __future__ import annotations

from typing import Any

from .spec_types import (
    ToolExample,
    ToolGuidance,
    ToolSpec,
    agent_visible_payload,
)


def _schema(
    properties: dict[str, dict[str, Any]],
    required: list[str] | tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build a standard JSON Schema object definition."""
    data: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        data["required"] = list(required)
    return data


DRAFT_TOOL_SPECS: dict[str, ToolSpec] = {
    "note_to_self": ToolSpec(
        name="note_to_self",
        description=(
            "Record your reasoning, observations, or intermediate findings "
            "for your own reference. Content is not shown to the user. "
            "Use this to organize your thoughts before responding."
        ),
        input_schema=_schema(
            {
                "thought": {
                    "type": "string",
                    "description": "Your note — reasoning, hypothesis, observation, or plan.",
                },
            },
            required=["thought"],
        ),
        guidance=ToolGuidance(
            family="foundation",
            use_when=(
                "You want to record an observation or hypothesis before acting.",
                "You are debugging and want to track what you have tried.",
                "You are planning a multi-step approach.",
            ),
            avoid_when=(
                "The thought is trivial or obvious from context.",
            ),
            common_mistakes=(
                "Writing to the user instead of to yourself — this tool is private.",
            ),
            returns="A short acknowledgment. The note is logged automatically.",
            related_tools=("send_message", "shell_exec"),
        ),
        examples=(
            ToolExample(
                intent="Record a debugging hypothesis",
                args={"thought": "The off-by-one error is likely in the rolling window size. Let me check line 3."},
            ),
        ),
        benchmark_role="core",
        domain_scope=("reasoning",),
        abstraction_level="low",
    ),
    "get_environment_info": ToolSpec(
        name="get_environment_info",
        description=(
            "Discover the sandbox environment, including available directories, "
            "documents, datasets, packages, and truthful session runtime context."
        ),
        input_schema=_schema({}),
        guidance=ToolGuidance(
            family="foundation",
            use_when=(
                "Start of an unfamiliar task.",
                "Need to discover where data, docs, or outputs live.",
                "Need to know whether specialized runtime support is available.",
            ),
            avoid_when=(
                "You already know the relevant files and only need their contents.",
            ),
            common_mistakes=(
                "Calling it repeatedly after the environment is already known.",
                "Treating environment notes as task-specific instructions.",
            ),
            returns=(
                "A JSON object describing the sandbox layout, available files, "
                "installed packages, and high-level environment notes. When "
                "available it also includes mounted task-provided code "
                "directories and tracked trial-budget context."
            ),
            related_tools=("file_list", "file_read", "search_docs", "shell_exec"),
        ),
        examples=(
            ToolExample(
                intent="Inspect the environment before choosing the first action",
                args={},
            ),
        ),
        benchmark_role="core",
        domain_scope=("environment", "filesystem"),
        abstraction_level="low",
    ),
    "file_list": ToolSpec(
        name="file_list",
        description="List files in a sandbox directory.",
        input_schema=_schema(
            {
                "directory": {
                    "type": "string",
                    "description": (
                        "Directory to inspect. Use a relative directory name or '.' "
                        "for the current working area."
                    ),
                },
            }
        ),
        guidance=ToolGuidance(
            family="foundation",
            use_when=(
                "Need to see which files already exist.",
                "Need to verify whether an output artifact was created.",
            ),
            avoid_when=("You need file contents rather than filenames.",),
            common_mistakes=(
                "Passing 'path' instead of 'directory'.",
                "Using file_read on a directory name.",
            ),
            returns="A newline-separated directory listing or an empty-directory marker.",
            related_tools=("file_read", "get_environment_info"),
        ),
        examples=(
            ToolExample(
                intent="List files in the current working area",
                args={"directory": "."},
            ),
        ),
        benchmark_role="core",
        domain_scope=("filesystem",),
        abstraction_level="low",
    ),
    "file_read": ToolSpec(
        name="file_read",
        description=(
            "Read a text file from the sandbox working area, data area, docs area, "
            "or other allowed directories."
        ),
        input_schema=_schema(
            {
                "path": {
                    "type": "string",
                    "description": (
                        "File to read. Use a relative file name or an allowed "
                        "sandbox file path."
                    ),
                },
                "offset": {
                    "type": "integer",
                    "description": "Zero-based line offset for paginated reads.",
                },
                "max_lines": {
                    "type": "integer",
                    "description": (
                        "Maximum number of lines to return. Use when you need a "
                        "specific slice instead of the default behavior."
                    ),
                },
            },
            required=("path",),
        ),
        guidance=ToolGuidance(
            family="foundation",
            use_when=(
                "Need to inspect a document, source file, report, or generated artifact.",
                "Need a lightweight preview of a large CSV before deciding on deeper analysis.",
            ),
            avoid_when=(
                "You only need a directory listing.",
                "You need full programmatic analysis of a large dataset rather than a preview.",
            ),
            common_mistakes=(
                "Passing 'filepath' instead of 'path'.",
                "Assuming a large CSV preview is the full dataset.",
                "Trying to read a directory rather than a file.",
            ),
            returns=(
                "Full text for small files, a paginated slice when requested, "
                "or a compact preview for large CSV files."
            ),
            related_tools=("file_list", "search_docs", "shell_exec"),
        ),
        examples=(
            ToolExample(
                intent="Read a documentation file",
                args={"path": "guide.md"},
            ),
            ToolExample(
                intent="Read a specific slice of a large text file",
                args={"path": "analysis_log.txt", "offset": 0, "max_lines": 30},
            ),
        ),
        benchmark_role="core",
        domain_scope=("filesystem", "docs", "data"),
        abstraction_level="low",
    ),
    "file_write": ToolSpec(
        name="file_write",
        description="Write a text file into the sandbox working area.",
        input_schema=_schema(
            {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative output file name or nested path inside the working area."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "Full text content to write to the file.",
                },
            },
            required=("path", "content"),
        ),
        guidance=ToolGuidance(
            family="foundation",
            use_when=(
                "Need to create a report, script, notes file, or reusable artifact.",
            ),
            avoid_when=("You only want to inspect or list files.",),
            common_mistakes=(
                "The parameter is 'content', not 'contents'.",
                "Trying to write outside the sandbox working area.",
            ),
            returns="A success message with the written file path and byte count.",
            related_tools=("file_read", "shell_exec"),
        ),
        examples=(
            ToolExample(
                intent="Create a markdown report",
                args={"path": "report.md", "content": "# Summary\n"},
            ),
        ),
        benchmark_role="core",
        domain_scope=("filesystem",),
        abstraction_level="low",
    ),
    "shell_exec": ToolSpec(
        name="shell_exec",
        description="Execute a shell command inside the sandbox working area.",
        input_schema=_schema(
            {
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command to run. Use this for custom scripts, "
                        "Python analysis, or multi-step shell workflows."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Optional timeout in seconds.",
                },
            },
            required=("command",),
        ),
        guidance=ToolGuidance(
            family="foundation",
            use_when=(
                "Need custom analysis not covered by a built-in tool.",
                "Need to run a Python script, combine multiple shell operations, or inspect runtime behavior.",
            ),
            avoid_when=(
                "A dedicated higher-level tool already provides the needed operation cleanly.",
                "You only need a simple file listing or file read.",
            ),
            common_mistakes=(
                "Missing the required 'command' field.",
                "Assuming the command runs from an arbitrary directory instead of the working area.",
                "Using it for simple file inspection that smaller tools already cover.",
            ),
            returns=(
                "Command output including stdout, optional stderr, and non-zero "
                "exit code information when relevant."
            ),
            related_tools=("get_environment_info", "file_read", "file_write"),
        ),
        examples=(
            ToolExample(
                intent="Run a short Python inspection script",
                args={
                    "command": (
                        "python3 - <<'EOF'\n"
                        "import pandas as pd\n"
                        "df = pd.read_csv('dataset.csv')\n"
                        "print(df.head())\n"
                        "EOF"
                    ),
                    "timeout": 60,
                },
            ),
        ),
        benchmark_role="core",
        domain_scope=("execution", "filesystem"),
        abstraction_level="low",
    ),
    "search_docs": ToolSpec(
        name="search_docs",
        description="Search sandbox documentation by keywords.",
        input_schema=_schema(
            {
                "query": {
                    "type": "string",
                    "description": "Keyword-style query for relevant documentation lines.",
                },
            },
            required=("query",),
        ),
        guidance=ToolGuidance(
            family="docs",
            use_when=(
                "Need to locate relevant docs before reading them in full.",
                "Need usage hints for libraries, APIs, or sandbox conventions.",
            ),
            avoid_when=("You already know the exact document you want to read.",),
            common_mistakes=(
                "Using a long natural-language paragraph instead of targeted keywords.",
                "Expecting a complete answer instead of ranked candidate matches.",
            ),
            returns=(
                "A ranked JSON list of documentation matches with filenames, lines, and text snippets."
            ),
            related_tools=("file_read", "get_environment_info"),
        ),
        examples=(
            ToolExample(
                intent="Find docs related to datetime parsing",
                args={"query": "read_csv parse_dates timezone"},
            ),
        ),
        benchmark_role="core",
        domain_scope=("docs",),
        abstraction_level="medium",
    ),
    "compute_statistics": ToolSpec(
        name="compute_statistics",
        description=(
            "Run built-in statistical analysis on tabular data, including "
            "descriptive summaries, missing-value checks, correlation, "
            "stationarity, cointegration, lead-lag, and rolling analysis."
        ),
        input_schema=_schema(
            {
                "data_path": {
                    "type": "string",
                    "description": "Input data file to analyze.",
                },
                "method": {
                    "type": "string",
                    "description": (
                        "Analysis method name such as ADF, CORRELATION, "
                        "COINTEGRATION, DESCRIPTIVE, MISSING, LEAD_LAG, or ROLLING."
                    ),
                },
                "method_params": {
                    "type": "object",
                    "description": (
                        "Method-specific options. Put method details here "
                        "instead of creating extra top-level arguments."
                    ),
                },
            },
            required=("data_path", "method"),
        ),
        guidance=ToolGuidance(
            family="analysis",
            use_when=(
                "Need a standard statistical workflow without writing custom analysis code.",
                "Need a reusable built-in method for exploratory or comparative analysis.",
            ),
            avoid_when=(
                "You need custom logic that is not covered by one of the supported methods.",
            ),
            common_mistakes=(
                "Method-specific options belong in 'method_params'.",
                "Do not pass extra fields like 'column' at the top level.",
                "Method names are constrained to the supported built-ins.",
            ),
            returns=(
                "Method-dependent structured output, usually JSON or tabular text, "
                "describing the requested analysis."
            ),
            related_tools=("file_read", "plot_chart", "shell_exec"),
        ),
        examples=(
            ToolExample(
                intent="Run descriptive statistics on a target column",
                args={
                    "data_path": "dataset.csv",
                    "method": "DESCRIPTIVE",
                    "method_params": {"column": "value"},
                },
            ),
        ),
        benchmark_role="convenient",
        domain_scope=("statistics", "analysis"),
        abstraction_level="high",
    ),
    "plot_chart": ToolSpec(
        name="plot_chart",
        description=(
            "Execute matplotlib plotting code and save a chart image automatically."
        ),
        input_schema=_schema(
            {
                "python_code": {
                    "type": "string",
                    "description": (
                        "Matplotlib-oriented Python code that builds a figure."
                    ),
                },
            },
            required=("python_code",),
        ),
        guidance=ToolGuidance(
            family="analysis",
            use_when=(
                "Need a quick chart artifact from Python plotting logic.",
                "Need a visual summary after computing statistics or signals.",
            ),
            avoid_when=("You only need numeric summaries.",),
            common_mistakes=(
                "The argument is 'python_code', not 'code'.",
                "You do not need to call savefig manually.",
            ),
            returns="A saved chart image path or a chart-generation error.",
            related_tools=("compute_statistics", "shell_exec", "file_read"),
        ),
        examples=(
            ToolExample(
                intent="Create a simple line chart from tabular data",
                args={
                    "python_code": (
                        "import pandas as pd\n"
                        "import matplotlib.pyplot as plt\n"
                        "df = pd.read_csv('dataset.csv')\n"
                        "plt.plot(df['value'])\n"
                        "plt.title('Series Overview')"
                    ),
                },
            ),
        ),
        benchmark_role="convenient",
        domain_scope=("analysis", "visualization"),
        abstraction_level="high",
    ),
    "run_backtest": ToolSpec(
        name="run_backtest",
        description=(
            "Run a built-in backtest workflow for supported strategy families "
            "without writing a custom script."
        ),
        input_schema=_schema(
            {
                "data_path": {
                    "type": "string",
                    "description": "Input market data file for the backtest.",
                },
                "strategy": {
                    "type": "string",
                    "description": (
                        "Built-in strategy identifier such as ma_crossover, "
                        "rsi_threshold, or bollinger_breakout."
                    ),
                },
                "strategy_params": {
                    "type": "object",
                    "description": "Optional strategy-specific parameters.",
                },
                "start": {
                    "type": "string",
                    "description": "Optional inclusive start date filter.",
                },
                "end": {
                    "type": "string",
                    "description": "Optional inclusive end date filter.",
                },
            },
            required=("data_path", "strategy"),
        ),
        guidance=ToolGuidance(
            family="backtest",
            use_when=(
                "Need a fast standardized backtest for a supported built-in strategy.",
                "Need portfolio metrics and saved artifacts without writing custom code.",
            ),
            avoid_when=(
                "The strategy logic is custom or unsupported by the built-in set.",
            ),
            common_mistakes=(
                "Unknown strategy names are rejected.",
                "Strategy-specific options belong in 'strategy_params'.",
                "This tool does not run arbitrary user code.",
            ),
            returns=(
                "A text summary with performance metrics and the names of saved result artifacts."
            ),
            related_tools=("analyze_backtest_results", "plot_chart", "shell_exec"),
        ),
        examples=(
            ToolExample(
                intent="Backtest a moving-average crossover strategy",
                args={
                    "data_path": "market_data.csv",
                    "strategy": "ma_crossover",
                    "strategy_params": {"fast_window": 10, "slow_window": 30},
                },
            ),
        ),
        benchmark_role="convenient",
        domain_scope=("backtest", "strategy"),
        abstraction_level="high",
    ),
    "run_lean_backtest": ToolSpec(
        name="run_lean_backtest",
        description=(
            "Compile and run a tracked LEAN C# backtest trial inside the sandbox."
        ),
        input_schema=_schema(
            {
                "algorithm_path": {
                    "type": "string",
                    "description": (
                        "Relative path to the sandbox C# algorithm source file. "
                        "This may be in the working area or a mounted user_code area."
                    ),
                },
                "params_json": {
                    "type": "string",
                    "description": "Optional JSON string of runtime parameters.",
                },
                "run_id": {
                    "type": "string",
                    "description": "Optional identifier for a named run or trial variant.",
                },
                "class_name": {
                    "type": "string",
                    "description": (
                        "Optional C# class name override for the QCAlgorithm entrypoint."
                    ),
                },
                "full_type_name": {
                    "type": "string",
                    "description": (
                        "Optional fully-qualified C# type name override such as "
                        "'MyNamespace.MyStrategy'."
                    ),
                },
            },
            required=("algorithm_path",),
        ),
        guidance=ToolGuidance(
            family="lean_trials",
            use_when=(
                "Need a benchmark-tracked LEAN backtest iteration.",
                "Need trial budgeting, recorded metrics, or tracked submissions.",
            ),
            avoid_when=(
                "The task does not require the LEAN trial workflow.",
                "You only need to inspect existing results instead of running a new trial.",
            ),
            common_mistakes=(
                "The algorithm source file must exist in an allowed sandbox area.",
                "Runtime parameters must be passed as a JSON string, not as an object.",
                "If the source uses a non-default namespace or class, rely on the tool's inferred entrypoint or pass class_name/full_type_name explicitly.",
            ),
            returns=(
                "Tracked trial status with key metrics, remaining trial budget, "
                "resolved source path, and selected entrypoint information."
            ),
            related_tools=(
                "get_trial_status",
                "select_submission",
                "analyze_lean_results",
                "file_write",
            ),
        ),
        examples=(
            ToolExample(
                intent="Run a tracked LEAN trial with optional parameters",
                args={
                    "algorithm_path": "strategy.cs",
                    "params_json": '{"fast": 10, "slow": 30}',
                    "run_id": "baseline",
                },
            ),
            ToolExample(
                intent="Run mounted task-provided code with an explicit fully-qualified entrypoint",
                args={
                    "algorithm_path": "user_code/user_code.cs",
                    "full_type_name": "MyNamespace.MyStrategy",
                },
            ),
        ),
        benchmark_role="core",
        domain_scope=("lean", "backtest"),
        abstraction_level="high",
    ),
    "analyze_lean_results": ToolSpec(
        name="analyze_lean_results",
        description=(
            "Summarize LEAN backtest outputs into structured sections such as "
            "summary metrics, orders, trades, and symbol-level breakdowns."
        ),
        input_schema=_schema(
            {
                "results_path": {
                    "type": "string",
                    "description": "Optional path to a results directory.",
                },
                "trial_id": {
                    "type": "integer",
                    "description": "Optional tracked trial number to analyze.",
                },
                "sections": {
                    "type": "string",
                    "description": (
                        "Comma-separated section names such as summary, orders, trades, symbols, or all."
                    ),
                },
            }
        ),
        guidance=ToolGuidance(
            family="lean_trials",
            use_when=(
                "Need a structured readout of LEAN results without writing custom parsers.",
                "Need order-level or symbol-level inspection of a tracked trial.",
            ),
            avoid_when=("No LEAN result set exists yet.",),
            common_mistakes=(
                "The 'sections' field is a comma-separated string, not an array.",
                "A provided trial_id overrides results_path.",
            ),
            returns=(
                "A structured text summary covering the requested LEAN result sections."
            ),
            related_tools=(
                "run_lean_backtest",
                "get_trial_status",
                "select_submission",
            ),
        ),
        examples=(
            ToolExample(
                intent="Review the latest trial at a high level",
                args={"sections": "summary"},
            ),
            ToolExample(
                intent="Inspect order and trade details for a specific trial",
                args={"trial_id": 3, "sections": "orders,trades"},
            ),
        ),
        benchmark_role="convenient",
        domain_scope=("lean", "backtest", "analysis"),
        abstraction_level="high",
    ),
}


def get_agent_visible_tool_spec(name: str) -> dict[str, Any]:
    """Return the safe agent-visible payload for a named draft tool spec."""
    return agent_visible_payload(DRAFT_TOOL_SPECS[name])
