# ── Backend Dockerfile ────────────────────────────────────────────────────────
# Multi-stage build: keeps the final image lean (~150 MB vs ~900 MB)
# ─────────────────────────────────────────────────────────────────────────────

# ── Stage 1: dependency builder ──────────────────────────────────────────────
FROM python:3.12-slim AS builder

# Suppress .pyc files and enable unbuffered stdout (better Docker logging)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# Install build tools needed to compile C extensions (asyncpg, cryptography, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --prefix=/install --no-cache-dir -r requirements.txt


# ── Stage 2: runtime image ────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy pre-built wheels from builder stage
COPY --from=builder /install /usr/local

# Non-root user for security
RUN useradd --create-home appuser
USER appuser

WORKDIR /app
COPY --chown=appuser:appuser . .

EXPOSE 8000

# workers=1 is intentional — the in-memory metrics cache is process-local.
# Multiple workers would each maintain a private cache, causing redundant
# hypervisor polls and divergent responses. Async concurrency handles load.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
