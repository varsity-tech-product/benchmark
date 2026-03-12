#!/usr/bin/env python3
"""Generate reference trade logs by running LEAN algorithms in Docker.

Usage:
    python generate_lean_reference.py --task I02
    python generate_lean_reference.py --task I03 --lean-image quant-tutor-env:v2.2-lean
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
- The LEAN Docker image must be built locally (default: quant-tutor-env:v2.2-lean)
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
    "I07": "I07_alpha_model.cs",
    "I08": "I08_multi_alpha.cs",
    "I09": "I09_risk_management.cs",
    "I10": "I10_parameter_optimization.cs",
    "X07": "X07_warmup_fixed.cs",
    "X08": "X08_order_type_fixed.cs",
    "X09": "X09_alpha_conflict_fixed.cs",
    "X10": "X10_universe_stale_fixed.cs",
    "E02": "E02_bb_reversion.cs",
    "E04": "E04_compound_fixed.cs",
    "E05": "E05_momentum_topn.cs",
}

# Import canonical config
sys.path.insert(0, str(BENCH_ROOT))
from config.benchmark_config import LEAN_IMAGE, DATASET_REVISION  # noqa: E402
from config.benchmark_dates import BENCH_START, BENCH_END  # noqa: E402

DEFAULT_LEAN_IMAGE = LEAN_IMAGE
DEFAULT_START_DATE = BENCH_START
DEFAULT_END_DATE = BENCH_END


def _generate_i06_sweep() -> list[dict]:
    """Generate I06 weight sweep grid (configs where weights sum to 1.0).

    trend_weight:     0.2 to 0.6 step 0.1
    reversion_weight: 0.1 to 0.5 step 0.1
    carry_weight:     1.0 - trend - reversion, must be in [0.1, 0.5]
    """
    configs = []
    for tw in [0.2, 0.3, 0.4, 0.5, 0.6]:
        for rw in [0.1, 0.2, 0.3, 0.4, 0.5]:
            cw = round(1.0 - tw - rw, 2)
            if cw < 0.1 or cw > 0.5:
                continue
            run_id = f"t{tw:.1f}_r{rw:.1f}_c{cw:.1f}".replace(".", "")
            configs.append({
                "run_id": run_id,
                "parameters": {
                    "trend_weight": str(tw),
                    "reversion_weight": str(rw),
                    "carry_weight": str(cw),
                },
            })
    return configs


def _generate_i10_grid() -> list[dict]:
    """Generate I10 parameter grid (~180 valid combos where fast < slow)."""
    fast_periods = [5, 10, 15, 20, 25, 30]
    slow_periods = [20, 30, 40, 50, 60, 70, 80, 90, 100]
    thresholds = ["0.0", "0.005", "0.01", "0.015", "0.02"]
    configs = []
    for fast in fast_periods:
        for slow in slow_periods:
            if fast >= slow:
                continue
            for thresh in thresholds:
                run_id = f"f{fast}_s{slow}_t{thresh.replace('.', '')}"
                configs.append({
                    "run_id": run_id,
                    "parameters": {
                        "fast_period": str(fast),
                        "slow_period": str(slow),
                        "signal_threshold": thresh,
                    },
                })
    return configs


# Multi-run configurations for tasks that need multiple backtest runs
TASK_RUN_CONFIGS = {
    "I06": _generate_i06_sweep(),
    "I08": [
        {"run_id": "insight_weighting", "parameters": {"portfolio_model": "InsightWeighting"}},
        {"run_id": "equal_weighting", "parameters": {"portfolio_model": "EqualWeighting"}},
    ],
    "I09": [
        {"run_id": "norisk", "parameters": {"risk_config": "none"}},
        {"run_id": "builtin", "parameters": {"risk_config": "builtin"}},
        {"run_id": "custom", "parameters": {"risk_config": "custom"}},
    ],
    "I10": _generate_i10_grid(),
}


def _compute_sharpe_from_trades(trades: list[dict], risk_free_rate: float = 0.0) -> float:
    """Compute annualized Sharpe ratio from round-trip trade records.

    Groups net_pnl by exit date, computes daily PnL mean/std, annualizes.
    Used as a fallback when LEAN's Sharpe overflows for high-return crypto.
    """
    from collections import defaultdict

    daily_pnl: dict[str, float] = defaultdict(float)
    for t in trades:
        exit_date = t.get("exit_time", "")[:10]  # YYYY-MM-DD
        daily_pnl[exit_date] += float(t.get("net_pnl", 0))

    if len(daily_pnl) < 2:
        return 0.0

    values = list(daily_pnl.values())
    mean_pnl = sum(values) / len(values)
    std_pnl = (sum((v - mean_pnl) ** 2 for v in values) / (len(values) - 1)) ** 0.5
    if std_pnl < 1e-9:
        return 0.0
    return (mean_pnl - risk_free_rate) / std_pnl * (252 ** 0.5)


def _fix_missing_return_drawdown(
    metrics: dict, trades: list[dict], initial_capital: float = 100_000.0
) -> None:
    """Compute total_return and max_drawdown from trades when LEAN didn't output them.

    Uses sum(net_pnl)/initial_capital for return and peak-to-trough for drawdown.
    Only fills in values that are missing from metrics (does not override LEAN's own stats).
    """
    if not trades:
        return

    has_return = "total_return" in metrics
    has_drawdown = "max_drawdown" in metrics

    if has_return and has_drawdown:
        return

    if not has_return:
        total_pnl = sum(float(t.get("net_pnl", 0)) for t in trades)
        total_return_pct = total_pnl / initial_capital * 100
        metrics["total_return"] = f"{total_return_pct:.3f}%"

    if not has_drawdown:
        sorted_trades = sorted(trades, key=lambda t: t.get("exit_time", ""))
        equity = initial_capital
        peak = equity
        max_dd = 0.0
        for t in sorted_trades:
            equity += float(t.get("net_pnl", 0))
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak * 100 if peak > 0 else 0
            if dd > max_dd:
                max_dd = dd
        metrics["max_drawdown"] = f"{max_dd:.3f}%"

    if not has_return or not has_drawdown:
        metrics.setdefault("metrics_source", "computed_from_trades")


def _fix_bogus_sharpe(metrics: dict, trades: list[dict]) -> None:
    """Replace LEAN's Sharpe with trade-derived Sharpe when clearly bogus.

    Triggers when:
    - |sharpe| > 100 (overflow), or
    - sharpe == 0 but there are enough trades for a meaningful calculation
      (LEAN sometimes returns 0 instead of NaN for overflow cases).
    """
    try:
        sharpe = float(metrics.get("sharpe_ratio", "0") or "0")
    except (ValueError, TypeError):
        sharpe = 0.0

    needs_fix = abs(sharpe) > 100  # clear overflow
    if sharpe == 0.0 and len(trades) >= 10:
        # LEAN returned 0 but there are enough trades — likely overflow→0 clamping
        needs_fix = True

    if needs_fix and trades:
        computed = _compute_sharpe_from_trades(trades)
        metrics["sharpe_ratio"] = str(round(computed, 4))
        metrics["sharpe_source"] = "computed_from_trades"


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
    # Prefer local converted data (populated by convert_binance_to_lean.py)
    local_lean = BENCH_ROOT / "data" / "lean"
    if local_lean.is_dir() and any(local_lean.iterdir()):
        # Warn if custom symbol-properties is missing — LEAN will fall back to
        # its built-in DB which doesn't cover all universe symbols.
        if not (local_lean / "symbol-properties").is_dir():
            print("WARNING: bench/data/lean/symbol-properties/ not found. "
                  "Run scripts/generate_symbol_properties.py to create it. "
                  "Without it, some symbols will fail to resolve in LEAN.")
        print(f"Using LEAN data from: {local_lean}")
        return str(local_lean)

    # Try to use data_manager (HuggingFace) if local data unavailable
    try:
        sys.path.insert(0, str(BENCH_ROOT))
        from scripts.data_manager import ensure_data
        paths = ensure_data(series="i", revision=DATASET_REVISION)
        return paths.lean_data
    except (ImportError, Exception) as e:
        print(f"WARNING: Could not load data via data_manager: {e}")

    # Fallback: check common locations
    candidates = [
        Path.home() / ".cache" / "quanttutorbench" / "lean_data",
        Path("/tmp/lean_data"),
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
    parameters: dict | None = None,
    run_id: str | None = None,
) -> dict:
    """Run a LEAN backtest for the given task and return structured results.

    Args:
        task_id: Task identifier (e.g., "I02")
        lean_image: Docker image for LEAN engine
        start_date: Backtest start date (YYYY-MM-DD)
        end_date: Backtest end date (YYYY-MM-DD)
        dry_run: If True, only validate setup without running
        parameters: Optional dict of algorithm parameters to set in config.json
        run_id: Optional run identifier for multi-run tasks

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
        "I07_alpha_model": "QuantTutorBench.I07AlphaModel",
        "I08_multi_alpha": "QuantTutorBench.I08MultiAlpha",
        "I09_risk_management": "QuantTutorBench.I09RiskManagement",
        "I10_parameter_optimization": "QuantTutorBench.I10ParameterOptimization",
        "X07_warmup_fixed": "QuantTutorBench.X07WarmupFixed",
        "X08_order_type_fixed": "QuantTutorBench.X08OrderTypeFixed",
        "X09_alpha_conflict_fixed": "QuantTutorBench.X09AlphaConflictFixed",
        "X10_universe_stale_fixed": "QuantTutorBench.X10UniverseStaleFixed",
        "E02_bb_reversion": "QuantTutorBench.E02BbReversion",
        "E04_compound_fixed": "QuantTutorBench.E04CompoundFixed",
        "E05_momentum_topn": "QuantTutorBench.E05MomentumTopn",
    }
    class_name = class_name_map.get(algo_name, algo_name)

    lean_data_dir = _ensure_lean_data()

    if dry_run:
        print(f"DRY RUN: Would run {class_name} from {algo_file}")
        print(f"  LEAN image: {lean_image}")
        print(f"  Data dir:   {lean_data_dir}")
        print(f"  Period:     {start_date} → {end_date}")
        if parameters:
            print(f"  Parameters: {parameters}")
        if run_id:
            print(f"  Run ID:     {run_id}")
        return {"status": "dry_run", "task_id": task_id, "run_id": run_id}

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
    <Reference Include="QuantConnect.Algorithm.Framework">
      <HintPath>/Lean/Launcher/bin/Debug/QuantConnect.Algorithm.Framework.dll</HintPath>
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
        # Merge task-specific parameters into config
        if parameters:
            config["parameters"] = {**config.get("parameters", {}), **parameters}
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

        run_label = f"{task_id}/{run_id}" if run_id else task_id
        print(f"Running LEAN backtest for {run_label} ({class_name})...")

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

        # Fix bogus Sharpe from LEAN overflow (common with extreme crypto returns)
        _fix_bogus_sharpe(metrics, trades)
        # Fill in total_return/max_drawdown when LEAN didn't output Statistics
        _fix_missing_return_drawdown(metrics, trades)

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
        if run_id:
            output_path = REFERENCE_OUTPUT_DIR / f"{task_id}_reference_trades_{run_id}.json"
            output["run_id"] = run_id
            output["parameters"] = parameters or {}
        else:
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


