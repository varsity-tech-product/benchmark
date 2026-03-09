#!/usr/bin/env python3
"""Generate reference trade logs by running LEAN algorithms in Docker.

Usage:
    python generate_lean_reference.py --task I02
    python generate_lean_reference.py --task I03 --lean-image quantconnect/lean:latest
    python generate_lean_reference.py --task all

This script:
1. Locates the reference C# algorithm for the given task (bench/reference/lean_algorithms/)
2. Spins up a LEAN Docker container with the algorithm and market data
3. Runs the backtest
4. Parses the LEAN output for trade logs
5. Exports structured JSON to bench/data/reference/<task>_reference_trades.json

Prerequisites:
- Docker must be installed and running
- LEAN market data must be available (via data_manager.py or manual download)
- The LEAN Docker image must be pulled (default: quantconnect/lean:latest)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

# Resolve paths relative to this script
SCRIPT_DIR = Path(__file__).parent
BENCH_ROOT = SCRIPT_DIR.parent
ALGO_DIR = SCRIPT_DIR / "lean_algorithms"
REFERENCE_OUTPUT_DIR = BENCH_ROOT / "data" / "reference"

# Task → algorithm file mapping
TASK_ALGO_MAP = {
    "I02": "I02_trend_following.cs",
    "I03": "I03_mean_reversion.cs",
    "I04": "I04_multi_timeframe.cs",
    "I05": "I05_cross_asset.cs",
    "I06": "I06_multi_signal.cs",
}

DEFAULT_LEAN_IMAGE = "quantconnect/lean:latest"
DEFAULT_START_DATE = "2023-01-01"
DEFAULT_END_DATE = "2023-12-31"


def _check_docker():
    """Verify Docker is available."""
    try:
        result = subprocess.run(
            ["docker", "info"], capture_output=True, timeout=10
        )
        if result.returncode != 0:
            print("ERROR: Docker is not running. Please start Docker first.")
            sys.exit(1)
    except FileNotFoundError:
        print("ERROR: Docker is not installed.")
        sys.exit(1)


def _ensure_lean_data():
    """Ensure LEAN market data is available. Returns data directory path."""
    # Try to use data_manager if available
    try:
        sys.path.insert(0, str(BENCH_ROOT.parent))
        from scripts.data_manager import ensure_data
        paths = ensure_data(series="i")
        return paths.lean_data
    except (ImportError, Exception) as e:
        print(f"WARNING: Could not load data via data_manager: {e}")

    # Fallback: check common locations
    candidates = [
        Path.home() / ".cache" / "quanttutorbench" / "lean_data",
        Path("/tmp/lean_data"),
        BENCH_ROOT / "data" / "lean",
    ]
    for path in candidates:
        if path.is_dir() and any(path.iterdir()):
            print(f"Using LEAN data from: {path}")
            return str(path)

    print("ERROR: No LEAN market data found. Run scripts/data_manager.py first.")
    sys.exit(1)


def _build_lean_config(algo_name: str, start_date: str, end_date: str) -> dict:
    """Build a minimal LEAN configuration for the backtest."""
    return {
        "environment": "backtesting",
        "algorithm-type-name": algo_name,
        "algorithm-language": "CSharp",
        "algorithm-location": f"/Lean/Algorithm.CSharp/{algo_name}.cs",
        "parameters": {},
        "data-folder": "/Lean/Data",
        "results-destination-folder": "/Results",
        "log-handler": "QuantConnect.Logging.CompositeLogHandler",
        "messaging-handler": "QuantConnect.Messaging.Messaging",
        "job-queue-handler": "QuantConnect.Queues.JobQueue",
        "api-handler": "QuantConnect.Api.Api",
        "start-date": start_date,
        "end-date": end_date,
    }


def _parse_trade_logs(results_dir: str) -> list[dict]:
    """Parse LEAN backtest results to extract trade logs.

    Looks for:
    1. The orders JSON in the results file
    2. Log entries matching the TRADE: pattern from OnOrderEvent
    """
    trades = []

    # Look for the main results JSON
    results_files = list(Path(results_dir).glob("*.json"))
    for rf in results_files:
        try:
            with open(rf) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        # Extract from Orders section
        orders = data.get("Orders", {})
        for order_id, order in orders.items():
            if order.get("Status") != 3:  # 3 = Filled
                continue
            trades.append({
                "order_id": int(order_id),
                "symbol": order.get("Symbol", {}).get("Value", ""),
                "direction": "Buy" if order.get("Direction") == 0 else "Sell",
                "quantity": order.get("Quantity", 0),
                "fill_price": order.get("Price", 0.0),
                "time": order.get("Time", ""),
                "tag": order.get("Tag", ""),
                "type": order.get("Type", ""),
            })

        # Also extract from log entries as backup
        logs = data.get("Logs", [])
        if isinstance(logs, list):
            for log_entry in logs:
                log_text = log_entry if isinstance(log_entry, str) else str(log_entry)
                if "TRADE:" in log_text:
                    trades.append({"raw_log": log_text})

    return trades


def _parse_performance_metrics(results_dir: str) -> dict:
    """Extract key performance metrics from LEAN results."""
    metrics = {}
    results_files = list(Path(results_dir).glob("*.json"))
    for rf in results_files:
        try:
            with open(rf) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        stats = data.get("Statistics", {})
        if stats:
            metrics = {
                "total_trades": stats.get("Total Trades", 0),
                "sharpe_ratio": stats.get("Sharpe Ratio", 0.0),
                "total_return": stats.get("Net Profit", "0%"),
                "max_drawdown": stats.get("Drawdown", "0%"),
                "win_rate": stats.get("Win Rate", "0%"),
                "profit_loss_ratio": stats.get("Profit-Loss Ratio", 0.0),
                "annual_return": stats.get("Compounding Annual Return", "0%"),
            }
            break

    return metrics


def run_lean_backtest(
    task_id: str,
    lean_image: str = DEFAULT_LEAN_IMAGE,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    dry_run: bool = False,
) -> dict:
    """Run a LEAN backtest for the given task and return structured results.

    Args:
        task_id: Task identifier (e.g., "I02")
        lean_image: Docker image for LEAN engine
        start_date: Backtest start date (YYYY-MM-DD)
        end_date: Backtest end date (YYYY-MM-DD)
        dry_run: If True, only validate setup without running

    Returns:
        Dict with trades, metrics, and metadata
    """
    task_id = task_id.upper()
    if task_id not in TASK_ALGO_MAP:
        raise ValueError(
            f"Unknown task: {task_id}. Valid tasks: {list(TASK_ALGO_MAP.keys())}"
        )

    algo_file = ALGO_DIR / TASK_ALGO_MAP[task_id]
    if not algo_file.exists():
        raise FileNotFoundError(f"Algorithm file not found: {algo_file}")

    algo_name = algo_file.stem  # e.g., "I02_trend_following"
    # LEAN expects the class name, which uses PascalCase
    class_name_map = {
        "I02_trend_following": "I02TrendFollowing",
        "I03_mean_reversion": "I03MeanReversion",
        "I04_multi_timeframe": "I04MultiTimeframe",
        "I05_cross_asset": "I05CrossAsset",
        "I06_multi_signal": "I06MultiSignal",
    }
    class_name = class_name_map.get(algo_name, algo_name)

    lean_data_dir = _ensure_lean_data()

    if dry_run:
        print(f"DRY RUN: Would run {class_name} from {algo_file}")
        print(f"  LEAN image: {lean_image}")
        print(f"  Data dir:   {lean_data_dir}")
        print(f"  Period:     {start_date} → {end_date}")
        return {"status": "dry_run", "task_id": task_id}

    # Set up temporary directories for the run
    with tempfile.TemporaryDirectory(prefix=f"lean_{task_id}_") as tmpdir:
        results_dir = os.path.join(tmpdir, "results")
        config_dir = os.path.join(tmpdir, "config")
        algo_mount_dir = os.path.join(tmpdir, "algorithm")
        os.makedirs(results_dir)
        os.makedirs(config_dir)
        os.makedirs(algo_mount_dir)

        # Copy algorithm file
        shutil.copy2(str(algo_file), os.path.join(algo_mount_dir, algo_file.name))

        # Copy universe.json if available
        universe_src = BENCH_ROOT / "data" / "frozen" / "universe.json"
        if universe_src.exists():
            shutil.copy2(str(universe_src), os.path.join(algo_mount_dir, "universe.json"))

        # Write LEAN config
        config = _build_lean_config(class_name, start_date, end_date)
        config_path = os.path.join(config_dir, "config.json")
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        # Run LEAN in Docker
        cmd = [
            "docker", "run", "--rm",
            "-v", f"{lean_data_dir}:/Lean/Data:ro",
            "-v", f"{algo_mount_dir}:/Lean/Algorithm.CSharp:ro",
            "-v", f"{results_dir}:/Results",
            "-v", f"{config_path}:/Lean/Launcher/config.json:ro",
            "--cpus", "2",
            "--memory", "4g",
            lean_image,
        ]

        print(f"Running LEAN backtest for {task_id} ({class_name})...")
        print(f"  Command: {' '.join(cmd)}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout
            )

            if result.returncode != 0:
                print(f"  LEAN exited with code {result.returncode}")
                print(f"  stderr: {result.stderr[:1000]}")
            else:
                print(f"  LEAN completed successfully")

        except subprocess.TimeoutExpired:
            print(f"  ERROR: LEAN backtest timed out after 600s")
            return {
                "status": "timeout",
                "task_id": task_id,
                "error": "Backtest timed out",
            }

        # Parse results
        trades = _parse_trade_logs(results_dir)
        metrics = _parse_performance_metrics(results_dir)

        output = {
            "task_id": task_id,
            "algorithm": class_name,
            "algorithm_file": TASK_ALGO_MAP[task_id],
            "start_date": start_date,
            "end_date": end_date,
            "lean_image": lean_image,
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "status": "success" if trades else "no_trades",
            "metrics": metrics,
            "trades": trades,
            "trade_count": len(trades),
        }

        # Save to reference directory
        REFERENCE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        output_path = REFERENCE_OUTPUT_DIR / f"{task_id}_reference_trades.json"
        with open(output_path, "w") as f:
            json.dump(output, f, indent=2)

        print(f"  Saved {len(trades)} trades to {output_path}")
        return output


def main():
    parser = argparse.ArgumentParser(
        description="Generate reference trade logs from LEAN backtests"
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Task ID (I02-I06) or 'all' to run all tasks",
    )
    parser.add_argument(
        "--lean-image",
        default=DEFAULT_LEAN_IMAGE,
        help=f"LEAN Docker image (default: {DEFAULT_LEAN_IMAGE})",
    )
    parser.add_argument(
        "--start-date",
        default=DEFAULT_START_DATE,
        help=f"Backtest start date (default: {DEFAULT_START_DATE})",
    )
    parser.add_argument(
        "--end-date",
        default=DEFAULT_END_DATE,
        help=f"Backtest end date (default: {DEFAULT_END_DATE})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate setup without running LEAN",
    )

    args = parser.parse_args()

    _check_docker()

    if args.task.lower() == "all":
        tasks = list(TASK_ALGO_MAP.keys())
    else:
        tasks = [args.task.upper()]

    results = {}
    for task_id in tasks:
        try:
            result = run_lean_backtest(
                task_id=task_id,
                lean_image=args.lean_image,
                start_date=args.start_date,
                end_date=args.end_date,
                dry_run=args.dry_run,
            )
            results[task_id] = result
            status = result.get("status", "unknown")
            trade_count = result.get("trade_count", 0)
            print(f"  {task_id}: status={status}, trades={trade_count}")
        except Exception as e:
            print(f"  {task_id}: FAILED - {e}")
            results[task_id] = {"status": "error", "error": str(e)}

    # Summary
    print("\n" + "=" * 60)
    print("Reference Generation Summary:")
    for task_id, result in results.items():
        status = result.get("status", "unknown")
        trades = result.get("trade_count", 0)
        print(f"  {task_id}: {status} ({trades} trades)")
    print("=" * 60)


if __name__ == "__main__":
    main()
