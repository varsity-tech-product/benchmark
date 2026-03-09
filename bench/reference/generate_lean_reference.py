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
    "I01": "I01_implement_sma.cs",
    "I02": "I02_trend_following.cs",
    "I03": "I03_mean_reversion.cs",
    "I04": "I04_multi_timeframe.cs",
    "I05": "I05_cross_asset.cs",
    "I06": "I06_multi_signal.cs",
}

DEFAULT_LEAN_IMAGE = "quantconnect/lean:latest"
DEFAULT_START_DATE = "2024-02-01"
DEFAULT_END_DATE = "2024-12-31"


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
        sys.path.insert(0, str(BENCH_ROOT))
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


def _build_lean_config(class_name: str, algo_filename: str, start_date: str, end_date: str) -> dict:
    """Build a minimal LEAN configuration for the backtest."""
    return {
        "environment": "backtesting",
        "algorithm-type-name": class_name,
        "algorithm-language": "CSharp",
        "algorithm-location": f"/Lean/Algorithm.CSharp/{algo_filename}",
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


def _ci_get(d: dict, key: str, default=None):
    """Case-insensitive dict get — handles both PascalCase and camelCase LEAN output."""
    for k in (key, key[0].lower() + key[1:], key.lower()):
        if k in d:
            return d[k]
    return default


def _parse_filled_orders(results_dir: str) -> list[dict]:
    """Parse filled orders from LEAN backtest results JSON."""
    orders_list = []

    results_files = list(Path(results_dir).glob("*.json"))
    for rf in results_files:
        try:
            with open(rf) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue

        orders = _ci_get(data, "Orders", {})
        if not isinstance(orders, dict):
            continue
        for order_id, order in orders.items():
            if _ci_get(order, "Status") != 3:  # 3 = Filled
                continue
            sym_obj = _ci_get(order, "Symbol", {})
            sym_value = _ci_get(sym_obj, "Value", "") if isinstance(sym_obj, dict) else str(sym_obj)
            orders_list.append({
                "order_id": int(order_id),
                "symbol": sym_value,
                "direction": "Buy" if _ci_get(order, "Direction") == 0 else "Sell",
                "quantity": abs(_ci_get(order, "Quantity", 0)),
                "fill_price": _ci_get(order, "Price", 0.0),
                "time": _ci_get(order, "Time", ""),
                "tag": _ci_get(order, "Tag", ""),
            })

    return orders_list


def _pair_round_trip_trades(orders: list[dict]) -> list[dict]:
    """Pair filled orders into round-trip trades (FIFO per symbol).

    For each symbol, pairs entry (Buy) and exit (Sell) orders into
    round-trip records with entry_time, exit_time, direction, net_pnl —
    the schema expected by match_trades() in _implementation_check.py.
    """
    from collections import defaultdict

    # Group orders by symbol, sorted by time
    by_symbol: dict[str, list[dict]] = defaultdict(list)
    for o in orders:
        by_symbol[o["symbol"]].append(o)
    for sym in by_symbol:
        by_symbol[sym].sort(key=lambda o: o["time"])

    trades = []
    for symbol, sym_orders in by_symbol.items():
        pending_entry = None
        for o in sym_orders:
            if pending_entry is None:
                # First order for this symbol = entry
                pending_entry = o
            else:
                # Opposite direction = exit → close round-trip
                if o["direction"] != pending_entry["direction"]:
                    entry_price = pending_entry["fill_price"]
                    exit_price = o["fill_price"]
                    qty = min(pending_entry["quantity"], o["quantity"])
                    if pending_entry["direction"] == "Buy":
                        gross_pnl = (exit_price - entry_price) * qty
                    else:
                        gross_pnl = (entry_price - exit_price) * qty

                    trades.append({
                        "symbol": symbol,
                        "direction": pending_entry["direction"],
                        "quantity": qty,
                        "entry_time": pending_entry["time"],
                        "entry_price": entry_price,
                        "exit_time": o["time"],
                        "exit_price": exit_price,
                        "gross_pnl": gross_pnl,
                        "net_pnl": gross_pnl,  # no fee model in reference
                        "entry_tag": pending_entry.get("tag", ""),
                        "exit_tag": o.get("tag", ""),
                    })
                    pending_entry = None
                else:
                    # Same direction again — replace entry (scaling in)
                    pending_entry = o

    return trades


def _parse_trade_logs(results_dir: str) -> list[dict]:
    """Parse LEAN backtest results into round-trip trade records.

    Extracts filled orders from the LEAN results JSON, then pairs them
    into round-trip trades with entry_time/exit_time/direction/net_pnl
    to match the schema expected by match_trades().
    """
    orders = _parse_filled_orders(results_dir)
    return _pair_round_trip_trades(orders)


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

        stats = _ci_get(data, "Statistics", {})
        if stats:
            # LEAN uses "Total Orders" (string), not "Total Trades" (int)
            total_orders_str = stats.get("Total Orders", stats.get("Total Trades", "0"))
            try:
                total_orders = int(total_orders_str)
            except (ValueError, TypeError):
                total_orders = 0
            metrics = {
                "total_orders": total_orders,
                "sharpe_ratio": stats.get("Sharpe Ratio", "0"),
                "total_return": stats.get("Net Profit", "0%"),
                "max_drawdown": stats.get("Drawdown", "0%"),
                "win_rate": stats.get("Win Rate", "0%"),
                "profit_loss_ratio": stats.get("Profit-Loss Ratio", "0"),
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
    # LEAN expects fully-qualified class name (Namespace.Class)
    class_name_map = {
        "I01_implement_sma": "QuantTutorBench.I01ImplementSma",
        "I02_trend_following": "QuantTutorBench.I02TrendFollowing",
        "I03_mean_reversion": "QuantTutorBench.I03MeanReversion",
        "I04_multi_timeframe": "QuantTutorBench.I04MultiTimeframe",
        "I05_cross_asset": "QuantTutorBench.I05CrossAsset",
        "I06_multi_signal": "QuantTutorBench.I06MultiSignal",
    }
    class_name = class_name_map.get(algo_name, algo_name)

    lean_data_dir = _ensure_lean_data()

    if dry_run:
        print(f"DRY RUN: Would run {class_name} from {algo_file}")
        print(f"  LEAN image: {lean_image}")
        print(f"  Data dir:   {lean_data_dir}")
        print(f"  Period:     {start_date} → {end_date}")
        return {"status": "dry_run", "task_id": task_id}

    # Set up temporary directories for the run.
    # Docker creates files as root, so use shutil.rmtree with onerror for cleanup.
    tmpdir = tempfile.mkdtemp(prefix=f"lean_{task_id}_")
    try:
        _tmpdir_ref = tmpdir  # keep reference for finally block
        results_dir = os.path.join(tmpdir, "results")
        config_dir = os.path.join(tmpdir, "config")
        algo_mount_dir = os.path.join(tmpdir, "algorithm")
        os.makedirs(results_dir)
        os.makedirs(config_dir)
        os.makedirs(algo_mount_dir)

        # Copy algorithm file and create .csproj for compilation
        shutil.copy2(str(algo_file), os.path.join(algo_mount_dir, algo_file.name))

        # Write .csproj that references LEAN assemblies in the Docker image
        csproj_content = """\
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net10.0</TargetFramework>
    <OutputType>Library</OutputType>
    <EnableDefaultCompileItems>true</EnableDefaultCompileItems>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="QuantConnect.Algorithm">
      <HintPath>/Lean/Launcher/bin/Debug/QuantConnect.Algorithm.dll</HintPath>
    </Reference>
    <Reference Include="QuantConnect.Common">
      <HintPath>/Lean/Launcher/bin/Debug/QuantConnect.Common.dll</HintPath>
    </Reference>
    <Reference Include="QuantConnect.Indicators">
      <HintPath>/Lean/Launcher/bin/Debug/QuantConnect.Indicators.dll</HintPath>
    </Reference>
    <Reference Include="Newtonsoft.Json">
      <HintPath>/Lean/Launcher/bin/Debug/Newtonsoft.Json.dll</HintPath>
    </Reference>
    <Reference Include="Python.Runtime">
      <HintPath>/Lean/Launcher/bin/Debug/Python.Runtime.dll</HintPath>
    </Reference>
  </ItemGroup>
</Project>
"""
        with open(os.path.join(algo_mount_dir, "CustomAlgo.csproj"), "w") as f:
            f.write(csproj_content)

        # Copy flat universe.json if available
        flat_universe = BENCH_ROOT / "data" / "lean_universe.json"
        structured_universe = BENCH_ROOT / "data" / "universe.json"
        if not flat_universe.exists() and structured_universe.exists():
            symbols = [
                sym["symbol"]
                for sym in json.loads(structured_universe.read_text())["tiers"]["tier1"]["symbols"]
            ]
            flat_universe.write_text(json.dumps(symbols, indent=2))
        if flat_universe.exists():
            shutil.copy2(str(flat_universe), os.path.join(algo_mount_dir, "universe.json"))

        # Write LEAN config — point to compiled DLL
        dll_path = "/CustomAlgo/bin/Debug/net10.0/CustomAlgo.dll"
        config = _build_lean_config(class_name, algo_file.name, start_date, end_date)
        config["algorithm-location"] = dll_path
        config_path = os.path.join(config_dir, "config.json")
        with open(config_path, "w") as f:
            json.dump(config, f, indent=2)

        # Build data mounts — mount individual subdirs to preserve LEAN's
        # built-in metadata (symbol-properties, market-hours).
        data_mounts = []
        lean_data = Path(lean_data_dir)
        for child in lean_data.iterdir():
            if child.name == "universe.json":
                data_mounts += ["-v", f"{child}:/Lean/Data/universe.json:ro"]
            elif child.is_dir() or child.is_symlink():
                data_mounts += ["-v", f"{child.resolve()}:/Lean/Data/{child.name}"]

        # Compile C# algorithm inside the container, then run LEAN
        build_and_run = (
            "cd /CustomAlgo && "
            "dotnet build -c Debug --nologo -v q 2>&1 && "
            "cd /Lean/Launcher/bin/Debug && "
            "dotnet QuantConnect.Lean.Launcher.dll"
        )

        cmd = [
            "docker", "run", "--rm",
            *data_mounts,
            "-v", f"{algo_mount_dir}:/CustomAlgo",
            "-v", f"{results_dir}:/Results",
            "-v", f"{config_path}:/Lean/Launcher/bin/Debug/config.json:ro",
            "--cpus", "2",
            "--memory", "4g",
            "--entrypoint", "bash",
            lean_image,
            "-c", build_and_run,
        ]

        print(f"Running LEAN backtest for {task_id} ({class_name})...")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=600,  # 10 minute timeout
            )

            if result.returncode != 0:
                print(f"  LEAN exited with code {result.returncode}")
                # Show build errors or LEAN errors
                output_text = result.stdout + result.stderr
                if "Build FAILED" in output_text:
                    for line in output_text.split("\n"):
                        if "error CS" in line:
                            print(f"  {line.strip()}")
                else:
                    print(f"  stderr: {result.stderr[:1000]}")
            else:
                # Show key stats from stdout
                for line in result.stdout.split("\n"):
                    if "STATISTICS:: Total Orders" in line or "STATISTICS:: Net Profit" in line:
                        print(f"  {line.strip()}")
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
        # Set total_trades from actual paired trade count (not LEAN's "Total Orders")
        metrics["total_trades"] = len(trades)

        output = {
            "task_id": task_id,
            "algorithm": class_name,
            "algorithm_file": TASK_ALGO_MAP[task_id],
            "start_date": start_date,
            "end_date": end_date,
            "lean_image": lean_image,
            "generated_at": datetime.now(tz=__import__('datetime').timezone.utc).isoformat(),
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

        # Also generate reference positions and summary for behavioral eval
        try:
            from generate_reference_signals import generate as gen_signals
            gen_signals(task_id, start_date, end_date)
        except Exception as e:
            print(f"  WARNING: Could not generate reference signals: {e}")

        return output
    finally:
        # Docker creates files as root — need elevated cleanup
        try:
            subprocess.run(
                ["docker", "run", "--rm", "-v", f"{_tmpdir_ref}:/cleanup",
                 "alpine", "rm", "-rf", "/cleanup"],
                timeout=30, capture_output=True,
            )
        except Exception:
            pass
        try:
            shutil.rmtree(_tmpdir_ref, ignore_errors=True)
        except Exception:
            pass


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