def _export_sweep_summary(task_id: str, results: list[dict]) -> None:
    """Export aggregated sweep/grid results for multi-run tasks (I06, I10)."""
    successful = [r for r in results if r.get("status") == "success"]
    if not successful:
        return

    sweep_results = []
    for r in successful:
        params = r.get("parameters", {})
        metrics = r.get("metrics", {})
        entry_metrics = {
            "total_trades": metrics.get("total_trades", 0),
            "sharpe_ratio": metrics.get("sharpe_ratio", "0"),
            "total_return": metrics.get("total_return", "0%"),
            "max_drawdown": metrics.get("max_drawdown", "0%"),
            "win_rate": metrics.get("win_rate", "0%"),
        }
        # Fix bogus Sharpe for sweep entries too
        _fix_bogus_sharpe(entry_metrics, r.get("trades", []))
        sweep_results.append({
            "run_id": r.get("run_id", ""),
            "parameters": params,
            "metrics": entry_metrics,
        })

    # Sort by Sharpe ratio descending
    def _sharpe_float(entry):
        try:
            return float(entry["metrics"]["sharpe_ratio"])
        except (ValueError, TypeError):
            return -999
    sweep_results.sort(key=_sharpe_float, reverse=True)

    # Export comparison JSON for I08/I09 (not a sweep, but a comparison)
    # Include ALL runs (even those with 0 trades) to show model differences
    if task_id in ("I08", "I09"):
        comparison = {
            "task_id": task_id,
            "type": "comparison",
            "generated_at": datetime.now(tz=__import__('datetime').timezone.utc).isoformat(),
            "runs": [
                {
                    "run_id": r.get("run_id", ""),
                    "parameters": r.get("parameters", {}),
                    "metrics": r.get("metrics", {}),
                    "trade_count": r.get("trade_count", 0),
                    "status": r.get("status", "unknown"),
                }
                for r in results
                if r.get("status") not in ("error", "timeout")
            ],
        }
        comp_path = REFERENCE_OUTPUT_DIR / f"{task_id}_reference_comparison.json"
        with open(comp_path, "w") as f:
            json.dump(comparison, f, indent=2)
        print(f"  Exported comparison to {comp_path.name}")
        return  # I08/I09 don't need sweep summary

    # Determine output filename
    if task_id == "I06":
        out_path = REFERENCE_OUTPUT_DIR / "I06_reference_sweep_results.json"
    elif task_id == "I10":
        out_path = REFERENCE_OUTPUT_DIR / "I10_reference_grid_results.json"
    else:
        out_path = REFERENCE_OUTPUT_DIR / f"{task_id}_reference_sweep_results.json"

    summary = {
        "task_id": task_id,
        "type": "parameter_sweep",
        "generated_at": datetime.now(tz=__import__('datetime').timezone.utc).isoformat(),
        "total_configurations_tested": len(successful),
        "total_configurations_attempted": len(results),
        "best_configuration": sweep_results[0] if sweep_results else None,
        "sweep_results": sweep_results,
    }

    REFERENCE_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"  Exported {len(sweep_results)} sweep results to {out_path.name}")
    if sweep_results:
        best = sweep_results[0]
        print(f"  Best config: {best['run_id']} (Sharpe={best['metrics']['sharpe_ratio']})")


