FROM python:3.11-slim

# Update CA certificates so HTTPS connections work reliably inside the container.
# python:3.11-slim ships a ca-certificates snapshot from image build time;
# if stale, TLS handshakes fail with "unable to get local issuer certificate".
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install all packages needed inside the sandbox.
# - pandas/numpy/scipy/statsmodels: data analysis & statistical tests
# - matplotlib/seaborn: chart generation and statistical visualization
# - scikit-learn/arch: broader research workflow support for S-series
# - tabulate: required by pandas .to_markdown() used in format_table
# - requests: HTTP API calls (agent-written scripts fetch data from public APIs)
# - websocket-client: WebSocket connections (e.g. Finnhub streaming in D11)
# - ddgs: DuckDuckGo web search for search_web tool
RUN pip install --no-cache-dir \
    pandas>=2.0.0 \
    numpy>=1.24.0 \
    matplotlib>=3.7.0 \
    seaborn>=0.12.0 \
    scipy>=1.10.0 \
    statsmodels>=0.14.0 \
    scikit-learn>=1.3.0 \
    arch>=6.0.0 \
    tabulate>=0.9.0 \
    requests>=2.28.0 \
    websocket-client>=1.0.0 \
    ddgs>=9.0.0 \
    tzdata>=2024.1

# Create mount-point directories and tool executor directory
RUN mkdir -p /workspace /data /docs /student_code /opt/bench

# Non-root user for security
RUN useradd -m -s /bin/bash sandbox \
    && chown sandbox:sandbox /workspace /opt/bench

# Avoid .pyc permission issues under sandbox user
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/home/sandbox
# tool_executor.py imports tools.py from /opt/bench (injected at runtime via docker cp)
ENV PYTHONPATH=/opt/bench

WORKDIR /workspace
USER sandbox
