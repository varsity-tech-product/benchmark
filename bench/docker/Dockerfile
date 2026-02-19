FROM python:3.11-slim

# Install all packages needed inside the sandbox.
# - pandas/numpy/scipy/statsmodels: data analysis & statistical tests
# - matplotlib: chart generation via plot_chart tool
# - tabulate: required by pandas .to_markdown() used in format_table
RUN pip install --no-cache-dir \
    pandas>=2.0.0 \
    numpy>=1.24.0 \
    matplotlib>=3.7.0 \
    scipy>=1.10.0 \
    statsmodels>=0.14.0 \
    tabulate>=0.9.0

# Create mount-point directories
RUN mkdir -p /workspace /data /docs /student_code

# Non-root user for security
RUN useradd -m -s /bin/bash sandbox \
    && chown sandbox:sandbox /workspace

# Avoid .pyc permission issues under sandbox user
ENV PYTHONDONTWRITEBYTECODE=1
ENV HOME=/home/sandbox

WORKDIR /workspace
USER sandbox
