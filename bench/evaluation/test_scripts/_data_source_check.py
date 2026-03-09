"""Shared data source verification for eval scripts.

Checks whether the agent actually accessed the task-specified data files
rather than fabricating synthetic data.
"""

import os


def verify_data_source(
    tool_logs: list,
    data_files: list[str],
) -> dict:
    """Check if the agent accessed the expected data files.

    Scans tool logs for evidence that the agent loaded/read the specified
    data files via fetch_market_data, file_read, or shell_exec (read_csv, etc.).

    Args:
        tool_logs: Tool call logs (ToolCallLog objects).
        data_files: Expected file names from task.environment.data_files.

    Returns:
        Dict with:
            verified: bool — True if ALL expected files were accessed.
            files_accessed: list — Which expected files were found in logs.
            files_missing: list — Which expected files were NOT found.
            fraction: float — files_accessed / data_files (0-1).
    """
    if not data_files:
        return {
            "verified": True,
            "files_accessed": [],
            "files_missing": [],
            "fraction": 1.0,
        }

    accessed: set[str] = set()

    for log in tool_logs or []:
        name = log.name or ""
        args_str = " ".join(str(v) for v in log.args.values()).lower()
        result_str = str(log.result or "").lower()

        for fname in data_files:
            fname_lower = fname.lower()
            base_no_ext = os.path.splitext(fname_lower)[0]  # e.g. "aapl_2018_2024"

            # Check 1: fetch_market_data with matching filename in args or result
            if name == "fetch_market_data":
                if fname_lower in args_str or fname_lower in result_str:
                    accessed.add(fname)
                    continue

            # Check 2: file_read with matching filename
            if name == "file_read":
                if fname_lower in args_str:
                    accessed.add(fname)
                    continue

            # Check 3: shell_exec referencing the file (read_csv, cat, head, etc.)
            if name == "shell_exec":
                if fname_lower in args_str:
                    accessed.add(fname)
                    continue
                # Also check for the base name without extension in pandas calls
                if base_no_ext in args_str and any(
                    kw in args_str
                    for kw in ["read_csv", "read_parquet", "pd.read", "pandas"]
                ):
                    accessed.add(fname)
                    continue

            # Check 4: Any tool that references the file in result (data was returned)
            if fname_lower in result_str and len(result_str) > 100:
                accessed.add(fname)

    files_accessed = sorted(accessed)
    files_missing = sorted(set(data_files) - accessed)
    fraction = len(files_accessed) / len(data_files) if data_files else 1.0

    return {
        "verified": len(files_missing) == 0,
        "files_accessed": files_accessed,
        "files_missing": files_missing,
        "fraction": round(fraction, 4),
    }
