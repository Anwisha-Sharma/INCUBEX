# RescueMesh Backend - Multi-stage Non-root Dockerfile
# Module E7: Hardened production container setup

FROM python:3.12-slim-bookworm AS builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Astral uv
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /uvx /bin/

# Copy dependency manifests
COPY pyproject.toml uv.lock ./

# Install production dependencies without dev packages
RUN uv sync --frozen --no-dev --no-editable

# Production runtime stage
FROM python:3.12-slim-bookworm AS runner

# Security: Create non-root user and group
RUN groupadd -g 10001 rescuemesh && \
    useradd -u 10001 -g rescuemesh -s /bin/bash -m rescuemesh

WORKDIR /app

# Install curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed virtual environment and uv binary
COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /bin/
COPY --from=builder /app/.venv /app/.venv

# Copy application source code
COPY --chown=rescuemesh:rescuemesh . /app

# Switch to non-root user
USER rescuemesh

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8000

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
