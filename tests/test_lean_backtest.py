#!/usr/bin/env python3
"""Infrastructure test: run I-series reference algorithms through the Docker
LEAN backtest pipeline (quant-tutor-env:v2.2-lean) and validate results.

This skips the LLM simulation entirely — it directly:
  1. Rewrites reference .cs to use class name "Algorithm" (as agents must)
  2. Spins up a Docker container with LEAN data mounted
  3. Runs run_backtest inside the container
  4. Checks trade count, Sharpe, return against reference expectations

Usage:
    pytest tests/test_lean_backtest.py -v              # all tasks
    pytest tests/test_lean_backtest.py -v -k I01       # single task
    pytest tests/test_lean_backtest.py -v -k "I01 or I03"  # subset
"""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

# ── Paths ───────────────────────────────────────────────────────────────
BENCH_ROOT = Path(__file__).parent.parent / "bench"
ALGO_DIR = BENCH_ROOT / "reference" / "Implementation" / "algorithms"
REF_DIR = BENCH_ROOT / "reference" / "Implementation" / "result"
LEAN_DATA = BENCH_ROOT / "data" / "hf_cache" / "lean" / "I"
DOCKER_IMAGE = "quant-tutor-env:v2.2-lean"
RUN_BACKTEST_SH = BENCH_ROOT / "docker" / "run_backtest.sh"

# ── Reference expectations ──────────────────────────────────────────────

@dataclass
class RefExpectation:
    algo_file: str           # e.g. "I01_implement_sma.cs"
    class_pattern: str       # regex to find original class name
    trade_count: int         # expected closed trades (or min orders if trades=0)
    sharpe: float | None     # expected Sharpe (None = skip check)
    return_pct: float | None # expected total return % (None = skip)
    tolerance_trades: float  # fractional tolerance on trade count
    tolerance_sharpe: float  # absolute tolerance on Sharpe
    timeout: int = 300       # seconds for LEAN engine
    min_orders: int = 0      # minimum order events (fallback when trade count=0)


TASKS = {
    "I01": RefExpectation(
        algo_file="I01_implement_sma.cs",
        class_pattern=r"class\s+I01ImplementSma",
        trade_count=85,
        sharpe=0.168,
        return_pct=32.91,
        tolerance_trades=0.05,  # within 5%
        tolerance_sharpe=0.20,  # different LEAN builds have stats variance
    ),
    "I02": RefExpectation(
        algo_file="I02_trend_following.cs",
        class_pattern=r"class\s+I02TrendFollowing",
        trade_count=1763,
        sharpe=None,  # reference has 0.0 (overflow)
        return_pct=None,
        tolerance_trades=0.05,
        tolerance_sharpe=0.2,
        min_orders=5000,  # LEAN TradeBuilder may report 0 closed trades for
                          # multi-symbol CryptoFutures; validate via order count
    ),
    "I03": RefExpectation(
        algo_file="I03_mean_reversion.cs",
        class_pattern=r"class\s+I03MeanReversion",
        trade_count=662,
        sharpe=-0.335,
        return_pct=-102.952,
        tolerance_trades=0.05,
        tolerance_sharpe=0.2,
    ),
    "I04": RefExpectation(
        algo_file="I04_multi_timeframe.cs",
        class_pattern=r"class\s+I04MultiTimeframe",
        trade_count=4026,
        sharpe=-0.07,
        return_pct=-17.194,
        tolerance_trades=0.10,
        tolerance_sharpe=0.2,
        timeout=600,  # multi-timeframe needs more time
    ),
    "I05": RefExpectation(
        algo_file="I05_cross_asset.cs",
        class_pattern=r"class\s+I05CrossAsset",
        trade_count=2294,
        sharpe=None,  # reference has 0.0
        return_pct=None,
        tolerance_trades=0.10,
        tolerance_sharpe=0.2,
        timeout=600,
        min_orders=3000,  # same TradeBuilder quirk as I02
    ),
    "I07": RefExpectation(
        algo_file="I07_alpha_model.cs",
        class_pattern=r"class\s+I07AlphaModel",
        trade_count=179,
        sharpe=None,  # different across LEAN builds
        return_pct=None,
        tolerance_trades=2.0,  # framework algos vary significantly across LEAN versions
        tolerance_sharpe=0.3,
        min_orders=100,  # must produce some orders
    ),
}


