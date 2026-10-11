# syntax=docker/dockerfile:1

# Stage 1: Builder
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

RUN python3 -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Stage 2: Final Runtime
FROM python:3.12-slim AS runtime

# Create dedicated non-root user (appuser, UID 10001)
RUN groupadd -g 10001 appgroup && \
    useradd -u 10001 -g appgroup -s /bin/false -m appuser

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Prepare directories for volumes/mounts with appuser permissions
RUN mkdir -p /app/data /app/logs /app/scripts && \
    chown -R appuser:appgroup /app

# Copy application source code
COPY --chown=appuser:appgroup app.py config.py activity.py ai_client.py conversation.py database.py commands.py safety.py ./
COPY --chown=appuser:appgroup scripts/healthcheck.py ./scripts/healthcheck.py

# Switch to non-root user
USER appuser

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python ./scripts/healthcheck.py || exit 1

# Graceful termination signal
STOPSIGNAL SIGTERM

ENTRYPOINT ["python", "app.py"]
