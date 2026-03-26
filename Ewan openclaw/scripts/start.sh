#!/bin/bash
# ============================================================
# Start the OpenClaw finance assistant.
#
# Prerequisites:
#   1. Docker Desktop installed and running
#   2. .env file created with API keys (copy from .env.example)
#   3. Telegram bot created via @BotFather
#
# Usage: bash scripts/start.sh
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_DIR"

# Check .env exists
if [ ! -f .env ]; then
    echo "ERROR: .env file not found."
    echo "Copy .env.example to .env and fill in your API keys."
    exit 1
fi

# Check Docker is running
if ! docker info > /dev/null 2>&1; then
    echo "ERROR: Docker is not running. Start Docker Desktop first."
    exit 1
fi

echo "=== Starting OpenClaw Finance Assistant ==="
echo "Project dir: $PROJECT_DIR"
echo ""

# Start with docker compose
docker compose up -d

echo ""
echo "=== Container started ==="
echo "Web UI: http://localhost:18789"
echo "Logs:   docker compose logs -f"
echo ""
echo "Send a message to your Telegram bot to test connectivity."