def run_lean_backtest_multi(
    task_id: str,
    lean_image: str = DEFAULT_LEAN_IMAGE,
    start_date: str = DEFAULT_START_DATE,
    end_date: str = DEFAULT_END_DATE,
    dry_run: bool = False,
    max_workers: int = 1,
) -> list[dict]:
    """Run multiple LEAN backtests for tasks with parameterized configurations.

    For tasks in TASK_RUN_CONFIGS, iterates over each config and runs
    run_lean_backtest() per config. For I10, uses ThreadPoolExecutor
    for parallel execution.

    Returns list of result dicts.
    """
    task_id = task_id.upper()
    configs = TASK_RUN_CONFIGS.get(task_id)
    if not configs:
        # Single-run task — delegate directly
        result = run_lean_backtest(task_id, lean_image, start_date, end_date, dry_run)
        return [result]

    print(f"Multi-run task {task_id}: {len(configs)} configurations")

    if max_workers > 1 and not dry_run:
        import concurrent.futures

        results = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for cfg in configs:
                future = executor.submit(
                    run_lean_backtest,
                    task_id=task_id,
                    lean_image=lean_image,
                    start_date=start_date,
                    end_date=end_date,
                    dry_run=dry_run,
                    parameters=cfg["parameters"],
                    run_id=cfg["run_id"],
                )
                futures[future] = cfg["run_id"]

            for future in concurrent.futures.as_completed(futures):
                rid = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    tc = result.get("trade_count", 0)
                    print(f"  {rid}: {result.get('status', 'unknown')} ({tc} trades)")
                except Exception as e:
                    print(f"  {rid}: FAILED - {e}")
                    results.append({"status": "error", "run_id": rid, "error": str(e)})

        _export_sweep_summary(task_id, results)
        return results
    else:
        results = []
        for cfg in configs:
            try:
                result = run_lean_backtest(
                    task_id=task_id,
                    lean_image=lean_image,
                    start_date=start_date,
                    end_date=end_date,
                    dry_run=dry_run,
                    parameters=cfg["parameters"],
                    run_id=cfg["run_id"],
                )
                results.append(result)
            except Exception as e:
                print(f"  {cfg['run_id']}: FAILED - {e}")
                results.append({"status": "error", "run_id": cfg["run_id"], "error": str(e)})
        _export_sweep_summary(task_id, results)
        return results


def main():
    parser = argparse.ArgumentParser(
        description="Generate reference trade logs from LEAN backtests"
    )
    parser.add_argument(
        "--task",
        required=True,
        help="Task ID (I01-I10) or 'all' to run all tasks",
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
    parser.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="Max parallel Docker workers for multi-run tasks (default: 1)",
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
            if task_id in TASK_RUN_CONFIGS:
                run_results = run_lean_backtest_multi(
                    task_id=task_id,
                    lean_image=args.lean_image,
                    start_date=args.start_date,
                    end_date=args.end_date,
                    dry_run=args.dry_run,
                    max_workers=args.max_workers,
                )
                results[task_id] = run_results
                total_trades = sum(r.get("trade_count", 0) for r in run_results)
                print(f"  {task_id}: {len(run_results)} runs, {total_trades} total trades")
            else:
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
        if isinstance(result, list):
            total_trades = sum(r.get("trade_count", 0) for r in result)
            print(f"  {task_id}: {len(result)} runs ({total_trades} total trades)")
        else:
            status = result.get("status", "unknown")
            trades = result.get("trade_count", 0)
            print(f"  {task_id}: {status} ({trades} trades)")
    print("=" * 60)


if __name__ == "__main__":
    main()
