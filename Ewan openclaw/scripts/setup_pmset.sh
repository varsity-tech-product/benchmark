#!/bin/bash
# ============================================================
# Setup macOS scheduled wake for evening report.
#
# Wakes the machine at 18:25 daily (5 min before 18:30 trigger)
# so Docker and OpenClaw have time to start.
#
# Usage: sudo bash scripts/setup_pmset.sh
# ============================================================

set -euo pipefail

echo "=== Setting up daily wake schedule ==="

# Schedule wake every day at 18:25
sudo pmset repeat wakeorpoweron MTWRFSU 18:25:00

echo "Schedule set. Verifying..."
sudo pmset -g sched

echo ""
echo "=== Done ==="
echo "Your Mac will wake at 18:25 daily."
echo "OpenClaw automation triggers at 18:30."
echo "System will auto-sleep ~30 min after report completes."
echo ""
echo "To remove this schedule later:"
echo "  sudo pmset repeat cancel"
