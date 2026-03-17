"""Trial management for I-series LEAN backtest tasks.

Tracks iteration cycles (compile errors, empty trades, logic fixes),
stores snapshots and results per trial, supports version selection,
and provides efficiency scoring for the evaluation layer.

All state is persisted to `.trials/manifest.json` within the workspace
directory, surviving tool restarts inside the container.
"""

from __future__ import annotations

import fcntl
import json
import shutil
from pathlib import Path
from typing import Optional


class TrialManager:
    """Manages trial state for a single workspace."""

    def __init__(self, workspace_path: str, max_trials: int = 5):
        self.workspace = Path(workspace_path)
        self.max_trials = max_trials
        self._trials_dir = self.workspace / ".trials"
        self._manifest_path = self._trials_dir / "manifest.json"

    # ── Public API ──────────────────────────────────────────────

    def can_run(self) -> bool:
        """Return True if there are remaining trials in the budget.

        Uses .backtest_runs.jsonl line count as the source of truth,
        covering both tool-invoked and shell_exec-invoked runs.
        """
        return self.count_all_runs() < self.max_trials

    def next_trial_id(self) -> int:
        """Return the next trial ID based on total run count (1-based).

        Does NOT increment any counter — the JSONL log (written by
        run_backtest.sh EXIT trap) is the single source of truth.
        """
        return self.count_all_runs() + 1

    def trials_used(self) -> int:
        """Return the number of trials consumed so far.

        Reads from .backtest_runs.jsonl for accurate cross-path counting.
        """
        return self.count_all_runs()

    def count_all_runs(self) -> int:
        """Count total backtest runs from the JSONL log (source of truth).

        The log is written by run_backtest.sh's EXIT trap, regardless of
        whether the run was invoked via run_lean_backtest tool or shell_exec.
        """
        jsonl = self._jsonl_path()
        if not jsonl.exists():
            return 0
        with open(jsonl) as f:
            return sum(1 for line in f if line.strip())

    def snapshot_and_record(
        self,
        trial_id: int,
        status: str,
        algo_path: Optional[str] = None,
        metrics: Optional[dict] = None,
    ) -> dict:
        """Snapshot workspace files and record trial metadata.

        Args:
            trial_id: The trial number (1-based).
            status: One of success, compile_error, runtime_error, empty_trades.
            algo_path: Path to the .cs algorithm file (relative to workspace).
            metrics: Pre-computed metrics dict. If None, reads from results/summary.json.

        Returns:
            Dict with trial metadata.
        """
        trial_dir = self._trials_dir / f"trial_{trial_id}"
        snapshot_dir = trial_dir / "snapshot"
        results_dir = trial_dir / "results"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        results_dir.mkdir(parents=True, exist_ok=True)

        # Snapshot .cs files from workspace root
        for f in self.workspace.glob("*.cs"):
            shutil.copy2(f, snapshot_dir / f.name)

        # Snapshot results/ if present.
        # When --run-id is used, results are in a subdirectory (e.g. results/sma_v1/).
        # Copy files from both the base results/ dir and any immediate subdirectories.
        ws_results = self.workspace / "results"
        if ws_results.is_dir():
            for f in ws_results.iterdir():
                if f.is_file():
                    shutil.copy2(f, results_dir / f.name)
                elif f.is_dir() and not f.name.startswith("."):
                    # --run-id subdirectory: copy its files into flat trial results
                    for sf in f.iterdir():
                        if sf.is_file() and not (results_dir / sf.name).exists():
                            shutil.copy2(sf, results_dir / sf.name)

        # Extract metrics from summary.json if not provided
        if metrics is None:
            metrics = self._read_summary(results_dir)

        trial_meta = {
            "trial_id": trial_id,
            "status": status,
            "algo_path": algo_path,
            "metrics": metrics,
        }

        # Update manifest
        manifest = self._load_manifest()
        manifest["trials"][str(trial_id)] = trial_meta
        self._save_manifest(manifest)

        return trial_meta

    def select(self, trial_id: int) -> str:
        """Select a trial for final evaluation.

        For tool-invoked trials: copies the trial's snapshot results/ back
        to /workspace/results/.
        For shell_exec-imported trials (no snapshot): marks as selected
        without copying (workspace already has the most recent results).

        Returns:
            Confirmation message.
        """
        manifest = self._load_manifest()
        trial_key = str(trial_id)
        if trial_key not in manifest["trials"]:
            return f"Error: trial {trial_id} does not exist"

        meta = manifest["trials"][trial_key]
        trial_results = self._trials_dir / f"trial_{trial_id}" / "results"

        if trial_results.is_dir():
            # Tool-invoked trial: restore snapshot
            ws_results = self.workspace / "results"
            if ws_results.exists():
                shutil.rmtree(ws_results)
            shutil.copytree(trial_results, ws_results)

            # Also restore .cs snapshot so code inspection sees the selected version
            snapshot_dir = self._trials_dir / f"trial_{trial_id}" / "snapshot"
            if snapshot_dir.is_dir():
                for f in snapshot_dir.glob("*.cs"):
                    shutil.copy2(f, self.workspace / f.name)

            manifest["selected_trial"] = trial_id
            self._save_manifest(manifest)
            return (
                f"Selected trial {trial_id} (status={meta.get('status', 'unknown')}). "
                f"Results copied to /workspace/results/."
            )
        else:
            # Shell_exec-imported trial: no snapshot available.
            # Mark as selected; workspace already has results from the last run.
            manifest["selected_trial"] = trial_id
            self._save_manifest(manifest)
            return (
                f"Selected trial {trial_id} (status={meta.get('status', 'unknown')}, "
                f"source={meta.get('source', 'unknown')}). "
                f"No snapshot to restore — using current workspace results."
            )

    def auto_select(self) -> Optional[int]:
        """Auto-select the best trial if none was manually selected.

        First imports any untracked runs from .backtest_runs.jsonl so that
        shell_exec-invoked backtests are considered alongside tool-invoked ones.

        Priority:
        1. Among successful trials with trades > 0, pick highest Sharpe.
        2. Tie-break: earlier trial (fewer resources used).
        3. Fallback: last trial.

        Returns:
            Selected trial_id, or None if no trials exist.
        """
        # Sync shell_exec runs into manifest before selection
        self._import_untracked_runs()

        manifest = self._load_manifest()
        if not manifest["trials"]:
            return None

        if manifest.get("selected_trial"):
            return manifest["selected_trial"]

        candidates = []
        for tid_str, meta in manifest["trials"].items():
            tid = int(tid_str)
            metrics = meta.get("metrics", {})
            status = meta.get("status", "")
            trades = metrics.get("total_trades", 0)
            sharpe = metrics.get("sharpe_ratio", float("-inf"))

            if status == "success" and trades > 0:
                candidates.append((sharpe, -tid, tid))  # higher Sharpe, earlier trial

        if candidates:
            candidates.sort(reverse=True)
            best_tid = candidates[0][2]
        else:
            # Fallback: last trial
            best_tid = max(int(k) for k in manifest["trials"])

        self.select(best_tid)
        return best_tid

    def get_status(self) -> dict:
        """Return full trial status for display to the agent."""
        # Sync untracked runs first so status reflects ALL runs
        self._import_untracked_runs()
        all_runs = self.count_all_runs()
        manifest = self._load_manifest()
        return {
            "max_trials": self.max_trials,
            "trials_used": all_runs,
            "trials_remaining": self.max_trials - all_runs,
            "selected_trial": manifest.get("selected_trial"),
            "trials": manifest["trials"],
        }

    # ── Internal helpers ────────────────────────────────────────

    def _jsonl_path(self) -> Path:
        """Path to the run log written by run_backtest.sh EXIT trap."""
        return self.workspace / ".backtest_runs.jsonl"

    def _import_untracked_runs(self) -> None:
        """Sync .backtest_runs.jsonl entries into the manifest.

        Shell_exec-invoked runs are recorded in the JSONL but not in the
        manifest. This method imports them so that auto_select and
        get_status see the complete picture.
        """
        jsonl = self._jsonl_path()
        if not jsonl.exists():
            return

        manifest = self._load_manifest()
        existing_ids = set(manifest["trials"].keys())

        with open(jsonl) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                run_num = str(record.get("run", 0))
                if run_num in existing_ids or run_num == "0":
                    continue

                exit_code = record.get("exit_code", -1)
                status_map = {
                    0: "success",
                    2: "compile_error",
                    3: "runtime_error",
                    124: "timeout",
                }
                status = status_map.get(exit_code, "runtime_error")

                raw_metrics = record.get("metrics", {})
                metrics = self._normalize_jsonl_metrics(raw_metrics)

                # For successful runs with empty metrics, try reading from results dir
                if status == "success" and not metrics:
                    results_dir = record.get("results_dir", "")
                    if results_dir:
                        rpath = Path(results_dir)
                        # Try workspace-relative path if absolute doesn't exist
                        if not rpath.exists():
                            rpath = self.workspace / results_dir.lstrip("/")
                        metrics = self._read_summary(rpath)

                # Override: if compile/runtime error, force empty metrics
                if status in ("compile_error", "runtime_error", "timeout"):
                    metrics = {}

                # Check trade count to distinguish success vs empty_trades
                if status == "success" and metrics.get("total_trades", 0) == 0:
                    status = "empty_trades"

                manifest["trials"][run_num] = {
                    "trial_id": int(run_num),
                    "status": status,
                    "algo_path": record.get("algo", ""),
                    "metrics": metrics,
                    "source": "shell_exec",
                }

        manifest["trials_used"] = max(
            len(manifest["trials"]),
            self.count_all_runs(),
        )
        self._save_manifest(manifest)

    @staticmethod
    def _normalize_jsonl_metrics(raw: dict) -> dict:
        """Convert JSONL metric keys to the manifest format."""
        if not raw:
            return {}

        def _parse_num(v, as_int=False):
            if isinstance(v, (int, float)):
                return int(v) if as_int else float(v)
            try:
                cleaned = str(v).replace("%", "").replace(",", "").strip()
                return int(cleaned) if as_int else float(cleaned)
            except (ValueError, TypeError):
                return 0 if as_int else 0.0

        return {
            "sharpe_ratio": _parse_num(raw.get("sharpe", 0)),
            "total_return_pct": _parse_num(raw.get("net_profit", "0%")),
            "total_trades": _parse_num(raw.get("trades", 0), as_int=True),
        }

    def _load_manifest(self) -> dict:
        """Load or initialize the manifest with file locking."""
        self._trials_dir.mkdir(parents=True, exist_ok=True)

        if not self._manifest_path.exists():
            return {
                "max_trials": self.max_trials,
                "trials_used": 0,
                "trials": {},
                "selected_trial": None,
            }

        with open(self._manifest_path, "r") as f:
            fcntl.flock(f, fcntl.LOCK_SH)
            try:
                data = json.load(f)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
        return data

    def _save_manifest(self, manifest: dict) -> None:
        """Save manifest with exclusive file lock."""
        self._trials_dir.mkdir(parents=True, exist_ok=True)

        with open(self._manifest_path, "w") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                json.dump(manifest, f, indent=2)
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)

    def _read_summary(self, results_dir: Path) -> dict:
        """Extract key metrics from summary.json in a results directory."""
        summary_path = results_dir / "summary.json"
        if not summary_path.exists():
            return {}

        try:
            with open(summary_path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}

        # Handle nested statistics key
        stats = data
        if "Statistics" in stats:
            stats = stats["Statistics"]
        elif "statistics" in stats:
            stats = stats["statistics"]

        def _pct(s):
            if isinstance(s, (int, float)):
                return float(s)
            return float(str(s).replace("%", "").strip() or "0")

        try:
            sharpe = float(stats.get("Sharpe Ratio", stats.get("sharpe_ratio", 0)))
        except (ValueError, TypeError):
            sharpe = 0.0

        total_return = stats.get("Net Profit", stats.get("total_return", "0%"))
        total_trades_str = stats.get(
            "Total Trades", stats.get("Total Orders", stats.get("total_trades", "0"))
        )
        try:
            total_trades = int(total_trades_str)
        except (ValueError, TypeError):
            total_trades = 0

        return {
            "sharpe_ratio": sharpe,
            "total_return_pct": _pct(total_return),
            "total_trades": total_trades,
        }