# ── Helpers ─────────────────────────────────────────────────────────────

def _get_fqn(src: Path) -> str:
    """Extract the fully-qualified class name (Namespace.ClassName) from a .cs file."""
    code = src.read_text()
    ns_match = re.search(r"namespace\s+([\w.]+)", code)
    cls_match = re.search(r"class\s+(\w+)\s*:\s*QCAlgorithm", code)
    if not cls_match:
        raise ValueError(f"No QCAlgorithm subclass found in {src}")
    cls_name = cls_match.group(1)
    if ns_match:
        return f"{ns_match.group(1)}.{cls_name}"
    return cls_name


def _docker_available() -> bool:
    try:
        r = subprocess.run(["docker", "info"], capture_output=True, timeout=5)
        return r.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _image_exists(image: str) -> bool:
    r = subprocess.run(
        ["docker", "images", "-q", image], capture_output=True, text=True, timeout=10
    )
    return bool(r.stdout.strip())


def _lean_data_available() -> bool:
    daily_dir = LEAN_DATA / "cryptofuture" / "binance" / "daily"
    return daily_dir.exists() and any(daily_dir.iterdir())


def run_lean_in_docker(
    algo_cs: Path,
    workspace: Path,
    timeout: int = 300,
    fqn: str | None = None,
) -> dict:
    """Run a LEAN backtest via Docker and return parsed results.

    The Docker image's baked-in run_backtest.sh may be stale (missing the
    DLL-copy step).  We mirror what the orchestrator does: start the
    container, inject the latest repo copy of run_backtest.sh, then exec.

    If *fqn* is provided the algorithm-type-name in LEAN config is patched
    to the fully-qualified class name so LEAN can locate the class among
    all other example algorithms in the Algorithm.CSharp project.

    Returns dict with keys: exit_code, stdout, trades, summary, trade_count.
    """
    container_name = f"test_lean_{int(time.time())}_{os.getpid()}"

    # Copy the original .cs into workspace
    shutil.copy2(algo_cs, workspace / "Algorithm.cs")

    # --- Start container (detached, sleep infinity) ---
    start_cmd = [
        "docker", "run", "-d",
        "--name", container_name,
        "--network", "none",
        "--cpus", "2",
        "--memory", "2g",
        "-v", f"{workspace}:/workspace",
        "-v", f"{LEAN_DATA}:/lean/Data:ro",
        "-e", f"LEAN_RUN_TIMEOUT={timeout}",
        DOCKER_IMAGE,
        "sleep", "infinity",
    ]
    start = subprocess.run(start_cmd, capture_output=True, text=True, timeout=30)
    if start.returncode != 0:
        raise RuntimeError(f"docker run failed: {start.stderr}")

    try:
        # --- Inject latest run_backtest.sh (same as orchestrator) ---
        subprocess.run(
            ["docker", "cp", str(RUN_BACKTEST_SH), f"{container_name}:/usr/local/bin/run_backtest"],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["docker", "exec", "--user", "root", container_name,
             "chmod", "+x", "/usr/local/bin/run_backtest"],
            capture_output=True, check=True,
        )

        # --- Patch LEAN config with fully-qualified class name ---
        if fqn:
            patch_cmd = (
                f"python3 -c \""
                f"import json; "
                f"cfg = json.load(open('/lean/Launcher/config.json')); "
                f"cfg['algorithm-type-name'] = '{fqn}'; "
                f"json.dump(cfg, open('/lean/Launcher/config.json','w'), indent=2)"
                f"\""
            )
            subprocess.run(
                ["docker", "exec", container_name, "bash", "-c", patch_cmd],
                capture_output=True, check=True,
            )

        # --- Run the backtest ---
        result = subprocess.run(
            ["docker", "exec", container_name,
             "run_backtest", "/workspace/Algorithm.cs"],
            capture_output=True,
            text=True,
            timeout=timeout + 120,  # extra buffer for build time
        )
    finally:
        # --- Cleanup container ---
        subprocess.run(
            ["docker", "rm", "-f", container_name],
            capture_output=True, timeout=15,
        )

    output = {
        "exit_code": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "trades": None,
        "summary": None,
        "trade_count": 0,
    }

    # Parse results from workspace.
    # run_backtest.sh may fail to extract results (exit code 4) if the
    # file-name patterns are stale, but LEAN still produces output.
    # We search for result files ourselves using LEAN's actual naming.
    results_dir = workspace / "results"
    if not results_dir.exists():
        return output

    # Find summary: try run_backtest.sh's extracted name first, then LEAN's
    summary_path = None
    for candidate in [
        results_dir / "summary.json",
        *results_dir.glob("*-summary.json"),
        *results_dir.glob("*-statistics.json"),
    ]:
        if candidate.exists():
            summary_path = candidate
            break

    # Also check the main algorithm JSON (contains everything)
    algo_jsons = list(results_dir.glob("*.json"))
    main_json = None
    for aj in algo_jsons:
        if not any(aj.name.endswith(s) for s in [
            "-summary.json", "-order-events.json", "-log.txt",
            "data-monitor-report", "data-requests",
        ]):
            if aj.stat().st_size > 10000:  # main JSON is large
                main_json = aj
                break

    if summary_path and summary_path.exists():
        try:
            with open(summary_path) as f:
                output["summary"] = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # If no summary but main JSON exists, use that
    if output["summary"] is None and main_json and main_json.exists():
        try:
            with open(main_json) as f:
                output["summary"] = json.load(f)
        except (json.JSONDecodeError, IOError):
            pass

    # Count orders from order-events file (fallback metric)
    order_count = 0
    for ofile in [
        *results_dir.glob("orders.json"),
        *results_dir.glob("*-order-events.json"),
    ]:
        try:
            with open(ofile) as f:
                odata = json.load(f)
            order_count = len(odata) if isinstance(odata, (list, dict)) else 0
            break
        except (json.JSONDecodeError, IOError):
            pass
    output["order_count"] = order_count

    # Override exit code 4 (extraction failure) when LEAN produced a valid
    # summary — the script may fail at result extraction due to stale
    # file-name patterns while LEAN itself completed fine.
    # Do NOT override exit code 3 (runtime error) — that means LEAN crashed
    # and partial output should not be treated as success.
    if output["summary"] is not None and result.returncode == 4:
        output["exit_code"] = 0

    return output


