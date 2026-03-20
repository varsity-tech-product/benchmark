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
import os
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
        """Return True if there are remaining trials in the budget."""
        manifest = self._load_manifest()
        return manifest["trials_used"] < self.max_trials

    def next_trial_id(self) -> int:
        """Allocate and return the next trial ID (1-based).

        Increments the trial counter atomically.
        """
        manifest = self._load_manifest()
        trial_id = manifest["trials_used"] + 1
        manifest["trials_used"] = trial_id
        self._save_manifest(manifest)
        return trial_id

    def trials_used(self) -> int:
        """Return the number of trials consumed so far."""
        return self._load_manifest()["trials_used"]

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

        # Snapshot results/ if present — copy files from workspace/results/
        # and also from any run-id subdirectory (run_lean_backtest uses --run-id).
        ws_results = self.workspace / "results"
        if ws_results.is_dir():
            for item in ws_results.iterdir():
                if item.is_file():
                    shutil.copy2(item, results_dir / item.name)
                elif item.is_dir():
                    # Copy files from run-id subdirs (e.g. results/sma_universe_v2/)
                    for f in item.iterdir():
                        if f.is_file():
                            shutil.copy2(f, results_dir / f.name)

        # Extract metrics from summary.json if not provided
        if metrics is None:
            metrics = self._read_summary(results_dir)
            # Fallback: try *-summary.json (LEAN names it Algorithm-summary.json)
            if not metrics:
                for f in results_dir.iterdir():
                    if f.name.endswith("-summary.json"):
                        metrics = self._read_summary_from(f)
                        if metrics:
                            break

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

        Copies the trial's results/ back to /workspace/results/ and records
        the selection in the manifest.

        Returns:
            Confirmation message.
        """
        manifest = self._load_manifest()
        trial_key = str(trial_id)
        if trial_key not in manifest["trials"]:
            return f"Error: trial {trial_id} does not exist"

        trial_results = self._trials_dir / f"trial_{trial_id}" / "results"
        if not trial_results.is_dir():
            return f"Error: trial {trial_id} has no results directory"

        # Copy results to workspace
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

        meta = manifest["trials"][trial_key]
        return (
            f"Selected trial {trial_id} (status={meta.get('status', 'unknown')}). "
            f"Results copied to /workspace/results/."
        )

    def auto_select(self) -> Optional[int]:
        """Auto-select the best trial if none was manually selected.

        Priority:
        1. Among successful trials with trades > 0, pick highest Sharpe.
        2. Tie-break: earlier trial (fewer resources used).
        3. Fallback: last trial.

        Returns:
            Selected trial_id, or None if no trials exist.
        """
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
        manifest = self._load_manifest()
        return {
            "max_trials": self.max_trials,
            "trials_used": manifest["trials_used"],
            "trials_remaining": self.max_trials - manifest["trials_used"],
            "selected_trial": manifest.get("selected_trial"),
            "trials": manifest["trials"],
        }

    # ── Internal helpers ────────────────────────────────────────

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
        return self._read_summary_from(summary_path)

    def _read_summary_from(self, summary_path: Path) -> dict:
        """Extract key metrics from a specific summary JSON file."""
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
