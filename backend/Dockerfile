# syntax=docker/dockerfile:1.7
# =============================================================================
# OPTIONAL - NOT USED IN THE CURRENT WORKFLOW AND NOT VERIFIED.
# Docker is not available on the development machine, so local development and
# all tests run against natively installed services instead (PostgreSQL Windows
# service on :5432, Memurai/Redis on :6379). See README.md. This file is kept only
# as a starting point should containers be adopted later; it has never been built.
# =============================================================================
# -----------------------------------------------------------------------------
# Backend image: FastAPI on Python 3.12, dependencies installed with uv.
# Multi-stage: build tooling (uv) never reaches the runtime image.
# -----------------------------------------------------------------------------

FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv

WORKDIR /build
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:${PATH}"

# Unprivileged runtime user with a fixed UID/GID and no login shell.
RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --no-create-home --shell /usr/sbin/nologin app

WORKDIR /app

# Code and dependencies are owned by root and only readable by the app user,
# so a compromised process cannot modify them.
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./

USER 10001:10001
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-server-header"]
