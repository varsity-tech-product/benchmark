# Dockerfile.lean — LEAN engine sandbox for I-series implementation tasks.
#
# Extends the base quant-tutor-env image with:
#   - .NET SDK 10.0 (required by current pinned LEAN source)
#   - QuantConnect LEAN engine (cloned and pre-built at a pinned commit)
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

# ── Install .NET SDK 10.0 ─────────────────────────────────────────────
# Use dotnet-install.sh instead of the Debian apt repo because the
# Microsoft repo signing chain currently fails under newer Debian policy.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        git \
        libicu-dev \
        wget \
    && wget -q https://dot.net/v1/dotnet-install.sh -O /tmp/dotnet-install.sh \
    && chmod +x /tmp/dotnet-install.sh \
    && /tmp/dotnet-install.sh --channel 10.0 --install-dir /usr/share/dotnet \
    && ln -s /usr/share/dotnet/dotnet /usr/local/bin/dotnet \
    && rm /tmp/dotnet-install.sh \
    && rm -rf /var/lib/apt/lists/*

# ── Clone and build LEAN engine ───────────────────────────────────────
# Pinned to QuantConnect/Lean master at build-time review:
#   0c4a121371be684c7e9e8d0e92816a2f34a185b9
ARG LEAN_COMMIT=0c4a121371be684c7e9e8d0e92816a2f34a185b9
RUN git init /lean \
    && git -C /lean remote add origin https://github.com/QuantConnect/Lean.git \
    && git -C /lean fetch --depth 1 origin ${LEAN_COMMIT} \
    && git -C /lean checkout FETCH_HEAD

WORKDIR /lean
RUN dotnet restore QuantConnect.Lean.sln \
    && dotnet build QuantConnect.Lean.sln -c Debug --no-restore

# ── Configure LEAN for Binance futures backtesting ────────────────────
COPY docker/lean-config.json /lean/Launcher/config.json

# ── Install backtest wrapper script ──────────────────────────────────
COPY docker/run_backtest.sh /usr/local/bin/run_backtest
RUN chmod +x /usr/local/bin/run_backtest

# ── Install shared LEAN config helper ────────────────────────────────
# Imported by run_backtest.sh (in-container) and by the reference
# generator (host-side). Same source, same behaviour — prevents the
# divergence that caused issue #33.
COPY docker/lean_config.py /lean/helpers/lean_config.py

# ── Install strategy injection script (for custom data mode) ────────
COPY scripts/inject_strategy.py /opt/bench/scripts/inject_strategy.py

# ── Create mount-point directories ───────────────────────────────────
# /lean/Data  — LEAN-format market data (mounted at runtime, NOT baked in)
# /Lean      — compatibility symlink for tools that expect the upstream path
# /workspace  — agent's working directory (read-write)
# /data       — universe.json and metadata (read-only)
# /docs       — reference documentation (read-only)
RUN mkdir -p /lean/Data /workspace /data /data/custom /docs \
    && ln -s /lean /Lean \
    && mkdir -p /home/sandbox/.nuget /home/sandbox/.dotnet \
    && mkdir -p /lean/Launcher/bin/Debug/storage \
    && chown -R sandbox:sandbox /lean /workspace /home/sandbox

# ── Runtime configuration ────────────────────────────────────────────
ENV DOTNET_CLI_TELEMETRY_OPTOUT=1
ENV DOTNET_NOLOGO=1
ENV DOTNET_ROOT=/usr/share/dotnet
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/home/sandbox
ENV PYTHONPATH=/opt/bench

WORKDIR /workspace
USER sandbox