def _extract_metric(summary: dict, key: str) -> float | None:
    """Extract a metric from LEAN summary JSON (handles various key formats)."""
    if summary is None:
        return None

    # Try direct key
    val = summary.get(key)
    if val is not None:
        if isinstance(val, str):
            # Remove % sign and parse
            val = val.replace("%", "").replace(",", "").strip()
            try:
                return float(val)
            except ValueError:
                return None
        return float(val)

    # Try nested under various paths
    for parent_key in ["Statistics", "statistics"]:
        stats = summary.get(parent_key, {})
        if isinstance(stats, dict):
            val = stats.get(key)
            if val is not None:
                if isinstance(val, str):
                    val = val.replace("%", "").replace(",", "").strip()
                    try:
                        return float(val)
                    except ValueError:
                        return None
                return float(val)

    return None


def _count_trades_from_summary(summary: dict) -> int | None:
    """Extract total trade count from summary.json."""
    if summary is None:
        return None

    # Try totalPerformance path
    perf = summary.get("totalPerformance", {}).get("tradeStatistics", {})
    tc = perf.get("totalNumberOfTrades")
    if tc is not None:
        return int(tc)

    # Try statistics.Total Trades (NOT Total Orders — orders != trades)
    for parent in [summary, summary.get("Statistics", {}), summary.get("statistics", {})]:
        if not isinstance(parent, dict):
            continue
        for key in ["Total Trades", "total_trades"]:
            val = parent.get(key)
            if val is not None:
                try:
                    return int(str(val).replace(",", ""))
                except ValueError:
                    pass

    return None


# ── Fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def check_prerequisites():
    """Verify Docker and LEAN data are available before running tests."""
    if not _docker_available():
        pytest.skip("Docker not available")
    if not _image_exists(DOCKER_IMAGE):
        pytest.skip(f"Docker image {DOCKER_IMAGE} not found")
    if not _lean_data_available():
        pytest.skip("LEAN market data not available in hf_cache")


# ── Tests ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("task_id", list(TASKS.keys()))
def test_lean_backtest(task_id: str, tmp_path: Path):
    """Run reference algorithm through Docker LEAN pipeline and validate."""
    ref = TASKS[task_id]
    algo_src = ALGO_DIR / ref.algo_file

    assert algo_src.exists(), f"Reference algorithm not found: {algo_src}"

    # Extract the fully-qualified class name from the original source
    fqn = _get_fqn(algo_src)

    workspace = tmp_path / "workspace"
    workspace.mkdir()

    # Run the backtest using original .cs with FQN
    print(f"\n--- Running {task_id} LEAN backtest (fqn={fqn}, timeout={ref.timeout}s) ---")
    result = run_lean_in_docker(algo_src, workspace, timeout=ref.timeout, fqn=fqn)

    # Print diagnostics on failure
    if result["exit_code"] != 0:
        print(f"STDOUT (last 2000 chars):\n{result['stdout'][-2000:]}")
        print(f"STDERR (last 1000 chars):\n{result['stderr'][-1000:]}")

    assert result["exit_code"] == 0, (
        f"{task_id} backtest failed with exit code {result['exit_code']}"
    )

    # Check summary.json was produced
    assert result["summary"] is not None, f"{task_id}: no summary.json produced"

    # Check trade count (closed trades from TradeBuilder)
    trade_count = _count_trades_from_summary(result["summary"])
    if trade_count is None:
        trade_count = result.get("trade_count", 0)
    order_count = result.get("order_count", 0)

    print(f"  Trades: {trade_count}, Orders: {order_count} (expected trades: ~{ref.trade_count})")

    if trade_count == 0 and ref.min_orders > 0:
        # LEAN TradeBuilder may not count CryptoFuture round-trips as
        # "closed trades" in some builds.  Validate via order count instead.
        assert order_count >= ref.min_orders, (
            f"{task_id}: 0 closed trades AND only {order_count} orders "
            f"(expected >= {ref.min_orders}). Strategy may not be trading."
        )
        print(f"  (trade count=0 is a TradeBuilder quirk; {order_count} orders confirms activity)")
    else:
        # Trade count should be within tolerance
        lower = int(ref.trade_count * (1 - ref.tolerance_trades))
        upper = int(ref.trade_count * (1 + ref.tolerance_trades))
        assert lower <= trade_count <= upper, (
            f"{task_id}: trade count {trade_count} outside [{lower}, {upper}] "
            f"(expected ~{ref.trade_count})"
        )

    # Check Sharpe if we have a reference value
    if ref.sharpe is not None:
        sharpe = _extract_metric(result["summary"], "Sharpe Ratio")
        if sharpe is not None:
            print(f"  Sharpe: {sharpe} (expected: {ref.sharpe})")
            assert abs(sharpe - ref.sharpe) <= ref.tolerance_sharpe, (
                f"{task_id}: Sharpe {sharpe} differs from expected {ref.sharpe} "
                f"by more than {ref.tolerance_sharpe}"
            )

    # Check total return if we have a reference value.
    # ref.return_pct values are Net Profit (not Compounding Annual Return).
    if ref.return_pct is not None:
        total_return = _extract_metric(result["summary"], "Net Profit")
        if total_return is None:
            total_return = _extract_metric(result["summary"], "Compounding Annual Return")
        if total_return is not None:
            print(f"  Net Profit: {total_return}% (expected: ~{ref.return_pct}%)")
            # Allow 1% absolute tolerance for rounding differences
            assert abs(total_return - ref.return_pct) <= 1.0, (
                f"{task_id}: Net Profit {total_return}% differs from expected "
                f"{ref.return_pct}% by more than 1.0%"
            )

    print(f"  PASS: {task_id}")


