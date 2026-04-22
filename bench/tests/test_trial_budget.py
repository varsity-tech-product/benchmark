"""Verify trial budget enforcement in run_lean_backtest.

Run:  python -m pytest tests/test_trial_budget.py -v
"""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_budget_blocks_after_max_trials():
    """run_lean_backtest refuses when trial budget is exhausted."""
    from mcp_servers.core.tools import _trial_managers, run_lean_backtest
    from mcp_servers.core.trial_manager import TrialManager

    with tempfile.TemporaryDirectory() as tmpdir:
        # Set up environment so _get_trial_manager uses our tmpdir
        os.environ["QTB_WORKSPACE_DIR"] = tmpdir
        os.environ["QTB_MAX_BACKTEST_TRIALS"] = "2"
        _trial_managers.clear()

        tm = TrialManager(tmpdir, max_trials=2)
        _trial_managers[tmpdir] = tm

        # Simulate 2 completed trials by writing manifest
        trials_dir = Path(tmpdir) / ".trials"
        trials_dir.mkdir()
        manifest = {
            "max_trials": 2,
            "trials_used": 2,
            "trials": {
                "1": {"status": "success"},
                "2": {"status": "compile_error"},
            },
            "selected_trial": None,
        }
        (trials_dir / "manifest.json").write_text(json.dumps(manifest))

        # Now try to run — should be blocked without calling shell_exec
        result = run_lean_backtest("Algorithm.cs")

        assert (
            "budget exhausted" in result.lower()
        ), f"Expected budget error, got: {result}"
        assert "2/2" in result, f"Expected 2/2 count, got: {result}"
        assert (
            "select_submission" in result
        ), f"Expected guidance to select, got: {result}"

        # Clean up
        _trial_managers.clear()
        os.environ.pop("QTB_WORKSPACE_DIR", None)
        os.environ.pop("QTB_MAX_BACKTEST_TRIALS", None)

    print("✓ Budget enforcement blocks run_lean_backtest after max trials")


def test_budget_allows_within_limit():
    """run_lean_backtest proceeds when budget remains (mocked shell_exec)."""
    from mcp_servers.core.tools import _trial_managers
    from mcp_servers.core.trial_manager import TrialManager

    with tempfile.TemporaryDirectory() as tmpdir:
        os.environ["QTB_WORKSPACE_DIR"] = tmpdir
        os.environ["QTB_MAX_BACKTEST_TRIALS"] = "3"
        _trial_managers.clear()

        tm = TrialManager(tmpdir, max_trials=3)
        _trial_managers[tmpdir] = tm

        # 1 trial used, 2 remaining — should be allowed
        trials_dir = Path(tmpdir) / ".trials"
        trials_dir.mkdir()
        manifest = {
            "max_trials": 3,
            "trials_used": 1,
            "trials": {"1": {"status": "success"}},
            "selected_trial": None,
        }
        (trials_dir / "manifest.json").write_text(json.dumps(manifest))

        # Mock shell_exec so we don't actually run anything
        with patch(
            "mcp_servers.core.tools.shell_exec",
            return_value="mock output\n[exit code]: 0",
        ):
            from mcp_servers.core.tools import run_lean_backtest

            result = run_lean_backtest("Algorithm.cs")

        # Should NOT contain budget error
        assert (
            "budget exhausted" not in result.lower()
        ), f"Should have proceeded, got: {result}"

        _trial_managers.clear()
        os.environ.pop("QTB_WORKSPACE_DIR", None)
        os.environ.pop("QTB_MAX_BACKTEST_TRIALS", None)

    print("✓ Budget allows execution when trials remain")


if __name__ == "__main__":
    test_budget_blocks_after_max_trials()
    test_budget_allows_within_limit()
    print("\nAll budget tests passed.")
