#!/usr/bin/env python3
"""
Estimate full synthesis cost by running a small sample and measuring actual spend.

Usage:
    # 1. Check current balance
    python scripts/estimate_cost.py balance

    # 2. Run sample and estimate full cost (default 100 records)
    python scripts/estimate_cost.py estimate --sample 100 --total 212503

    # 3. Monitor balance in real-time during a long synthesis run
    python scripts/estimate_cost.py monitor --interval 60
"""

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)

OPENROUTER_API = "https://openrouter.ai/api/v1"


def get_api_key() -> str:
    key = os.getenv("OPENROUTER_API_KEY")
    if not key:
        print("Error: OPENROUTER_API_KEY not set in .env")
        sys.exit(1)
    return key


def get_credits() -> dict:
    """Fetch current credits from OpenRouter API."""
    resp = requests.get(
        f"{OPENROUTER_API}/credits",
        headers={"Authorization": f"Bearer {get_api_key()}"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()["data"]
    return {
        "total_credits": data["total_credits"],
        "total_usage": data["total_usage"],
        "remaining": data["total_credits"] - data["total_usage"],
        "timestamp": datetime.now().isoformat(),
    }


def print_balance(credits: dict):
    print(f"  Total credits:  ${credits['total_credits']:.4f}")
    print(f"  Total usage:    ${credits['total_usage']:.4f}")
    print(f"  Remaining:      ${credits['remaining']:.4f}")
    print(f"  Checked at:     {credits['timestamp']}")


# ── balance command ──────────────────────────────────────────────
def cmd_balance(_args):
    print("OpenRouter Account Balance")
    print("=" * 50)
    credits = get_credits()
    print_balance(credits)


# ── estimate command ─────────────────────────────────────────────
def cmd_estimate(args):
    sample = args.sample
    total = args.total

    print("Cost Estimation")
    print("=" * 50)
    print(f"Sample size:    {sample} records")
    print(f"Total records:  {total:,} records")
    print()

    # Before
    print("[1/3] Checking balance before synthesis...")
    before = get_credits()
    print_balance(before)
    print()

    # Run synthesis
    print(f"[2/3] Running synthesis on {sample} records per dataset...")
    print("-" * 50)
    tmp_output_dir = PROJECT_ROOT / "data" / "02_synthesized" / "_cost_estimate"
    synth_cmd = [
        sys.executable,
        str(PROJECT_ROOT / "scripts" / "03_synthesize" / "synthesize_tsr.py"),
        "--all",
        "--sample",
        str(sample),
        "--no-checkpoint",
        "--output-dir",
        str(tmp_output_dir),
    ]
    result = subprocess.run(synth_cmd)
    print("-" * 50)

    if result.returncode != 0:
        print(f"Warning: synthesis exited with code {result.returncode}")
        print("Cost estimate may be based on partial run.\n")

    # After — wait briefly for usage to settle
    time.sleep(3)
    print("[3/3] Checking balance after synthesis...")
    after = get_credits()
    print_balance(after)
    print()

    # Count actual records synthesized
    actual_sample = 0
    if tmp_output_dir.exists():
        for f in tmp_output_dir.glob("*.jsonl"):
            actual_sample += sum(1 for _ in open(f))

    effective_sample = actual_sample if actual_sample > 0 else sample

    # Calculate
    spent = after["total_usage"] - before["total_usage"]
    cost_per_record = spent / effective_sample if effective_sample > 0 else 0
    estimated_total = cost_per_record * total

    print("=" * 50)
    print("Results")
    print("=" * 50)
    print(
        f"  Sample size:           {effective_sample} records (requested {sample}/dataset)"
    )
    print(f"  Actual cost (sample):  ${spent:.6f}")
    print(f"  Cost per record:       ${cost_per_record:.6f}")
    print()
    print(f"  Total records:         {total:,}")
    print(f"  Estimated full cost:   ${estimated_total:.2f}")
    print()
    print(f"  Current remaining:     ${after['remaining']:.4f}")
    if after["remaining"] < estimated_total:
        shortfall = estimated_total - after["remaining"]
        print(f"  Shortfall:             ${shortfall:.2f} (need to top up)")
    else:
        print(
            f"  Sufficient:            Yes (${after['remaining'] - estimated_total:.2f} to spare)"
        )

    # Save report
    report = {
        "sample_size": sample,
        "total_records": total,
        "cost_sample": spent,
        "cost_per_record": cost_per_record,
        "estimated_total_cost": estimated_total,
        "balance_before": before,
        "balance_after": after,
    }
    report_path = PROJECT_ROOT / "data" / "cost_estimate_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with open(report_path, "w") as f:
        json.dump(report, f, indent=2)
    print(f"\n  Report saved: {report_path}")

    # Cleanup temp output
    if tmp_output_dir.exists():
        import shutil

        shutil.rmtree(tmp_output_dir)


# ── monitor command ──────────────────────────────────────────────
def cmd_monitor(args):
    interval = args.interval
    print("Balance Monitor (Ctrl+C to stop)")
    print("=" * 50)

    initial = get_credits()
    print(f"Starting balance: ${initial['remaining']:.4f}")
    print(f"Polling every {interval}s\n")
    print(f"{'Time':<10} {'Remaining':>12} {'Spent':>12} {'Rate $/min':>12}")
    print("-" * 50)

    start_time = time.time()
    start_usage = initial["total_usage"]

    try:
        while True:
            time.sleep(interval)
            current = get_credits()
            elapsed_min = (time.time() - start_time) / 60
            total_spent = current["total_usage"] - start_usage
            rate = total_spent / elapsed_min if elapsed_min > 0 else 0

            now = datetime.now().strftime("%H:%M:%S")
            print(
                f"{now:<10} "
                f"${current['remaining']:>11.4f} "
                f"${total_spent:>11.6f} "
                f"${rate:>11.6f}"
            )
    except KeyboardInterrupt:
        print("\n\nMonitor stopped.")
        elapsed_min = (time.time() - start_time) / 60
        final = get_credits()
        total_spent = final["total_usage"] - start_usage
        print(f"  Duration:    {elapsed_min:.1f} min")
        print(f"  Total spent: ${total_spent:.6f}")
        if elapsed_min > 0:
            rate = total_spent / elapsed_min
            print(f"  Avg rate:    ${rate:.6f}/min")


def main():
    parser = argparse.ArgumentParser(description="OpenRouter cost estimation tool")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("balance", help="Check current balance")

    est = sub.add_parser("estimate", help="Run sample and estimate full cost")
    est.add_argument(
        "--sample", type=int, default=100, help="Sample size (default: 100)"
    )
    est.add_argument(
        "--total", type=int, default=119441, help="Total records to estimate for"
    )

    mon = sub.add_parser("monitor", help="Monitor balance in real-time")
    mon.add_argument(
        "--interval",
        type=int,
        default=60,
        help="Poll interval in seconds (default: 60)",
    )

    args = parser.parse_args()

    {"balance": cmd_balance, "estimate": cmd_estimate, "monitor": cmd_monitor}[
        args.command
    ](args)


if __name__ == "__main__":
    main()
