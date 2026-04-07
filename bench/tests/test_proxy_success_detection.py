"""Verify MCPProxy generic success/failure detection.

Tests that proxy marks log.success correctly using tool-name-agnostic
patterns, covering all previously hardcoded cases plus new generic ones.

Run:  python -m pytest tests/test_proxy_success_detection.py -v
  or: python -m tests.test_proxy_success_detection
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_servers.proxy.mcp_proxy import MCPProxy


def _make_proxy_with_tool(name: str, return_value: str) -> MCPProxy:
    """Create a proxy with a single tool that returns a fixed string."""
    proxy = MCPProxy()
    proxy.register_tool(name, lambda **kw: return_value, description="test tool")
    return proxy


def _call(proxy: MCPProxy, name: str) -> bool:
    """Call tool and return log.success."""
    proxy.call_tool(name)
    return proxy.get_logs()[-1].success


# =====================================================================
# 1. Cases that WERE hardcoded to specific tool names (regression tests)
# =====================================================================


class TestFormerlyHardcoded:
    """These cases previously used `if log.name == "shell_exec"` etc.
    Now they must work for ANY tool returning the same patterns."""

    def test_shell_exec_nonzero_exit_code(self):
        """[exit code]: N pattern → failure (was shell_exec-only)."""
        proxy = _make_proxy_with_tool("any_tool", "some output\n[exit code]: 1")
        assert _call(proxy, "any_tool") is False

    def test_shell_exec_zero_exit_code(self):
        """[exit code]: 0 → success."""
        proxy = _make_proxy_with_tool("any_tool", "some output\n[exit code]: 0")
        assert _call(proxy, "any_tool") is True

    def test_shell_exec_timeout(self):
        """'Error: Command timed out' → failure (was shell_exec-only)."""
        proxy = _make_proxy_with_tool("any_tool", "Error: Command timed out after 30s")
        assert _call(proxy, "any_tool") is False

    def test_lean_compile_error(self):
        """'Status: compile_error' → failure (was run_lean_backtest-only)."""
        proxy = _make_proxy_with_tool(
            "any_tool", "=== Trial 1 Result ===\nStatus: compile_error\nErrors: ..."
        )
        assert _call(proxy, "any_tool") is False

    def test_lean_runtime_error(self):
        """'Status: runtime_error' → failure (was run_lean_backtest-only)."""
        proxy = _make_proxy_with_tool(
            "any_tool", "=== Trial 1 Result ===\nStatus: runtime_error"
        )
        assert _call(proxy, "any_tool") is False

    def test_plot_chart_error(self):
        """'Error: ...' → failure (was plot_chart-only)."""
        proxy = _make_proxy_with_tool("any_tool", "Error: Chart file was not generated")
        assert _call(proxy, "any_tool") is False

    def test_stderr_no_longer_overrides(self):
        """stderr with 'No such file' but exit code 0 → NOW success.

        Previously this was hardcoded as failure for shell_exec.
        Removed: exit code 0 means the process succeeded; stderr
        warnings are not necessarily fatal.
        """
        proxy = _make_proxy_with_tool(
            "shell_exec",
            "output ok\n[stderr]: warning: No such file foo.tmp\n[exit code]: 0",
        )
        assert _call(proxy, "shell_exec") is True


# =====================================================================
# 2. Generic error pattern detection
# =====================================================================


class TestGenericPatterns:
    """Test that generic error patterns work regardless of tool name."""

    def test_error_colon_prefix(self):
        proxy = _make_proxy_with_tool("custom_tool", "Error: Something went wrong")
        assert _call(proxy, "custom_tool") is False

    def test_error_space_prefix(self):
        proxy = _make_proxy_with_tool("custom_tool", "Error 404: not found")
        assert _call(proxy, "custom_tool") is False

    def test_python_traceback(self):
        proxy = _make_proxy_with_tool(
            "compute_stats",
            "Traceback (most recent call last):\n  File ...\nValueError: ...",
        )
        assert _call(proxy, "compute_stats") is False

    def test_negative_exit_code(self):
        proxy = _make_proxy_with_tool("run_script", "Segfault\n[exit code]: -11")
        assert _call(proxy, "run_script") is False

    def test_exit_code_deep_in_output(self):
        """Exit code marker can appear anywhere in the output."""
        proxy = _make_proxy_with_tool(
            "run_script", "line1\nline2\nline3\n[exit code]: 2\n"
        )
        assert _call(proxy, "run_script") is False


# =====================================================================
# 3. Success cases — must NOT be marked as failure
# =====================================================================


class TestSuccessCases:
    """Ensure normal outputs are not false-positived into failure."""

    def test_normal_output(self):
        proxy = _make_proxy_with_tool("file_read", "Hello world\nline 2")
        assert _call(proxy, "file_read") is True

    def test_output_mentioning_error_in_content(self):
        """The word 'error' mid-text should NOT trigger failure."""
        proxy = _make_proxy_with_tool(
            "analyze",
            "The tracking error was 2.3%, which is acceptable.",
        )
        assert _call(proxy, "analyze") is True

    def test_error_in_middle_not_prefix(self):
        """'Error:' only triggers when it's a prefix, not mid-text."""
        proxy = _make_proxy_with_tool(
            "analyze", "Results show Mean Absolute Error: 0.05"
        )
        assert _call(proxy, "analyze") is True

    def test_status_success(self):
        """'Status: success' should not be confused with error statuses."""
        proxy = _make_proxy_with_tool(
            "run_backtest",
            "=== Trial 1 Result ===\nStatus: success\nSharpe: 1.5",
        )
        assert _call(proxy, "run_backtest") is True

    def test_exit_code_zero(self):
        proxy = _make_proxy_with_tool("shell_exec", "compilation done\n[exit code]: 0")
        assert _call(proxy, "shell_exec") is True

    def test_empty_but_returned(self):
        """Empty string from tool function is not an error (tool ran fine)."""
        proxy = _make_proxy_with_tool("file_write", "")
        assert _call(proxy, "file_write") is True

    def test_traceback_substring_in_explanation(self):
        """Exact match 'Traceback (most recent call last)' required."""
        proxy = _make_proxy_with_tool(
            "tutor",
            "A traceback shows the call stack. Use 'Traceback' to debug.",
        )
        assert _call(proxy, "tutor") is True


