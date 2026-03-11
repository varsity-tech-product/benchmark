# Dockerfile.lean — LEAN engine sandbox for I-series implementation tasks.
#
# Extends the base quant-tutor-env image with:
#   - .NET SDK 10.0 (for C# algorithm compilation)
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

# ── Install .NET SDK 10.0 via install script ────────────────────────
# Bypasses Microsoft apt repository (SHA1 GPG signature rejected by
# Debian trixie since 2026-02-01). Uses the official dotnet-install.sh
# script which downloads the SDK tarball directly.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        wget \
        git \
        libicu-dev \
    && rm -rf /var/lib/apt/lists/* \
    && wget -q https://dot.net/v1/dotnet-install.sh -O /tmp/dotnet-install.sh \
    && chmod +x /tmp/dotnet-install.sh \
    && /tmp/dotnet-install.sh --channel 10.0 --install-dir /usr/share/dotnet \
    && ln -sf /usr/share/dotnet/dotnet /usr/bin/dotnet \
    && rm /tmp/dotnet-install.sh

# ── Clone and build LEAN engine ───────────────────────────────────────
RUN git clone --depth 1 https://github.com/QuantConnect/Lean.git /lean

WORKDIR /lean
RUN dotnet restore QuantConnect.Lean.sln \
    && dotnet build QuantConnect.Lean.sln -c Debug --no-restore

# ── Configure LEAN for Binance futures backtesting ────────────────────
# Copy to BOTH the project dir and the build output dir.
# LEAN reads config.json from AppDomain.BaseDirectory (= DLL directory),
# so the bin/Debug/ copy is the one actually used at runtime.
# The Launcher/ copy is kept as the canonical source for run_backtest.sh.
COPY lean-config.json /lean/Launcher/config.json
RUN cp /lean/Launcher/config.json /lean/Launcher/bin/Debug/config.json

# ── Install backtest wrapper script ──────────────────────────────────
COPY run_backtest.sh /usr/local/bin/run_backtest
RUN chmod +x /usr/local/bin/run_backtest

# ── Create mount-point directories ───────────────────────────────────
# /lean/Data  — LEAN-format market data (mounted at runtime, NOT baked in)
# /workspace  — agent's working directory (read-write)
# /data       — universe.json and metadata (read-only)
# /docs       — reference documentation (read-only)
RUN mkdir -p /lean/Data /workspace /data /docs \
    && chown -R sandbox:sandbox /workspace \
    && chown -R sandbox:sandbox /lean

# ── Runtime configuration ────────────────────────────────────────────
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV DOTNET_NOLOGO=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/home/sandbox
ENV PYTHONPATH=/opt/bench

WORKDIR /workspace
USER sandbox
