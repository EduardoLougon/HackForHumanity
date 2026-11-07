# Multi-stage Dockerfile for San Diego Parking Finder API (Render & Cloud Deployment)
FROM python:3.11-slim AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

COPY pyproject.toml README.md ./
RUN uv pip install --system --no-cache -e .

# Final stage
FROM python:3.11-slim AS runner

WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source and config
COPY config.yaml pyproject.toml ./
COPY src/ ./src/

# Copy prebuilt serving database and metadata
COPY data/serving/ ./data/serving/

EXPOSE 8000

# Bind to Render's $PORT environment variable, defaulting to 8000
CMD uvicorn src.parking.api.main:app --host 0.0.0.0 --port ${PORT:-8000}