# =====================================================================
# 4. Edge cases
# =====================================================================


class TestEdgeCases:
    def test_exception_in_tool(self):
        """Tool that raises → success=False with Error message."""
        proxy = MCPProxy()
        proxy.register_tool(
            "bad_tool", lambda **kw: (_ for _ in ()).throw(ValueError("boom"))
        )
        proxy.call_tool("bad_tool")
        log = proxy.get_logs()[-1]
        assert log.success is False
        assert "ValueError" in log.result

    def test_unknown_tool(self):
        proxy = MCPProxy()
        proxy.call_tool("nonexistent")
        log = proxy.get_logs()[-1]
        assert log.success is False
        assert "Unknown tool" in log.result

    def test_distractor_error_based(self):
        proxy = MCPProxy()
        proxy.register_distractor("fake_tool", error_message="Not available")
        proxy.call_tool("fake_tool")
        log = proxy.get_logs()[-1]
        assert log.success is False

    def test_distractor_functional(self):
        """Functional distractor returns plausible output → success=True."""
        proxy = MCPProxy()
        proxy.register_distractor(
            "sentiment_analysis",
            func=lambda **kw: "Sentiment: positive (0.85)",
            description="Analyze sentiment",
        )
        proxy.call_tool("sentiment_analysis")
        log = proxy.get_logs()[-1]
        assert log.success is True

    def test_multiple_patterns_first_wins(self):
        """Output with both Error prefix and exit code — Error prefix detected first."""
        proxy = _make_proxy_with_tool(
            "tool",
            "Error: failed\n[exit code]: 1",
        )
        assert _call(proxy, "tool") is False


# =====================================================================
# CLI runner
# =====================================================================

if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