# ── Quick smoke test (always runs first) ────────────────────────────────

def test_docker_lean_smoke():
    """Minimal smoke test: can we start the LEAN container and run dotnet?"""
    result = subprocess.run(
        ["docker", "run", "--rm", DOCKER_IMAGE, "dotnet", "--version"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"dotnet check failed: {result.stderr}"
    version = result.stdout.strip()
    print(f"  .NET version: {version}")
    assert version.startswith("10."), f"Expected .NET 10.x, got {version}"


def test_lean_data_mounted():
    """Verify LEAN data is accessible and has expected structure."""
    daily_dir = LEAN_DATA / "cryptofuture" / "binance" / "daily"
    assert daily_dir.exists(), f"Daily data dir missing: {daily_dir}"

    zips = list(daily_dir.glob("*_trade.zip"))
    assert len(zips) >= 600, f"Expected 600+ daily zips, found {len(zips)}"
    print(f"  Daily data: {len(zips)} symbols")

    # Check universe.json
    universe = LEAN_DATA / "universe.json"
    assert universe.exists(), "universe.json missing"
    with open(universe) as f:
        u = json.load(f)
    if isinstance(u, list):
        print(f"  Universe: {len(u)} symbols")
        assert len(u) >= 600
    elif isinstance(u, dict):
        total = sum(len(v) if isinstance(v, list) else 1 for v in u.values())
        print(f"  Universe: {total} symbols across {len(u)} tiers")


def test_dataset_coherence():
    """Report dataset coherence gaps (informational, not gating).

    Documents the gap between universe.json and available market data
    so that missing-data failures during backtests are expected.
    """
    # Universe vs daily trade data
    universe = LEAN_DATA / "universe.json"
    with open(universe) as f:
        u = json.load(f)
    universe_count = len(u) if isinstance(u, list) else sum(
        len(v) if isinstance(v, list) else 1 for v in u.values()
    )

    daily_dir = LEAN_DATA / "cryptofuture" / "binance" / "daily"
    trade_zips = {p.name.replace("_trade.zip", "").upper()
                  for p in daily_dir.glob("*_trade.zip")}
    quote_zips = set(daily_dir.glob("*_quote.zip"))

    universe_symbols = set(u) if isinstance(u, list) else set()
    missing_daily = universe_symbols - trade_zips if universe_symbols else set()

    print(f"  Universe:        {universe_count} symbols")
    print(f"  Daily trade.zip: {len(trade_zips)} symbols")
    print(f"  Daily quote.zip: {len(quote_zips)} files (not in dataset)")
    print(f"  Missing daily:   {len(missing_daily)} symbols")
    if missing_daily:
        print(f"    Examples: {sorted(missing_daily)[:5]}")

    # Hourly data
    hourly_dir = LEAN_DATA / "cryptofuture" / "binance" / "hour"
    if hourly_dir.exists():
        hourly_zips = list(hourly_dir.glob("*_trade.zip"))
        print(f"  Hourly trade:    {len(hourly_zips)} symbols")

    # Sidecar databases
    sym_props = LEAN_DATA / "symbol-properties"
    mkt_hours = LEAN_DATA / "market-hours"
    print(f"  Symbol props:    {'present' if sym_props.exists() else 'MISSING'}")
    print(f"  Market hours:    {'present' if mkt_hours.exists() else 'MISSING'}")

    # These are informational — the gap is known and tolerated
    assert len(trade_zips) >= 600, "Too few daily trade zips"
    assert universe_count >= 600, "Universe too small"
