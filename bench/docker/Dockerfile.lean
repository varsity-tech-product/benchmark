# Dockerfile.lean — LEAN engine sandbox for I-series implementation tasks.
#
# Extends the base quant-tutor-env image with:
#   - .NET SDK 8.0 (for C# algorithm compilation)
#   - QuantConnect LEAN engine (cloned and pre-built)
#   - Pre-configured lean-config.json for Binance futures backtesting
#   - run_backtest wrapper script
#
# NO data is baked into this image (~3GB). Market data is mounted at runtime:
#   -v hf_cache/lean/:/lean/Data:ro    (LEAN-format market data)
#   -v workspace/:/workspace            (agent working directory)
#   -v data/:/data:ro                   (universe.json + metadata)
#   -v docs/:/docs:ro                   (reference documentation)
#
# Build:
#   docker build -f Dockerfile.lean -t quant-tutor-env:v2.2-lean .
#
# Run (example):
#   docker run --rm \
#     -v /path/to/lean-data:/lean/Data:ro \
#     -v /tmp/workspace:/workspace \
#     -v /path/to/universe:/data:ro \
#     -v /path/to/docs:/docs:ro \
#     quant-tutor-env:v2.2-lean \
#     run_backtest /workspace/Algorithm.cs

FROM quant-tutor-env:v2.2

USER root

# ── Install .NET SDK 8.0 ──────────────────────────────────────────────
# Microsoft package repository for Debian
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        wget \
        apt-transport-https \
    && wget -q https://packages.microsoft.com/config/debian/12/packages-microsoft-prod.deb \
    && dpkg -i packages-microsoft-prod.deb \
    && rm packages-microsoft-prod.deb \
    && apt-get update \
    && apt-get install -y --no-install-recommends dotnet-sdk-8.0 \
    && rm -rf /var/lib/apt/lists/*

# ── Clone and build LEAN engine ───────────────────────────────────────
RUN git clone --depth 1 https://github.com/QuantConnect/Lean.git /lean

WORKDIR /lean
RUN dotnet restore QuantConnect.Lean.sln \
    && dotnet build QuantConnect.Lean.sln -c Debug --no-restore

# ── Configure LEAN for Binance futures backtesting ────────────────────
COPY lean-config.json /lean/Launcher/config.json

# ── Install backtest wrapper script ──────────────────────────────────
COPY run_backtest.sh /usr/local/bin/run_backtest
RUN chmod +x /usr/local/bin/run_backtest

# ── Create mount-point directories ───────────────────────────────────
# /lean/Data  — LEAN-format market data (mounted at runtime, NOT baked in)
# /workspace  — agent's working directory (read-write)
# /data       — universe.json and metadata (read-only)
# /docs       — reference documentation (read-only)
RUN mkdir -p /lean/Data /workspace /data /docs \
    && chown -R sandbox:sandbox /workspace

# ── Runtime configuration ────────────────────────────────────────────
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV DOTNET_NOLOGO=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/home/sandbox
ENV PYTHONPATH=/opt/bench

WORKDIR /workspace
USER sandbox
